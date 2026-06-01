"""
NIFTY Options Trading Engine — MVLA Model (Momentum-Validated, Loss-Averse)
3-Layer Decision Engine for high-probability NIFTY options trades.

Layers:
  1. Time Filter (mandatory)
  2. Direction Engine (VWAP + Supertrend + EMA trend alignment)
  3. Strength & Momentum (EMA 9/21 crossover + distance momentum + ATR + OI)

Bull market → CE calls  |  Bear market → PE calls
Outputs 3 trade recommendations (ATM, OTM, ITM) per expiry with confidence scoring.
"""

import logging
import math
import time as _time_mod
from datetime import datetime, date, timedelta, time as dtime
from typing import Dict, Any, List, Optional
import pytz

logger = logging.getLogger(__name__)

IST = pytz.timezone('Asia/Kolkata')

NIFTY_LOT_SIZE = 50      # kept for backward compat
STRIKE_INTERVAL = 50     # kept for backward compat

# ── Per-index configuration ───────────────────────────────────────────────────
INDEX_CONFIGS = {
    'NIFTY': {
        'lot_size':         50,
        'strike_interval':  50,
        'yf_ticker':        '^NSEI',
        'nse_symbol':       'NIFTY',
        'dhan_symbol':      'NIFTY',
        'truedata_spot_sym':'NIFTY 50',
        'truedata_chain_sym':'NIFTY',
        'default_spot':     23500,
        'display_name':     'NIFTY 50',
        'dhan_security_id': 13,        # Dhan internal security ID for NIFTY index
        'exchange_segment': 'IDX_I',
    },
    'BANKNIFTY': {
        'lot_size':         15,
        'strike_interval':  100,
        'yf_ticker':        '^NSEBANK',
        'nse_symbol':       'BANKNIFTY',
        'dhan_symbol':      'BANKNIFTY',
        'truedata_spot_sym':'BANK NIFTY',
        'truedata_chain_sym':'BANKNIFTY',
        'default_spot':     50200,
        'display_name':     'Bank Nifty',
        'dhan_security_id': 25,
        'exchange_segment': 'IDX_I',
    },
    'FINNIFTY': {
        'lot_size':         40,
        'strike_interval':  50,
        'yf_ticker':        '^CNXFIN',
        'nse_symbol':       'FINNIFTY',
        'dhan_symbol':      'FINNIFTY',
        'truedata_spot_sym':'NIFTY FIN SERVICE',
        'truedata_chain_sym':'FINNIFTY',
        'default_spot':     21000,
        'display_name':     'Fin Nifty',
        'dhan_security_id': 27,         # Dhan security ID for NIFTY FIN SERVICE index
        'exchange_segment': 'IDX_I',
    },
    'SENSEX': {
        'lot_size':         10,
        'strike_interval':  100,
        'yf_ticker':        '^BSESN',
        'nse_symbol':       'SENSEX',
        'dhan_symbol':      'SENSEX',
        'truedata_spot_sym':'SENSEX',
        'truedata_chain_sym':'SENSEX',
        'default_spot':     77500,
        'display_name':     'SENSEX',
        'dhan_security_id': None,       # BSE index — indicators sourced from scaled NIFTY candles
        'exchange_segment': 'IDX_I',
        'candle_source':    'NIFTY',    # Use NIFTY candles (scaled) for SENSEX indicators
    },
}

# Module-level candle cache — per-index dict {index_key: (df, timestamp)}
_candle_cache: dict = {}
_CANDLE_CACHE_TTL = 30   # ~30 s — always evaluate the live forming 5-min candle.
                          # Previously 300 s caused the engine to miss intra-candle
                          # breakouts (e.g. 2:18 breakout not picked up until 2:23).

# Module-level analysis result cache — per-index dict {index_key: (result, timestamp)}
# Prevents concurrent browser requests from racing against the FNO monitor's Dhan call.
# Only caches live-broker results (never 'estimated'). TTL must be < monitor interval (60s).
_analysis_cache: dict = {}
_ANALYSIS_CACHE_TTL = 58   # Just under the 60s monitor interval — covers the full cycle


class NiftyOptionsEngine:

    def __init__(self, user_id: int = None, index: str = 'NIFTY'):
        index = (index or 'NIFTY').upper()
        if index not in INDEX_CONFIGS:
            raise ValueError(f"Unknown index '{index}'. Valid: {list(INDEX_CONFIGS)}")
        cfg = INDEX_CONFIGS[index]
        self.index                  = index
        self.lot_size               = cfg['lot_size']
        self.strike_interval        = cfg['strike_interval']
        self.yf_ticker              = cfg['yf_ticker']
        self.nse_symbol             = cfg['nse_symbol']
        self.dhan_symbol            = cfg['dhan_symbol']
        self.truedata_spot_sym      = cfg['truedata_spot_sym']
        self.truedata_chain_sym     = cfg['truedata_chain_sym']
        self.default_spot           = cfg['default_spot']
        self.display_name           = cfg['display_name']
        self.dhan_security_id       = cfg.get('dhan_security_id')
        self.exchange_segment       = cfg.get('exchange_segment', 'IDX_I')
        self.candle_source          = cfg.get('candle_source', None)   # e.g. 'NIFTY' for SENSEX
        self.data_source            = self._get_active_data_source()
        self.user_id                = user_id
        self._broker_adapter        = None

    def _get_admin_data_plan(self) -> str:
        try:
            from app import db
            result = db.session.execute(
                db.text("SELECT plan_type FROM data_api_plan WHERE is_active = true LIMIT 1")
            ).fetchone()
            return result[0] if result else 'user_data'
        except Exception:
            return 'user_data'

    def _get_truedata(self) -> tuple:
        try:
            from app import db
            row = db.session.execute(
                db.text("SELECT truedata_api_key, truedata_api_secret FROM data_api_plan WHERE is_active = true AND truedata_api_key IS NOT NULL AND truedata_api_key <> '' LIMIT 1")
            ).fetchone()
            if not row or not row[0]:
                return None, None, None

            api_key = row[0]
            import requests
            headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
            spot_resp = requests.get(
                'https://api.truedata.in/v1/getltp',
                params={'symbol': self.truedata_spot_sym},
                headers=headers, timeout=10
            )
            if spot_resp.status_code != 200:
                logger.warning(f"TrueData spot API returned {spot_resp.status_code}")
                return None, None, None

            spot_data = spot_resp.json()
            spot = float(spot_data.get('ltp', 0) or spot_data.get('data', {}).get('ltp', 0))
            if not spot or spot <= 0:
                logger.warning("TrueData returned no spot price")
                return None, None, None

            chain_resp = requests.get(
                'https://api.truedata.in/v1/optionchain',
                params={'symbol': self.truedata_chain_sym},
                headers=headers, timeout=15
            )
            if chain_resp.status_code == 200:
                chain_data = chain_resp.json()
                records = chain_data.get('data', chain_data.get('optionchain', []))
                if records:
                    normalized = []
                    for rec in records:
                        normalized.append({
                            'strike': rec.get('strike', rec.get('strikePrice', 0)),
                            'call_ltp': rec.get('call_ltp', rec.get('CE', {}).get('ltp', 0)),
                            'call_oi': rec.get('call_oi', rec.get('CE', {}).get('oi', 0)),
                            'call_iv': rec.get('call_iv', rec.get('CE', {}).get('iv', 0)),
                            'call_volume': rec.get('call_volume', rec.get('CE', {}).get('volume', 0)),
                            'put_ltp': rec.get('put_ltp', rec.get('PE', {}).get('ltp', 0)),
                            'put_oi': rec.get('put_oi', rec.get('PE', {}).get('oi', 0)),
                            'put_iv': rec.get('put_iv', rec.get('PE', {}).get('iv', 0)),
                            'put_volume': rec.get('put_volume', rec.get('PE', {}).get('volume', 0)),
                        })
                    from services.option_chain_builder import chain_to_engine_format
                    engine_chain = chain_to_engine_format(normalized, spot)
                    logger.info(f"✅ TrueData API: spot={spot:.2f}, chain_strikes={len(normalized)}")
                    return float(spot), engine_chain, 'TrueData'

            logger.warning("TrueData option chain empty or failed")
            return None, None, None
        except Exception as e:
            logger.error(f"TrueData API error: {e}")
            return None, None, None

    def _get_broker_data(self) -> tuple:
        """Returns (spot, engine_chain, broker_name, expiry_list) or (None, None, None, [])."""
        if not self.user_id:
            return None, None, None, []
        try:
            from services.broker_factory import get_data_broker_for_user
            broker = get_data_broker_for_user(self.user_id)
            if not broker:
                logger.info(f"No data broker configured for user {self.user_id}")
                return None, None, None, []

            if not broker.connect():
                logger.warning(f"Data broker {broker.BROKER_NAME} failed to connect")
                return None, None, None, []

            self._broker_adapter = broker

            # Expiry list — only Dhan supports this directly; others return []
            broker_expiries = []
            if hasattr(broker, 'get_expiry_list'):
                try:
                    broker_expiries = broker.get_expiry_list(self.dhan_symbol) or []
                except Exception as ex:
                    logger.warning(f"get_expiry_list({self.dhan_symbol}) failed: {ex}")

            import time as _time_retry
            for attempt in range(2):
                # For brokers that return a direct option chain (Dhan), we use the
                # first available expiry so that spot+chain are always consistent.
                if broker_expiries:
                    nearest_expiry = broker_expiries[0]
                    chain_raw = broker.get_option_chain(self.dhan_symbol, nearest_expiry)
                    # Spot is embedded in each chain row
                    spot = float(chain_raw[0].get("spot", 0)) if chain_raw else 0.0
                    if not spot or spot <= 0:
                        # Fall back to separate price call
                        spot = broker.get_price(self.dhan_symbol)
                else:
                    spot = broker.get_price(self.dhan_symbol)
                    chain_raw = broker.get_option_chain(self.dhan_symbol) if spot and spot > 0 else []

                if chain_raw and spot and spot > 0:
                    break  # success — no retry needed

                if attempt == 0:
                    # Dhan sometimes returns a null-error when concurrent requests are
                    # made with the same client ID. A brief pause resolves it.
                    logger.warning(
                        f"_get_broker_data({self.index}): Dhan returned empty/no-spot on attempt 1 "
                        f"(spot={spot}, chain_len={len(chain_raw) if chain_raw else 0}) — retrying in 0.5s"
                    )
                    _time_retry.sleep(0.5)

            if not spot or spot <= 0:
                logger.warning(f"Data broker {broker.BROKER_NAME} returned no spot price after retry")
                return None, None, None, []

            if chain_raw:
                from services.option_chain_builder import chain_to_engine_format
                engine_chain = chain_to_engine_format(chain_raw, spot)
                logger.info(
                    f"✅ Broker Data API ({broker.BROKER_NAME}): "
                    f"spot={spot:.2f}, chain_strikes={len(chain_raw)}, expiries={len(broker_expiries)}"
                )
                return float(spot), engine_chain, broker.BROKER_NAME, broker_expiries
            else:
                logger.warning(f"Data broker {broker.BROKER_NAME} returned empty chain after retry")
                return None, None, None, []
        except Exception as e:
            logger.error(f"Broker data API error: {e}")
            return None, None, None, []

    def _get_admin_broker_data(self) -> tuple:
        """
        Iterate admin-managed data brokers (priority 1 → 2) and return the first
        broker that returns a valid (spot, chain). Invisible to end users.
        Returns (spot, engine_chain, broker_name, expiry_list) or (None, None, None, []).
        """
        try:
            from services.broker_factory import get_admin_data_brokers
            admin_brokers = get_admin_data_brokers()
            if not admin_brokers:
                return None, None, None, []

            for priority, broker_type, broker_name, broker in admin_brokers:
                try:
                    if not broker.connect():
                        logger.warning(f"Admin data broker P{priority} ({broker_name}) failed to connect")
                        continue

                    broker_expiries = []
                    if hasattr(broker, 'get_expiry_list'):
                        try:
                            broker_expiries = broker.get_expiry_list(self.dhan_symbol) or []
                        except Exception as ex:
                            logger.warning(f"Admin P{priority} get_expiry_list({self.dhan_symbol}) failed: {ex}")

                    chain_raw = []
                    spot = 0.0
                    if broker_expiries:
                        nearest_expiry = broker_expiries[0]
                        chain_raw = broker.get_option_chain(self.dhan_symbol, nearest_expiry)
                        spot = float(chain_raw[0].get("spot", 0)) if chain_raw else 0.0
                        if not spot or spot <= 0:
                            spot = broker.get_price(self.dhan_symbol)
                    else:
                        spot = broker.get_price(self.dhan_symbol)
                        chain_raw = broker.get_option_chain(self.dhan_symbol) if spot and spot > 0 else []

                    if chain_raw and spot and spot > 0:
                        from services.option_chain_builder import chain_to_engine_format
                        engine_chain = chain_to_engine_format(chain_raw, spot)
                        logger.info(
                            f"✅ Admin Data Broker P{priority} ({broker_name}): "
                            f"spot={spot:.2f}, chain_strikes={len(chain_raw)}, expiries={len(broker_expiries)}"
                        )
                        self._broker_adapter = broker
                        return float(spot), engine_chain, f"Admin/{broker_name}", broker_expiries
                    else:
                        logger.warning(f"Admin P{priority} ({broker_name}) returned empty data — trying next")
                except Exception as e:
                    logger.error(f"Admin data broker P{priority} ({broker_name}) error: {e}")
                    continue
            return None, None, None, []
        except Exception as e:
            logger.error(f"_get_admin_broker_data fatal: {e}")
            return None, None, None, []

    def _get_active_data_source(self) -> str:
        try:
            from app import db
            result = db.session.execute(
                db.text("SELECT source_key FROM data_source_config WHERE is_active = true ORDER BY id LIMIT 1")
            ).fetchone()
            if result:
                return result[0]
        except Exception:
            pass
        return 'nse_python'

    def get_market_indices(self) -> Dict[str, Any]:
        nifty_price = 0.0
        nifty_change = 0.0
        nifty_pct = 0.0
        bn_price = 0.0
        bn_change = 0.0
        bn_pct = 0.0
        finnifty_price = 0.0
        finnifty_change = 0.0
        finnifty_pct = 0.0
        sensex_price = 0.0
        sensex_change = 0.0
        sensex_pct = 0.0
        vix_price = 0.0

        # ── Priority 1: Dhan DataApiBroker — fetch ALL indices in one call ──
        try:
            from services.dhan_service import get_index_quotes
            dhan_data = get_index_quotes(self.user_id)
            if dhan_data.get('NIFTY', {}).get('ltp', 0) > 0:
                d = dhan_data['NIFTY']
                nifty_price  = float(d['ltp'])
                nifty_change = float(d.get('change', 0))
                nifty_pct    = float(d.get('pct_change', 0))
            if dhan_data.get('BANKNIFTY', {}).get('ltp', 0) > 0:
                d = dhan_data['BANKNIFTY']
                bn_price  = float(d['ltp'])
                bn_change = float(d.get('change', 0))
                bn_pct    = float(d.get('pct_change', 0))
            if dhan_data.get('FINNIFTY', {}).get('ltp', 0) > 0:
                d = dhan_data['FINNIFTY']
                finnifty_price  = float(d['ltp'])
                finnifty_change = float(d.get('change', 0))
                finnifty_pct    = float(d.get('pct_change', 0))
            if dhan_data.get('SENSEX', {}).get('ltp', 0) > 0:
                d = dhan_data['SENSEX']
                sensex_price  = float(d['ltp'])
                sensex_change = float(d.get('change', 0))
                sensex_pct    = float(d.get('pct_change', 0))
            if dhan_data.get('INDIA VIX', {}).get('ltp', 0) > 0:
                vix_price = float(dhan_data['INDIA VIX']['ltp'])
            logger.info(f"Dhan market indices: NIFTY={nifty_price}, BankNIFTY={bn_price}, "
                        f"FinNIFTY={finnifty_price}, SENSEX={sensex_price}, VIX={vix_price}")
        except Exception as e:
            logger.warning(f"Dhan market indices error: {e}")

        # ── Priority 2: yfinance (fallback) ─────────────────────────────────
        if not nifty_price or not bn_price or not sensex_price or not vix_price:
            try:
                import yfinance as yf
                if not nifty_price:
                    ni = yf.Ticker("^NSEI").fast_info
                    nifty_price = float(getattr(ni, 'last_price', 0) or 0)
                    prev = float(getattr(ni, 'previous_close', 0) or 0)
                    if nifty_price and prev:
                        nifty_change = round(nifty_price - prev, 2)
                        nifty_pct = round((nifty_price - prev) / prev * 100, 2)
                if not bn_price:
                    bn = yf.Ticker("^NSEBANK").fast_info
                    bn_price = float(getattr(bn, 'last_price', 0) or 0)
                    prev_bn = float(getattr(bn, 'previous_close', 0) or 0)
                    if bn_price and prev_bn:
                        bn_change = round(bn_price - prev_bn, 2)
                        bn_pct = round((bn_price - prev_bn) / prev_bn * 100, 2)
                if not sensex_price:
                    sx = yf.Ticker("^BSESN").fast_info
                    sensex_price = float(getattr(sx, 'last_price', 0) or 0)
                    prev_sx = float(getattr(sx, 'previous_close', 0) or 0)
                    if sensex_price and prev_sx:
                        sensex_change = round(sensex_price - prev_sx, 2)
                        sensex_pct = round((sensex_price - prev_sx) / prev_sx * 100, 2)
                if not vix_price:
                    vix_price = float(getattr(yf.Ticker("^INDIAVIX").fast_info, 'last_price', 0) or 0)
                logger.info(f"yfinance fallback used: NIFTY={nifty_price}")
            except Exception as e:
                logger.warning(f"yfinance fallback error: {e}")

        def _idx(price, change, pct):
            if not price or price <= 0:
                return {'price': None, 'change': None, 'pct': None, 'available': False}
            return {
                'price': round(float(price), 2),
                'change': round(float(change), 2),
                'pct':    round(float(pct), 2),
                'available': True,
            }

        return {
            'nifty':     _idx(nifty_price,    nifty_change,    nifty_pct),
            'sensex':    _idx(sensex_price,   sensex_change,   sensex_pct),
            'banknifty': _idx(bn_price,       bn_change,       bn_pct),
            'finnifty':  _idx(finnifty_price, finnifty_change, finnifty_pct),
            'vix':       _idx(vix_price,      0,               0),
        }

    def _get_nse_option_chain_raw(self) -> dict:
        """NSE direct option chain — disabled (geo-blocked on server).
        Option chain data comes from Dhan via _get_option_chain_data()."""
        return {}

    def _parse_expiry_dates(self, raw: dict) -> List[str]:
        try:
            dates = raw.get('records', {}).get('expiryDates', [])
            if dates:
                return dates
        except Exception:
            pass
        return []

    def _pick_expiries(self, expiry_dates: List[str]) -> dict:
        if not expiry_dates:
            return {'current': None, 'next': None, 'current_label': '', 'next_label': ''}

        today = datetime.now(IST).date()
        parsed = []
        for d in expiry_dates:
            try:
                dt = None
                for fmt in ['%d-%b-%Y', '%d-%B-%Y', '%Y-%m-%d', '%d/%m/%Y']:
                    try:
                        dt = datetime.strptime(d, fmt).date()
                        break
                    except ValueError:
                        continue
                if dt and dt >= today:
                    parsed.append((dt, d))
            except Exception:
                continue

        parsed.sort(key=lambda x: x[0])

        if not parsed:
            return {'current': None, 'next': None, 'current_label': '', 'next_label': ''}

        current_dt, current_raw = parsed[0]
        days_to_current = (current_dt - today).days

        current_label = self._expiry_label(current_dt, today)

        next_raw = None
        next_label = ''
        if len(parsed) > 1:
            next_dt, next_raw = parsed[1]
            next_label = self._expiry_label(next_dt, today)

        return {
            'current': current_raw,
            'next': next_raw,
            'current_label': current_label,
            'next_label': next_label,
            'current_date': current_dt.strftime('%d %b %Y'),
            'next_date': parsed[1][0].strftime('%d %b %Y') if len(parsed) > 1 else '',
            'current_dte': days_to_current,
            'next_dte': (parsed[1][0] - today).days if len(parsed) > 1 else 0,
        }

    def _expiry_label(self, exp_date: date, today: date) -> str:
        days = (exp_date - today).days
        if days <= 7:
            return 'Weekly'
        elif days <= 14:
            return 'Next Week'
        else:
            return 'Monthly'

    def _build_chain_for_expiry(self, raw: dict, expiry_str: str, spot: float) -> dict:
        chain = {}
        try:
            data = raw.get('records', {}).get('data', []) or raw.get('filtered', {}).get('data', [])
            for entry in data:
                if entry.get('expiryDate') != expiry_str:
                    continue
                strike_raw = entry.get('strikePrice', 0)
                strike = int(strike_raw) if strike_raw == int(strike_raw) else strike_raw
                ce = entry.get('CE', {})
                pe = entry.get('PE', {})
                if ce:
                    chain[f'{strike}CE'] = {
                        'strike': strike,
                        'type': 'CE',
                        'ltp': ce.get('lastPrice', 0),
                        'oi': ce.get('openInterest', 0),
                        'oi_change': ce.get('changeinOpenInterest', 0),
                        'volume': ce.get('totalTradedVolume', 0),
                        'iv': ce.get('impliedVolatility', 0),
                        'bid': ce.get('bidprice', 0),
                        'ask': ce.get('askPrice', 0),
                        'bid_qty': ce.get('bidQty', 0),
                        'ask_qty': ce.get('askQty', 0),
                        'change': ce.get('change', 0),
                        'pct_change': ce.get('pchangeinOpenInterest', 0),
                        'prev_oi': ce.get('previousDayOI', 0) if 'previousDayOI' in ce else ce.get('openInterest', 0) - ce.get('changeinOpenInterest', 0),
                    }
                if pe:
                    chain[f'{strike}PE'] = {
                        'strike': strike,
                        'type': 'PE',
                        'ltp': pe.get('lastPrice', 0),
                        'oi': pe.get('openInterest', 0),
                        'oi_change': pe.get('changeinOpenInterest', 0),
                        'volume': pe.get('totalTradedVolume', 0),
                        'iv': pe.get('impliedVolatility', 0),
                        'bid': pe.get('bidprice', 0),
                        'ask': pe.get('askPrice', 0),
                        'bid_qty': pe.get('bidQty', 0),
                        'ask_qty': pe.get('askQty', 0),
                        'change': pe.get('change', 0),
                        'pct_change': pe.get('pchangeinOpenInterest', 0),
                        'prev_oi': pe.get('previousDayOI', 0) if 'previousDayOI' in pe else pe.get('openInterest', 0) - pe.get('changeinOpenInterest', 0),
                    }
        except Exception as e:
            logger.warning(f"Chain parse error for {expiry_str}: {e}")

        if not chain:
            logger.info(f"No chain data for expiry {expiry_str}, using sample")
            return self._get_sample_chain(spot)
        logger.info(f"Chain for {expiry_str}: {len(chain)} strikes, keys sample: {list(chain.keys())[:6]}")
        return chain

    def _get_option_chain_data(self) -> Dict[str, Any]:
        try:
            from services.options_service import OptionsService
            svc = OptionsService()
            data = svc.get_option_chain(self.nse_symbol)
            if data and data.get('option_chain'):
                return data
        except Exception as e:
            logger.warning(f"Option chain fetch error: {e}")
        return self._get_sample_option_chain()

    def _get_sample_chain(self, spot: float) -> dict:
        si = self.strike_interval
        atm = round(spot / si) * si
        chain = {}
        # Per-index annualised IV estimates (typical market conditions)
        _iv_map = {'NIFTY': 13.0, 'BANKNIFTY': 20.0, 'FINNIFTY': 17.0, 'SENSEX': 13.0}
        base_iv = _iv_map.get(self.index, 15.0)
        # Estimate DTE: find days to next weekly expiry
        # BANKNIFTY expires Wednesday, NIFTY/FINNIFTY Thursday, SENSEX Friday
        _expiry_weekday = {'NIFTY': 3, 'BANKNIFTY': 2, 'FINNIFTY': 3, 'SENSEX': 4}
        try:
            from datetime import date as _date
            today = _date.today()
            target_wd = _expiry_weekday.get(self.index, 3)
            days_ahead = (target_wd - today.weekday()) % 7
            days_to_expiry = max(1, days_ahead if days_ahead > 0 else 7)
        except Exception:
            days_to_expiry = 5
        time_val = (base_iv / 100) * spot * (days_to_expiry / 365) ** 0.5 * 0.4

        for i in range(-6, 7):
            strike = atm + i * si
            diff = spot - strike
            ce_intrinsic = max(0, diff)
            pe_intrinsic = max(0, -diff)
            dist_factor = max(0.15, 1.0 - abs(i) * 0.12)
            ce_ltp = round(ce_intrinsic + time_val * dist_factor, 2)
            pe_ltp = round(pe_intrinsic + time_val * dist_factor, 2)
            ce_ltp = max(2.0, ce_ltp)
            pe_ltp = max(2.0, pe_ltp)

            ce_oi = max(500000, 5000000 - abs(i) * 600000 + (300000 if i < 0 else -200000))
            pe_oi = max(500000, 5000000 - abs(i) * 600000 + (300000 if i > 0 else -200000))
            ce_vol = int(ce_oi * 0.4)
            pe_vol = int(pe_oi * 0.4)
            iv = round(base_iv + abs(i) * 0.6, 1)
            chain[f'{strike}CE'] = {
                'strike': strike, 'type': 'CE', 'ltp': ce_ltp,
                'oi': ce_oi, 'oi_change': int(ce_oi * 0.03), 'volume': ce_vol,
                'iv': iv,
                'bid': round(ce_ltp - 1.0, 2), 'ask': round(ce_ltp + 1.0, 2),
                'bid_qty': 1500, 'ask_qty': 1500,
                'change': round(ce_ltp * 0.02, 2), 'pct_change': 2.0,
                'prev_oi': int(ce_oi * 0.97),
            }
            chain[f'{strike}PE'] = {
                'strike': strike, 'type': 'PE', 'ltp': pe_ltp,
                'oi': pe_oi, 'oi_change': int(pe_oi * 0.03), 'volume': pe_vol,
                'iv': iv,
                'bid': round(pe_ltp - 1.0, 2), 'ask': round(pe_ltp + 1.0, 2),
                'bid_qty': 1500, 'ask_qty': 1500,
                'change': round(pe_ltp * 0.02, 2), 'pct_change': 2.0,
                'prev_oi': int(pe_oi * 0.97),
            }
        return chain

    def _get_sample_option_chain(self) -> Dict[str, Any]:
        spot = float(self.default_spot)
        try:
            import yfinance as yf
            hist = yf.Ticker(self.yf_ticker).history(period="1d")
            if not hist.empty:
                spot = float(hist['Close'].iloc[-1])
        except Exception:
            pass
        chain = self._get_sample_chain(spot)
        return {
            'spot_price': spot,
            'previous_close': spot - 30,
            'lot_size': self.lot_size,
            'strike_interval': self.strike_interval,
            'option_chain': chain,
            'expiry_dates': [],
        }

    def _compute_oi_differential(self, chain: dict, atm: int) -> float:
        total_put_oi = 0
        total_call_oi = 0
        for i in range(-6, 7):
            strike = atm + i * self.strike_interval
            ce_key = f'{strike}CE'
            pe_key = f'{strike}PE'
            if ce_key in chain:
                total_call_oi += chain[ce_key].get('oi', 0)
            if pe_key in chain:
                total_put_oi += chain[pe_key].get('oi', 0)
        if total_call_oi == 0:
            return 0
        return (total_put_oi - total_call_oi) / total_call_oi

    def _compute_oi_metrics(self, chain: dict, atm: int, spot: float) -> dict:
        """Comprehensive OI analysis: Max Pain, ATM PCR, top support/resistance strikes."""
        strikes = sorted(set(
            int(k[:-2]) for k in chain
            if (k.endswith('CE') or k.endswith('PE'))
            and k[:-2].lstrip('-').isdigit()
        ))
        if not strikes:
            return {'oi_diff': 0, 'oi_signal': 'NEUTRAL', 'pcr': 0, 'atm_pcr': 0,
                    'total_call_oi': 0, 'total_put_oi': 0, 'max_pain': atm,
                    'max_pain_distance': 0, 'top_ce_strikes': [], 'top_pe_strikes': []}

        # ── Totals ──────────────────────────────────────────────────────
        total_call_oi = sum(chain.get(f'{s}CE', {}).get('oi', 0) for s in strikes)
        total_put_oi  = sum(chain.get(f'{s}PE', {}).get('oi', 0) for s in strikes)
        pcr_overall   = round(total_put_oi / total_call_oi, 2) if total_call_oi else 0

        # ── ATM PCR (±3 strikes around ATM) ─────────────────────────────
        atm_range = [s for s in strikes if abs(s - atm) <= 3 * self.strike_interval]
        atm_call  = sum(chain.get(f'{s}CE', {}).get('oi', 0) for s in atm_range)
        atm_put   = sum(chain.get(f'{s}PE', {}).get('oi', 0) for s in atm_range)
        atm_pcr   = round(atm_put / atm_call, 2) if atm_call else 0

        # ── OI Differential (overall) ────────────────────────────────────
        oi_diff   = (total_put_oi - total_call_oi) / total_call_oi if total_call_oi else 0

        # ── Windowed OI Differential (±6 strikes from ATM) ───────────────
        # New trade-selection rule: bullish if window diff > +20%, bearish if < -20%.
        window_strikes = [s for s in strikes if abs(s - atm) <= 6 * self.strike_interval]
        win_call_oi = sum(chain.get(f'{s}CE', {}).get('oi', 0) for s in window_strikes)
        win_put_oi  = sum(chain.get(f'{s}PE', {}).get('oi', 0) for s in window_strikes)
        oi_window_diff = (win_put_oi - win_call_oi) / win_call_oi if win_call_oi else 0

        oi_signal = 'BULLISH' if oi_window_diff > 0.20 else ('BEARISH' if oi_window_diff < -0.20 else 'NEUTRAL')

        # ── Max Pain ─────────────────────────────────────────────────────
        # Strike where total loss for option buyers (CE+PE combined) is maximum
        pain = {}
        for test_s in strikes:
            ce_pain = sum(max(0, test_s - s) * chain.get(f'{s}CE', {}).get('oi', 0) for s in strikes)
            pe_pain = sum(max(0, s - test_s) * chain.get(f'{s}PE', {}).get('oi', 0) for s in strikes)
            pain[test_s] = ce_pain + pe_pain
        max_pain_strike = min(pain, key=pain.get) if pain else atm
        max_pain_dist   = round((max_pain_strike - spot) / spot * 100, 2) if spot else 0

        # ── Top OI Strikes (resistance = high CE OI, support = high PE OI) ──
        ce_oi = [(s, chain.get(f'{s}CE', {}).get('oi', 0)) for s in strikes]
        pe_oi = [(s, chain.get(f'{s}PE', {}).get('oi', 0)) for s in strikes]
        top_ce = [{'strike': s, 'oi': o} for s, o in sorted(ce_oi, key=lambda x: x[1], reverse=True)[:5] if o > 0]
        top_pe = [{'strike': s, 'oi': o} for s, o in sorted(pe_oi, key=lambda x: x[1], reverse=True)[:5] if o > 0]

        logger.info(
            f"OI Metrics — PCR:{pcr_overall} ATM_PCR:{atm_pcr} MaxPain:{max_pain_strike} "
            f"({max_pain_dist:+.2f}%) OI_diff:{oi_diff:.3f} "
            f"CE_OI:{total_call_oi:,} PUT_OI:{total_put_oi:,}"
        )
        return {
            'oi_diff':           round(oi_diff, 4),
            'oi_window_diff':    round(oi_window_diff, 4),
            'window_call_oi':    win_call_oi,
            'window_put_oi':     win_put_oi,
            'oi_signal':         oi_signal,
            'pcr':               pcr_overall,
            'atm_pcr':           atm_pcr,
            'total_call_oi':     total_call_oi,
            'total_put_oi':      total_put_oi,
            'max_pain':          max_pain_strike,
            'max_pain_distance': max_pain_dist,
            'top_ce_strikes':    top_ce,
            'top_pe_strikes':    top_pe,
        }

    def _time_filter(self) -> Dict[str, Any]:
        now = datetime.now(IST)
        current_time = now.time()
        if now.weekday() >= 5:
            return {'pass': False, 'reason': 'Weekend — market closed', 'status': 'blocked', 'caution': False, 'caution_weight': 0}
        # No trades before 9:30 AM
        if current_time < dtime(9, 30):
            return {'pass': False, 'reason': 'No trades before 9:30 AM — opening noise window', 'status': 'blocked', 'caution': False, 'caution_weight': 0}
        # No trades after 2:59 PM
        if current_time >= dtime(15, 0):
            return {'pass': False, 'reason': 'No trades after 2:59 PM — market closing', 'status': 'blocked', 'caution': False, 'caution_weight': 0}
        # Mid-session caution: 12:00–12:30 → index-aware penalty
        # NIFTY genuinely chops at lunch (-10). BANKNIFTY frequently active (-5).
        # FINNIFTY/SENSEX track NIFTY behaviour (-10).
        caution = dtime(12, 0) <= current_time <= dtime(12, 30)
        caution_weight = 0
        if caution:
            caution_weight = 5 if self.index == 'BANKNIFTY' else 10
        return {
            'pass': True,
            'reason': 'Mid-session caution — lunch hour, confidence reduced' if caution else 'Trading window active',
            'status': 'caution' if caution else 'active',
            'caution': caution,
            'caution_weight': caution_weight,
        }

    # ------------------------------------------------------------------
    # Real indicator calculations — intraday 5-min candles
    # Priority 1: Dhan  |  Priority 2: yfinance (fallback)
    # ------------------------------------------------------------------

    def _fetch_intraday_candles(self):
        """
        Fetch today's 5-min candles for this index.
        Tries Dhan first (if Dhan security ID is known), falls back to yfinance.
        Result is cached per-index at module level for _CANDLE_CACHE_TTL seconds.
        """
        global _candle_cache
        now_ts = _time_mod.time()
        cached = _candle_cache.get(self.index)
        if cached is not None:
            df_c, ts_c = cached
            if (now_ts - ts_c) < _CANDLE_CACHE_TTL and df_c is not None:
                return df_c

        df = None

        # ── SENSEX: use scaled NIFTY candles (per user directive) ──────────────────
        # SENSEX is a BSE index with limited intraday data availability.
        # NIFTY candles are fetched instead and scaled to SENSEX price level so that
        # VWAP, SuperTrend, and ADX indicators remain meaningful for SENSEX analysis.
        if self.candle_source and self.candle_source in INDEX_CONFIGS:
            src_cfg = INDEX_CONFIGS[self.candle_source]
            try:
                from services.dhan_service import get_index_intraday_candles
                src_id = src_cfg.get('dhan_security_id')
                if src_id is not None:
                    df_src = get_index_intraday_candles(
                        security_id=src_id,
                        exchange_segment=src_cfg.get('exchange_segment', 'IDX_I'),
                        interval=5, user_id=self.user_id,
                        index_label=self.candle_source,
                    )
                    if df_src is not None and not df_src.empty and len(df_src) >= 5:
                        df = df_src
                        logger.info(f"_fetch_intraday_candles({self.index}): {len(df)} candles from Dhan/{self.candle_source}")
            except Exception as e:
                logger.warning(f"Dhan candles error for {self.index} (source={self.candle_source}): {e}")

            if df is None or df.empty:
                try:
                    import yfinance as yf
                    df_src = yf.Ticker(src_cfg['yf_ticker']).history(period="5d", interval="5m")
                    if df_src is not None and not df_src.empty and len(df_src) >= 5:
                        df = df_src
                        logger.info(f"_fetch_intraday_candles({self.index}): {len(df)} candles from yfinance/{self.candle_source} (5d)")
                except Exception as e:
                    logger.warning(f"yfinance candles error for {self.index} (source={self.candle_source}): {e}")

            # Scale source candles to this index's price level so VWAP is meaningful.
            # Use the latest close of the source candles as the reference price,
            # so the scale factor reflects the actual current price ratio.
            if df is not None and not df.empty:
                src_last_close = float(df['Close'].iloc[-1])
                if src_last_close > 0:
                    scale = float(self.default_spot) / src_last_close
                    df = df.copy()
                    for col in ['Open', 'High', 'Low', 'Close']:
                        if col in df.columns:
                            df[col] = df[col] * scale
                    logger.info(f"_fetch_intraday_candles({self.index}): scaled {self.candle_source} candles by {scale:.2f}x")

        else:
            # ── Priority 1: Dhan intraday candles (confirmed security IDs) ────────
            if self.dhan_security_id is not None:
                try:
                    from services.dhan_service import get_index_intraday_candles
                    df_dhan = get_index_intraday_candles(
                        security_id=self.dhan_security_id,
                        exchange_segment=self.exchange_segment,
                        interval=5, user_id=self.user_id,
                        index_label=self.index,
                    )
                    if df_dhan is not None and not df_dhan.empty and len(df_dhan) >= 5:
                        df = df_dhan
                        logger.info(f"_fetch_intraday_candles({self.index}): {len(df)} candles from Dhan")
                except Exception as e:
                    logger.warning(f"Dhan intraday candles error ({self.index}): {e}")

            # ── Priority 2: yfinance fallback ─────────────────────────────────────
            # Fetch 5 days to provide historical warm-up for EWM-based indicators
            # (RSI, ADX, SuperTrend). VWAP is filtered to today's session only.
            if df is None or df.empty:
                try:
                    import yfinance as yf
                    ticker = yf.Ticker(self.yf_ticker)
                    df_yf = ticker.history(period="5d", interval="5m")
                    if df_yf is not None and not df_yf.empty and len(df_yf) >= 5:
                        df = df_yf
                        logger.info(f"_fetch_intraday_candles({self.index}): {len(df)} candles from yfinance (5d)")
                    else:
                        logger.warning(f"_fetch_intraday_candles({self.index}): yfinance also returned no data")
                except Exception as e:
                    logger.warning(f"yfinance intraday candles error ({self.index}): {e}")

        result = df if (df is not None and not df.empty) else None
        _candle_cache[self.index] = (result, now_ts)
        return result

    def _calculate_vwap(self, df) -> float:
        """VWAP of the last 3 candles (≤15 min micro-VWAP).
        Fallback: close price of latest candle.
        """
        try:
            import pandas as pd
            df = df.tail(3)
            df_today = None

            # ── Primary: filter by datetime index (Dhan with parsed timestamps) ──
            try:
                idx = df.index
                if hasattr(idx, 'tz') and idx.tz is not None:
                    today_date = pd.Timestamp.now(tz=idx.tz).normalize()
                    _today = df[df.index >= today_date]
                    if len(_today) >= 3:
                        df_today = _today
                elif hasattr(idx, 'dtype') and str(idx.dtype).startswith('datetime'):
                    # tz-naive datetime index (yfinance without tz)
                    today_date = pd.Timestamp.now().normalize()
                    _today = df[df.index >= today_date]
                    if len(_today) >= 3:
                        df_today = _today
            except Exception:
                pass

            # ── Fallback: use the last 75 rows (≈ one full trading session) ──
            # Handles: integer-indexed df, failed timestamp parsing, holiday edge cases
            if df_today is None or len(df_today) < 3:
                n = min(75, len(df))
                df_today = df.iloc[-n:]

            tp = (df_today['High'] + df_today['Low'] + df_today['Close']) / 3
            vol = df_today['Volume'].replace(0, 1)
            vwap = (tp * vol).cumsum() / vol.cumsum()
            return float(vwap.iloc[-1])
        except Exception:
            return float(df['Close'].iloc[-1])

    def _calculate_adx(self, df, period: int = 14, di_period: int = 14):
        """Wilder's ADX, +DI, -DI. Returns (adx, dmi_plus, dmi_minus, adx_rising).

        Uses proper Wilder sum-based initialisation: seed = sum of first min(n, k)
        bars, then recursive smoothing.  When fewer than `di_period` candles are
        available the partial sum is used as the seed — the DM+/TR ratio is
        preserved because both are seeded from the same number of bars.
        This matches TradingView / Zerodha Kite DI values without needing
        multi-day historical warm-up.
        """
        try:
            import pandas as pd, numpy as np

            h = df['High'].astype(float)
            l = df['Low'].astype(float)
            c = df['Close'].astype(float)

            if len(c) < 2:
                return 0.0, 0.0, 0.0, False

            tr   = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()],
                             axis=1).max(axis=1)
            up   = h.diff()
            down = -l.diff()
            dm_p = up.where((up > 0) & (up > down), 0.0)
            dm_m = down.where((down > 0) & (down > up), 0.0)

            # Fill NaN at row-0 (caused by shift/diff) with 0
            tr   = tr.fillna(0.0)
            dm_p = dm_p.fillna(0.0)
            dm_m = dm_m.fillna(0.0)

            def wilder_smooth(series: pd.Series, n: int) -> pd.Series:
                """Wilder smoothing with partial-sum seed for k < n.
                Seed = sum(first min(n,k) values); then Wilder recursive formula.
                Both TR and DM series use the same seed length, so the
                DI ratio (DM/TR) is correct regardless of candle count.
                """
                vals = series.values.astype(float)
                k = len(vals)
                out = np.full(k, np.nan)
                fw = min(n, k)              # first-window length
                out[fw - 1] = float(np.nansum(vals[:fw]))
                for i in range(fw, k):
                    out[i] = out[i - 1] - out[i - 1] / n + vals[i]
                return pd.Series(out, index=series.index)

            atr_w = wilder_smooth(tr,   di_period)
            dmp_w = wilder_smooth(dm_p, di_period)
            dmm_w = wilder_smooth(dm_m, di_period)

            safe_atr = atr_w.replace(0, 0.01)
            di_p = (100 * dmp_w / safe_atr).clip(0, 100)
            di_m = (100 * dmm_w / safe_atr).clip(0, 100)
            dx   = (100 * (di_p - di_m).abs() / (di_p + di_m).replace(0, 0.01)).clip(0, 100)
            adx_s = wilder_smooth(dx.fillna(0.0), period)

            valid_adx = adx_s.dropna()
            valid_dip = di_p.dropna()
            valid_dim = di_m.dropna()

            if valid_adx.empty or valid_dip.empty or valid_dim.empty:
                return 0.0, 0.0, 0.0, False

            adx   = min(float(valid_adx.iloc[-1]), 100.0)
            dmi_p = min(float(valid_dip.iloc[-1]), 100.0)
            dmi_m = min(float(valid_dim.iloc[-1]), 100.0)
            rising = bool(len(valid_adx) >= 3 and valid_adx.iloc[-1] > valid_adx.iloc[-3])
            return round(adx, 1), round(dmi_p, 1), round(dmi_m, 1), rising
        except Exception as e:
            logger.warning(f"ADX calculation error: {e}")
            return 0.0, 0.0, 0.0, False

    def _calculate_atr(self, df, period: int = 14):
        """EMA-based ATR. Returns (atr, atr_rising).
        Uses full candle history — EMA-14 needs the full series to converge.
        Only the latest value is reported; earlier truncation to 3 candles
        was producing meaningless ATR readings.
        """
        try:
            import pandas as pd
            h = df['High']
            l = df['Low']
            c = df['Close']
            tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
            atr_s = tr.ewm(span=period, adjust=False).mean()
            atr = float(atr_s.iloc[-1])
            rising = bool(atr_s.iloc[-1] > atr_s.iloc[-3]) if len(atr_s) >= 3 else False
            return round(atr, 2), rising
        except Exception as e:
            logger.warning(f"ATR calculation error: {e}")
            return 100.0, False

    def _calculate_supertrend(self, df, atr_period: int = 10, multiplier: float = 3.0) -> str:
        """Proper Supertrend with locked final bands and trend flips.

        Returns 'BUY' if final trend = up, 'SELL' otherwise.
        """
        try:
            import pandas as pd
            # Full history required — Wilder ATR-10 + trend lock-in flips depend
            # on every prior bar. Only the latest trend state is returned.
            h = df['High'].astype(float)
            l = df['Low'].astype(float)
            c = df['Close'].astype(float)

            tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
            # Wilder ATR (alpha = 1/period)
            atr = tr.ewm(alpha=1.0 / atr_period, adjust=False).mean()

            hl2 = (h + l) / 2.0
            upper_basic = (hl2 + multiplier * atr).values
            lower_basic = (hl2 - multiplier * atr).values
            close_arr = c.values
            n = len(close_arr)
            if n < 2:
                return 'BUY'

            final_upper = [0.0] * n
            final_lower = [0.0] * n
            trend = [1] * n  # 1 = up (BUY), -1 = down (SELL)

            final_upper[0] = upper_basic[0]
            final_lower[0] = lower_basic[0]

            for i in range(1, n):
                # Lock bands once price crosses them
                if upper_basic[i] < final_upper[i - 1] or close_arr[i - 1] > final_upper[i - 1]:
                    final_upper[i] = upper_basic[i]
                else:
                    final_upper[i] = final_upper[i - 1]

                if lower_basic[i] > final_lower[i - 1] or close_arr[i - 1] < final_lower[i - 1]:
                    final_lower[i] = lower_basic[i]
                else:
                    final_lower[i] = final_lower[i - 1]

                # Trend flips
                if trend[i - 1] == 1 and close_arr[i] < final_lower[i]:
                    trend[i] = -1
                elif trend[i - 1] == -1 and close_arr[i] > final_upper[i]:
                    trend[i] = 1
                else:
                    trend[i] = trend[i - 1]

            return 'BUY' if trend[-1] == 1 else 'SELL'
        except Exception as e:
            logger.warning(f"Supertrend calculation error: {e}")
            return 'BUY'

    # ------------------------------------------------------------------
    # New: RSI(7), Market Regime Filter, Entry Trigger, OI classifier
    # ------------------------------------------------------------------

    def _calculate_rsi(self, df, period: int = 7):
        """Wilder RSI on Close. Returns (rsi, rsi_expanding_up, rsi_expanding_down).
        EWM with alpha=1/period works from the first candle — no warm-up needed.
        """
        try:
            import pandas as pd
            # Full history required — Wilder RSI-7 needs ≥7 prior closes for
            # the EWM alpha=1/7 average to converge. Tail(3) earlier was
            # collapsing it to a 2-bar delta, producing garbage RSI values.
            c = df['Close'].astype(float)
            if len(c) < 2:
                return 50.0, False, False
            delta = c.diff().dropna()
            gain = delta.clip(lower=0)
            loss = (-delta).clip(lower=0)
            avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
            avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
            rs = avg_gain / avg_loss.replace(0, 1e-9)
            rsi_s = (100 - 100 / (1 + rs)).clip(0, 100)
            rsi = round(float(rsi_s.iloc[-1]), 1)
            expanding_up   = bool(len(rsi_s) >= 3 and rsi_s.iloc[-1] > rsi_s.iloc[-2] > rsi_s.iloc[-3])
            expanding_down = bool(len(rsi_s) >= 3 and rsi_s.iloc[-1] < rsi_s.iloc[-2] < rsi_s.iloc[-3])
            return rsi, expanding_up, expanding_down
        except Exception as e:
            logger.warning(f"RSI calculation error: {e}")
            return 50.0, False, False

    def _calculate_ema_momentum(self, df) -> dict:
        """EMA 9 / EMA 21 crossover and distance-momentum indicator.

        Replaces ADX (Layer 3) for trend-strength classification.

        Logic:
          - Fresh crossover in last 3 candles  = momentum starting point
          - Distance increasing on latest candle = momentum confirmation
          - distance_pct = |EMA9 - EMA21| / close * 100

        Tiers (relaxed for Indian indices which trend with tight EMA spreads):
          NO_TRADE_ZONE  : gap < 0.03% — flat market, no edge
          WEAK_TREND     : gap 0.03–0.08%, no fresh crossover/momentum
          TRADABLE_TREND : gap 0.03–0.08% WITH fresh crossover OR distance increasing
          STRONG_TREND   : gap > 0.08% and distance increasing
        """
        try:
            # Full candle history required — EMA-9 and especially EMA-21 need
            # at least 21 bars to be meaningful. tail(3) earlier was collapsing
            # both EMAs to ≈close[-1], making the 9/21 gap and crossover
            # detection effectively useless.
            c = df['Close'].astype(float)
            if len(c) < 2:
                return {
                    'ema9': 0.0, 'ema21': 0.0, 'trend': 'NEUTRAL',
                    'crossover': False, 'distance_pct': 0.0,
                    'distance_increasing': False, 'tier': 'WEAK_TREND', 'no_trade_zone': True,
                }

            # EWM uses the full series — current live value reflects true momentum
            ema9_s  = c.ewm(span=9,  adjust=False).mean()
            ema21_s = c.ewm(span=21, adjust=False).mean()

            ema9  = float(ema9_s.iloc[-1])
            ema21 = float(ema21_s.iloc[-1])

            trend = 'BULLISH' if ema9 > ema21 else ('BEARISH' if ema9 < ema21 else 'NEUTRAL')

            crossover = False
            look_back = min(4, len(ema9_s) - 1)
            for i in range(1, look_back + 1):
                prev_diff = float(ema9_s.iloc[-i - 1]) - float(ema21_s.iloc[-i - 1])
                curr_diff = float(ema9_s.iloc[-i])     - float(ema21_s.iloc[-i])
                if (prev_diff <= 0 < curr_diff) or (prev_diff >= 0 > curr_diff):
                    crossover = True
                    break

            price_ref        = float(c.iloc[-1]) or 1.0
            curr_dist        = abs(ema9 - ema21)
            prev_dist        = abs(float(ema9_s.iloc[-2]) - float(ema21_s.iloc[-2]))
            distance_pct     = curr_dist / price_ref * 100.0
            distance_increasing = curr_dist > prev_dist

            if distance_pct < 0.03:
                tier          = 'NO_TRADE_ZONE'
                no_trade_zone = True
            elif distance_pct < 0.08:
                # TRADABLE if EITHER fresh crossover OR distance still expanding
                # (relaxed from AND, since both rarely co-occur on the same candle)
                active        = crossover or distance_increasing
                tier          = 'TRADABLE_TREND' if active else 'WEAK_TREND'
                no_trade_zone = not active
            else:
                tier          = 'STRONG_TREND'
                no_trade_zone = False

            return {
                'ema9':               round(ema9, 2),
                'ema21':              round(ema21, 2),
                'trend':              trend,
                'crossover':          crossover,
                'distance_pct':       round(distance_pct, 3),
                'distance_increasing': distance_increasing,
                'tier':               tier,
                'no_trade_zone':      no_trade_zone,
            }
        except Exception as e:
            logger.warning(f"EMA momentum calculation error: {e}")
            return {
                'ema9': 0.0, 'ema21': 0.0, 'trend': 'NEUTRAL',
                'crossover': False, 'distance_pct': 0.0,
                'distance_increasing': False, 'tier': 'WEAK_TREND', 'no_trade_zone': True,
            }

    def _market_regime_filter(self, df, spot: float, vwap: float, ema_data: dict = None) -> Dict[str, Any]:
        """Intelligent chop guard — blocks ONLY when ≥2 independent chop signals fire.

        Single-blocker mode was over-filtering. New logic:
          Track 3 chop indicators:
            1. EMA 9/21 gap  < 0.03%
            2. |spot − VWAP| / spot * 100 < 0.08
            3. Range compression: current candle range < 0.6 × avg(last 5)
          Trade is blocked only if 2 or more of these are simultaneously true.

        A 4th indicator (overlapping candles) is reported as a soft warning but
        never blocks on its own.

        Volatility expansion is also detected and surfaced so confidence can
        reward breakout days: `vol_expansion = cur_range > 1.2 × avg_range_5`.
        """
        chop_flags = []
        soft_warnings = []
        vol_expansion = False
        try:
            ema_gap = (ema_data or {}).get('distance_pct', 0.10)
            if ema_gap < 0.03:
                chop_flags.append("EMA 9/21 gap below 0.03% (flat trend)")

            # NOTE: VWAP-proximity is NO LONGER a chop blocker — it lives only as
            # a confidence penalty in _confidence_score. Trending days legitimately
            # spend long stretches away from VWAP and we don't want to gate them out.
            vwap_dist_pct = (abs(spot - vwap) / spot * 100.0) if (spot and vwap) else 0

            if df is not None and len(df) >= 6:
                highs = df['High'].astype(float).values
                lows  = df['Low'].astype(float).values
                opens = df['Open'].astype(float).values
                closes = df['Close'].astype(float).values

                ranges = highs - lows
                avg_range_5 = float(sum(ranges[-6:-1]) / 5) if len(ranges) >= 6 else 0.0
                cur_range  = float(ranges[-1])
                if avg_range_5 > 0:
                    if cur_range < 0.6 * avg_range_5:
                        chop_flags.append("Range compression — current candle smaller than recent")
                    if cur_range > 1.2 * avg_range_5:
                        vol_expansion = True

                # Overlapping candles → soft warning only (never blocks alone)
                if len(closes) >= 4:
                    bodies = [abs(closes[i] - opens[i]) for i in range(len(closes))]
                    avg_body = sum(bodies[-10:]) / max(1, len(bodies[-10:]))

                    def _overlap_pct(i_prev, i_cur):
                        a_lo, a_hi = lows[i_prev], highs[i_prev]
                        b_lo, b_hi = lows[i_cur], highs[i_cur]
                        inter = max(0.0, min(a_hi, b_hi) - max(a_lo, b_lo))
                        rng = max(a_hi - a_lo, 1e-9)
                        return inter / rng

                    last3_overlap = all(
                        _overlap_pct(-i - 1, -i) > 0.5 for i in range(1, 4)
                    )
                    last3_small = all(bodies[-i] < avg_body for i in range(1, 4))
                    if last3_overlap and last3_small:
                        soft_warnings.append("Overlapping candles — sideways tape")
        except Exception as e:
            logger.warning(f"Market regime filter error: {e}")

        # Block only when 2+ chop conditions co-occur
        blocked = len(chop_flags) >= 2
        reasons = []
        if blocked:
            reasons.append("Chop regime — " + " + ".join(chop_flags))

        return {
            'pass': not blocked,
            'reasons': reasons,
            'chop_flags': chop_flags,
            'soft_warnings': soft_warnings,
            'vol_expansion': vol_expansion,
            'vwap_distance_pct': round(abs(spot - vwap) / spot * 100.0, 3) if (spot and vwap) else 0,
        }

    def _classify_oi(self, oi_diff: float, direction: str) -> Dict[str, Any]:
        """OI as advisory only — never blocks a trade.

        Returns role: STRONG_SUPPORT | MILD_SUPPORT | NEUTRAL | OPPOSE
        `block` is always False — OI data is lagging + noisy intraday and
        should never be a hard gate. Opposing OI applies a confidence
        penalty (-10) instead.
        """
        role = 'NEUTRAL'
        if direction == 'BULLISH':
            if oi_diff >= 0.20:
                role = 'STRONG_SUPPORT'
            elif oi_diff >= 0.05:
                role = 'MILD_SUPPORT'
            elif oi_diff <= -0.10:
                role = 'OPPOSE'
        elif direction == 'BEARISH':
            if oi_diff <= -0.20:
                role = 'STRONG_SUPPORT'
            elif oi_diff <= -0.05:
                role = 'MILD_SUPPORT'
            elif oi_diff >= 0.10:
                role = 'OPPOSE'
        return {'role': role, 'block': False, 'oppose': role == 'OPPOSE'}

    def _entry_trigger_validation(self, df, direction: str, vwap: float,
                                  rsi: float = 50.0, ema_widening: bool = False,
                                  ema9: float = 0.0, ema_trend: str = 'NEUTRAL') -> Dict[str, Any]:
        """Validate breakout / retest / momentum / pullback entry trigger.

        Modes:
          BREAKOUT          — current high > previous high (bull) / low < previous low (bear).
                              Close-confirmation optional → +5 bonus.
          RETEST            — VWAP retest holds.
          MOMENTUM          — RSI extended (>60 bull / <40 bear) AND EMA gap widening.
          PULLBACK_IN_TREND — In a confirmed EMA trend, price pulls back to/through EMA9,
                              RSI exits the extreme, then last close resumes in trend
                              direction. Catches continuation entries on trending days.
        """
        if direction == 'NEUTRAL' or df is None or len(df) < 3:
            return {'triggered': False, 'mode': 'NONE', 'close_confirmed': False,
                    'reason': 'Direction aligned, waiting for breakout trigger'}
        try:
            h = df['High'].astype(float).values
            l = df['Low'].astype(float).values
            c = df['Close'].astype(float).values

            cur_h, prev_h = float(h[-1]), float(h[-2])
            cur_l, prev_l = float(l[-1]), float(l[-2])
            cur_c, prev_c = float(c[-1]), float(c[-2])

            # Pullback helper — only fires when EMA trend agrees with direction.
            # Bull pullback: any of last 3 lows touched/pierced EMA9, prior RSI was
            # cooling (<55), and last close is making higher closes again.
            def _bull_pullback() -> bool:
                if ema_trend != 'BULLISH' or ema9 <= 0 or len(c) < 4:
                    return False
                touched = any(float(l[-i]) <= ema9 * 1.0010 for i in (2, 3, 4))
                rsi_reset = rsi < 65 and rsi > 45
                resumed = cur_c > prev_c and cur_c > ema9
                return touched and rsi_reset and resumed

            def _bear_pullback() -> bool:
                if ema_trend != 'BEARISH' or ema9 <= 0 or len(c) < 4:
                    return False
                touched = any(float(h[-i]) >= ema9 * 0.9990 for i in (2, 3, 4))
                rsi_reset = rsi > 35 and rsi < 55
                resumed = cur_c < prev_c and cur_c < ema9
                return touched and rsi_reset and resumed

            if direction in ('BULLISH', 'BOTH'):
                breakout = cur_h > prev_h
                close_confirmed = breakout and cur_c > prev_h
                retest = vwap > 0 and cur_l <= vwap * 1.0008 and cur_c > vwap
                # Per spec: RSI > 58 (was > 60) + EMA gap widening
                momentum_entry = rsi > 58 and ema_widening
                if breakout:
                    reason = 'Bullish breakout — current high > previous high'
                    if close_confirmed:
                        reason += ' (close confirmed)'
                    return {'triggered': True, 'mode': 'BREAKOUT',
                            'close_confirmed': close_confirmed, 'reason': reason}
                if retest:
                    return {'triggered': True, 'mode': 'RETEST',
                            'close_confirmed': True, 'reason': 'Bullish retest of VWAP held'}
                if momentum_entry:
                    return {'triggered': True, 'mode': 'MOMENTUM',
                            'close_confirmed': False,
                            'reason': f'Momentum entry — RSI {rsi:.0f} + EMA widening'}
                if _bull_pullback():
                    return {'triggered': True, 'mode': 'PULLBACK_IN_TREND',
                            'close_confirmed': True,
                            'reason': f'Bullish pullback to EMA9 held — RSI reset to {rsi:.0f}, trend resuming'}
                if direction == 'BULLISH':
                    return {'triggered': False, 'mode': 'WAIT', 'close_confirmed': False,
                            'reason': 'Direction aligned, waiting for breakout / pullback / momentum trigger'}

            if direction in ('BEARISH', 'BOTH'):
                breakout = cur_l < prev_l
                close_confirmed = breakout and cur_c < prev_l
                retest = vwap > 0 and cur_h >= vwap * 0.9992 and cur_c < vwap
                # Per spec: RSI < 42 (was < 40) + EMA gap widening
                momentum_entry = rsi < 42 and ema_widening
                if breakout:
                    reason = 'Bearish breakdown — current low < previous low'
                    if close_confirmed:
                        reason += ' (close confirmed)'
                    return {'triggered': True, 'mode': 'BREAKOUT',
                            'close_confirmed': close_confirmed, 'reason': reason}
                if retest:
                    return {'triggered': True, 'mode': 'RETEST',
                            'close_confirmed': True, 'reason': 'Bearish rejection at VWAP held'}
                if momentum_entry:
                    return {'triggered': True, 'mode': 'MOMENTUM',
                            'close_confirmed': False,
                            'reason': f'Momentum entry — RSI {rsi:.0f} + EMA widening'}
                if _bear_pullback():
                    return {'triggered': True, 'mode': 'PULLBACK_IN_TREND',
                            'close_confirmed': True,
                            'reason': f'Bearish pullback to EMA9 rejected — RSI reset to {rsi:.0f}, trend resuming'}
                return {'triggered': False, 'mode': 'WAIT', 'close_confirmed': False,
                        'reason': 'Direction aligned, waiting for breakout / pullback / momentum trigger'}
        except Exception as e:
            logger.warning(f"Entry trigger validation error: {e}")
        return {'triggered': False, 'mode': 'NONE', 'close_confirmed': False,
                'reason': 'Trigger evaluation unavailable'}

    def _direction_engine(self, spot: float, oi_window_diff: float = 0.0,
                          adx: float = 22.0, ema_data: dict = None) -> Dict[str, Any]:
        """Layer 2 — Direction Engine.

        Uses EMA 9/21 trend alignment (from Layer 3) to replace the old
        'adx >= 20' gate. Supertrend, VWAP, and RSI(7) remain as confirming
        filters for both CE (bull) and PE (bear) calls.
        """
        df = self._fetch_intraday_candles()
        # Volume vs 20-period average — replaces OI as 6th direction vote
        # (OI moves to advisory-only). Volume above 20-bar avg = participation
        # confirms the move; below = thin/uncommitted move.
        vol_above_avg = False
        try:
            if df is not None and 'Volume' in df.columns and len(df) >= 5:
                vols = df['Volume'].astype(float).values
                lookback = min(20, max(5, len(vols) - 1))
                avg_vol = float(sum(vols[-(lookback + 1):-1]) / lookback) if lookback > 0 else 0.0
                cur_vol = float(vols[-1])
                if avg_vol > 0:
                    vol_above_avg = cur_vol >= avg_vol  # at-or-above the 20-bar avg
        except Exception:
            vol_above_avg = False

        if df is not None and len(df) >= 10:
            vwap = self._calculate_vwap(df)
            supertrend_signal = self._calculate_supertrend(df)
            _, dmi_plus, dmi_minus, _ = self._calculate_adx(df, period=14, di_period=14)
            rsi, rsi_up, rsi_down = self._calculate_rsi(df, period=7)
        else:
            vwap = spot
            supertrend_signal = 'BUY'
            dmi_plus = 22.0
            dmi_minus = 22.0
            rsi, rsi_up, rsi_down = 50.0, False, False

        # EMA momentum from Layer 3
        ema = ema_data or {}
        ema_trend        = ema.get('trend', 'NEUTRAL')
        ema_crossover    = ema.get('crossover', False)
        ema_dist_rising  = ema.get('distance_increasing', False)
        # EMA momentum active = EMA trend present AND (fresh crossover OR distance growing)
        ema_bull_active  = ema_trend == 'BULLISH' and (ema_crossover or ema_dist_rising)
        ema_bear_active  = ema_trend == 'BEARISH' and (ema_crossover or ema_dist_rising)

        # OI is advisory only — feeds confidence (+/-) but no longer votes on direction.
        bull_oi = self._classify_oi(oi_window_diff, 'BULLISH')
        bear_oi = self._classify_oi(oi_window_diff, 'BEARISH')

        # SOFT scoring — per MVLA + HalfTrend spec, direction fires when
        # ≥3 of 5 sub-conditions agree. Supertrend is REMOVED from voting
        # (it lags + duplicates ATR trend logic that HalfTrend now owns);
        # it remains as a +5 confidence bonus when aligned (see _confidence_score).
        # EMA trend acts as both a scoring point AND a hard guard against contradictions.
        bull_checks = [
            ema_trend == 'BULLISH',                           # 1. EMA trend bullish
            ema_crossover or ema_dist_rising,                 # 2. EMA momentum
            spot > vwap,                                      # 3. VWAP alignment
            rsi > 52,                                         # 4. RSI > 52 (relaxed)
            vol_above_avg,                                    # 5. Volume confirms participation
        ]
        bear_checks = [
            ema_trend == 'BEARISH',
            ema_crossover or ema_dist_rising,
            spot < vwap,
            rsi < 48,
            vol_above_avg,
        ]
        bull_score = sum(bull_checks)
        bear_score = sum(bear_checks)

        # ≥3 of 5 + EMA trend cannot be the opposite (avoid contradictions)
        bullish = bull_score >= 3 and ema_trend != 'BEARISH'
        bearish = bear_score >= 3 and ema_trend != 'BULLISH'
        # Both CE and PE can be active simultaneously
        if bullish and bearish:
            direction = 'BOTH'
        elif bullish:
            direction = 'BULLISH'
        elif bearish:
            direction = 'BEARISH'
        else:
            direction = 'NEUTRAL'

        oi_role = 'NEUTRAL'
        oi_block_dir = False
        if direction in ('BULLISH', 'BOTH'):
            oi_role = bull_oi['role']
        if direction in ('BEARISH', 'BOTH'):
            # For BOTH, show bearish OI only if different from bull OI
            bear_role = bear_oi['role']
            oi_role = f"{oi_role}/{bear_role}" if direction == 'BOTH' and bear_role != oi_role else (bear_role if direction == 'BEARISH' else oi_role)
        if direction == 'NEUTRAL':
            if bull_oi['block'] or bear_oi['block']:
                oi_block_dir = True

        return {
            'direction': direction,
            'bull_active': bullish,
            'bear_active': bearish,
            'vwap': round(vwap, 2),
            'spot_vs_vwap': 'ABOVE' if spot > vwap else 'BELOW',
            'supertrend': supertrend_signal,
            'dmi_plus': round(dmi_plus, 1),
            'dmi_minus': round(dmi_minus, 1),
            'rsi': rsi,
            'rsi_expanding_up': rsi_up,
            'rsi_expanding_down': rsi_down,
            'volume_above_avg': vol_above_avg,
            'oi_role': oi_role,
            'oi_blocks_candidate': oi_block_dir,
            'indicators_aligned': bullish or bearish,
            # 3-of-5 soft scoring exposed for UI / debugging
            'bull_score': bull_score,
            'bear_score': bear_score,
            'score_max': 5,
            # EMA momentum used for direction decision
            'ema_trend': ema_trend,
            'ema_crossover': ema_crossover,
            'ema_dist_rising': ema_dist_rising,
        }

    def _strength_engine(self, spot: float) -> Dict[str, Any]:
        """Layer 3 — Strength & Momentum.

        Primary gate: EMA 9/21 crossover + distance momentum.
        ADX still calculated but only for informational display; it no longer
        gates trade entry.
        """
        df = self._fetch_intraday_candles()

        # ── EMA 9/21 momentum (primary gate) ──────────────────────────────────
        if df is not None and len(df) >= 22:
            ema = self._calculate_ema_momentum(df)
        else:
            ema = {
                'ema9': 0.0, 'ema21': 0.0, 'trend': 'NEUTRAL',
                'crossover': False, 'distance_pct': 0.0,
                'distance_increasing': False, 'tier': 'WEAK_TREND', 'no_trade_zone': True,
            }

        # ── ADX (informational only — not used to gate trades) ─────────────────
        if df is not None and len(df) >= 10:
            adx, _, _, adx_rising = self._calculate_adx(df, period=14, di_period=14)
            atr, atr_rising = self._calculate_atr(df)
        else:
            adx, adx_rising = 22.0, False
            atr, atr_rising = 100.0, False

        # Map EMA tier to readable strength label
        tier = ema['tier']
        no_trade_zone = ema['no_trade_zone']
        if tier == 'STRONG_TREND':
            strength_label = 'STRONG'
            caution = False
        elif tier == 'TRADABLE_TREND':
            strength_label = 'MODERATE'
            caution = False
        elif tier == 'WEAK_TREND':
            strength_label = 'WEAK'
            caution = True
        else:
            strength_label = 'WEAK'
            caution = False

        return {
            # EMA momentum fields (primary)
            'ema9':               ema['ema9'],
            'ema21':              ema['ema21'],
            'ema_trend':          ema['trend'],
            'ema_crossover':      ema['crossover'],
            'ema_distance_pct':   ema['distance_pct'],
            'ema_distance_increasing': ema['distance_increasing'],
            # Tier / gate (driven by EMA)
            'tier':               tier,
            'no_trade_zone':      no_trade_zone,
            'strength':           strength_label,
            'caution':            caution,
            # ADX kept for display / logging only
            'adx':                round(adx, 1),
            'adx_rising':         adx_rising,
            'adx_falling':        False,
            'atr':                round(atr, 2),
            'atr_rising':         atr_rising,
            # Convenience alias for callers that still reference 'ema'
            'ema':                ema,
        }

    def _confidence_score(self, direction: dict, strength: dict, oi_diff: float,
                          trigger: Optional[dict] = None, time_check: Optional[dict] = None,
                          spot: float = 0.0, regime: Optional[dict] = None,
                          halftrend: Optional[dict] = None) -> int:
        score = 0
        # Direction agreement
        if direction['spot_vs_vwap'] in ['ABOVE', 'BELOW'] and direction['direction'] != 'NEUTRAL':
            score += 20
        if direction['indicators_aligned']:
            score += 15
        if direction['dmi_plus'] != direction['dmi_minus']:
            score += 10

        # EMA momentum strength — per MVLA spec thresholds (0.08 / 0.12)
        ema_dist = strength.get('ema_distance_pct', 0.0)
        if ema_dist >= 0.12:
            score += 20
        elif ema_dist >= 0.08:
            score += 10
        elif ema_dist >= 0.03:
            score += 5

        # Supertrend bonus — moved out of direction voting; only a confidence
        # tailwind when it agrees with the chosen direction.
        st_signal = direction.get('supertrend')
        _dir_now = direction.get('direction')
        if (_dir_now in ('BULLISH', 'BOTH') and st_signal == 'BUY') or \
           (_dir_now in ('BEARISH', 'BOTH') and st_signal == 'SELL'):
            score += 5

        # HalfTrend stability layer — per spec: only manages lifecycle, but
        # a stable, aligned HalfTrend is a strong tailwind for entry too.
        if halftrend and halftrend.get('available'):
            ht_trend = halftrend.get('trend')
            ht_aligned = (
                (_dir_now in ('BULLISH', 'BOTH') and ht_trend == 'BULLISH') or
                (_dir_now in ('BEARISH', 'BOTH') and ht_trend == 'BEARISH')
            )
            if ht_aligned:
                score += 5
                if halftrend.get('stability_score', 0) >= 50:
                    score += 10  # strong, persistent HalfTrend

        # EMA crossover bonus — fresh crossover is a strong momentum signal
        if strength.get('ema_crossover'):
            score += 10

        # Distance increasing — momentum is building
        if strength.get('ema_distance_increasing'):
            score += 10

        # RSI alignment with direction (+10 if expanding in trade direction)
        _dir = direction['direction']
        _rsi = direction.get('rsi', 50)
        if _dir in ('BULLISH', 'BOTH') and _rsi > 52:
            score += 5
            if direction.get('rsi_expanding_up'):
                score += 5
        if _dir in ('BEARISH', 'BOTH') and _rsi < 48:
            score += 5
            if direction.get('rsi_expanding_down'):
                score += 5

        # OI as advisory layer — supports add, opposing penalises (no longer blocks)
        oi_role = direction.get('oi_role', 'NEUTRAL')
        if 'STRONG_SUPPORT' in oi_role:
            score += 10
        elif 'MILD_SUPPORT' in oi_role:
            score += 5
        elif 'OPPOSE' in oi_role:
            score -= 10

        # Trigger confirmation bonus + close-confirmation bonus (was mandatory before)
        if trigger and trigger.get('triggered'):
            score += 15
            if trigger.get('close_confirmed'):
                score += 5  # close-confirmed breakout is a stronger trigger

        # Volatility expansion bonus — captures breakout days (important for options)
        if regime and regime.get('vol_expansion'):
            score += 10

        # VWAP-extension penalty: if spot too far from VWAP, entry is late
        try:
            vwap = direction.get('vwap', 0) or 0
            if spot and vwap:
                vwap_dist_pct = abs(spot - vwap) / spot * 100.0
                if vwap_dist_pct > 0.6:
                    score -= 10
        except Exception:
            pass

        # Lunch-hour caution — index-aware weight (NIFTY/SENSEX/FINNIFTY: -10, BANKNIFTY: -5)
        if time_check and time_check.get('caution'):
            score -= int(time_check.get('caution_weight', 10))

        return max(0, min(score, 100))

    def _select_strikes(self, spot: float, direction: str) -> List[Dict[str, Any]]:
        si = self.strike_interval
        atm = int(round(spot / si) * si)
        trades = []
        opt_type = 'CE' if direction == 'BULLISH' else 'PE'
        if direction == 'NEUTRAL':
            opt_type = 'CE'

        trades.append({
            'strike': atm,
            'type': opt_type,
            'label': f'ATM {opt_type}',
            'moneyness': 'ATM',
            'risk': 'Medium',
            'reward': 'Good',
            'suggested_for': 'Default pick — balanced risk/reward',
        })

        if opt_type == 'CE':
            otm_strike = atm + si
            itm_strike = atm - si
        else:
            otm_strike = atm - si
            itm_strike = atm + si

        trades.append({
            'strike': otm_strike,
            'type': opt_type,
            'label': f'OTM {opt_type}',
            'moneyness': 'OTM',
            'risk': 'High',
            'reward': 'High',
            'suggested_for': 'Aggressive — lower premium, higher reward',
        })
        trades.append({
            'strike': itm_strike,
            'type': opt_type,
            'label': f'ITM {opt_type}',
            'moneyness': 'ITM',
            'risk': 'Low',
            'reward': 'Moderate',
            'suggested_for': 'Conservative — higher delta, lower risk',
        })

        return trades

    def _generate_trade_reasons(self, trade: dict, direction: dict, strength: dict, oi_signal: str,
                                 trigger: Optional[dict] = None) -> List[str]:
        reasons = []
        m = trade.get('moneyness', '')
        if direction['indicators_aligned']:
            reasons.append(f"Confluence aligned ({direction['direction']})")
        # EMA momentum reasons
        ema_dist = strength.get('ema_distance_pct', 0.0)
        ema_tier = strength.get('tier', '')
        if ema_tier == 'STRONG_TREND':
            reasons.append(f"Strong EMA momentum — 9/21 gap {ema_dist:.2f}%")
        elif ema_tier == 'TRADABLE_TREND':
            reasons.append(f"EMA 9/21 crossover confirmed — momentum building ({ema_dist:.2f}%)")
        if strength.get('ema_crossover'):
            reasons.append("Fresh EMA 9/21 crossover — trend just started")
        if strength.get('ema_distance_increasing'):
            reasons.append("EMA gap widening — trend gaining strength")
        # RSI — use trade type (CE/PE) for per-trade reason when direction is BOTH
        rsi = direction.get('rsi', 50)
        _trade_type = trade.get('type', '')
        _eff_dir = direction['direction']
        if _eff_dir == 'BOTH':
            _eff_dir = 'BULLISH' if _trade_type == 'CE' else 'BEARISH'
        if _eff_dir == 'BULLISH' and rsi > 55:
            reasons.append(f"RSI(7) bullish ({rsi})")
        elif _eff_dir == 'BEARISH' and rsi < 45:
            reasons.append(f"RSI(7) bearish ({rsi})")
        # OI confirmation
        oi_role = direction.get('oi_role', 'NEUTRAL')
        if oi_role == 'STRONG_SUPPORT':
            reasons.append("OI strongly supports direction")
        elif oi_role == 'MILD_SUPPORT':
            reasons.append("OI mildly supports direction")
        # Trigger
        if trigger and trigger.get('triggered'):
            reasons.append(trigger.get('reason', 'Entry trigger confirmed'))
        # Moneyness blurb
        if m == 'ATM':
            reasons.append("Best delta exposure — highest probability of profit")
        elif m == 'OTM':
            reasons.append("Lower premium cost — higher leverage if move extends")
        elif m == 'ITM':
            reasons.append("Higher intrinsic value — safer with more delta")
        return reasons[:5]

    def _enrich_trades(self, trades: List[Dict], chain: dict, confidence: int, entry_mode: str, expiry_info: dict = None) -> List[Dict]:
        enriched = []
        # Liquidity / spread filters (per new rules)
        MIN_OPTION_VOLUME = 1000
        MAX_SPREAD_PCT = 3.0
        OVERHEATED_PCT_CHANGE = 25.0  # block if option premium already moved >25% on last tick

        # Resolve admin-configured SL/Target rule ONCE per enrichment cycle
        # (avoids N DB hits inside the loop).
        try:
            from services.fno_config import compute_sl_target_points as _compute_sl_tgt
        except Exception as _cfg_err:
            logger.warning(f"fno_config import failed, using legacy defaults: {_cfg_err}")
            def _compute_sl_tgt(ltp_, index=None):
                return max(20, round(ltp_ * 0.10)), max(30, round(ltp_ * 0.15))

        for t in trades:
            strike = int(t['strike']) if isinstance(t['strike'], float) and t['strike'] == int(t['strike']) else t['strike']
            key = f"{strike}{t['type']}"
            opt_data = chain.get(key, {})
            ltp = opt_data.get('ltp', 0)
            if ltp <= 0:
                nearby_keys = [k for k in chain.keys() if k.endswith(t['type'])]
                if nearby_keys:
                    def extract_strike(k):
                        try:
                            return int(k.replace('CE','').replace('PE',''))
                        except ValueError:
                            return 0
                    nearest = min(nearby_keys, key=lambda k: abs(extract_strike(k) - strike))
                    opt_data = chain.get(nearest, {})
                    ltp = opt_data.get('ltp', 0)
            if ltp <= 0:
                logger.warning(f"No option data for {key}, skipping")
                continue

            # ── Liquidity & execution-quality filters ─────────────────
            bid = float(opt_data.get('bid', 0) or 0)
            ask = float(opt_data.get('ask', 0) or 0)
            volume = float(opt_data.get('volume', 0) or 0)
            pct_chg = float(opt_data.get('pct_change', 0) or 0)
            spread_pct = ((ask - bid) / ltp * 100.0) if (bid > 0 and ask > 0 and ltp > 0) else 0.0

            if spread_pct > MAX_SPREAD_PCT:
                logger.info(f"Skipping {key}: spread {spread_pct:.2f}% > {MAX_SPREAD_PCT}%")
                continue
            if volume > 0 and volume < MIN_OPTION_VOLUME:
                logger.info(f"Skipping {key}: volume {volume:.0f} below min {MIN_OPTION_VOLUME}")
                continue
            if abs(pct_chg) > OVERHEATED_PCT_CHANGE:
                logger.info(f"Skipping {key}: premium overheated ({pct_chg:.1f}%)")
                continue

            # SL/Target are admin-configurable absolute points, set PER INDEX
            # via Admin → F&O Settings. _compute_sl_tgt resolved once above this loop.
            try:
                sl_points, target_points, target_2_points, target_3_points = _compute_sl_tgt(ltp, self.index)
            except Exception as _cfg_err:
                logger.warning(f"compute_sl_target_points failed, using legacy defaults: {_cfg_err}")
                sl_points      = max(20, round(ltp * 0.10))
                target_points  = max(30, round(ltp * 0.15))
                target_2_points = max(50, round(ltp * 0.25))
                target_3_points = max(70, round(ltp * 0.35))

            entry_price = ltp
            # Floor SL so it never crosses zero (cap at 0.05 minimum tick).
            # If LTP is too small to absorb the stop, shrink stop to half the premium
            # but only if that still leaves a sensible >= 5pt buffer; otherwise skip.
            MIN_TICK = 0.05
            MIN_SL_POINTS = 5
            if entry_price - sl_points <= MIN_TICK:
                adjusted_sl = max(MIN_SL_POINTS, int(round(entry_price * 0.5)))
                if adjusted_sl < MIN_SL_POINTS or entry_price <= MIN_SL_POINTS + MIN_TICK:
                    logger.info(f"Skipping {key}: premium {entry_price:.2f} too low for valid SL {sl_points}")
                    continue
                sl_points = adjusted_sl
                target_points   = max(target_points,   int(round(1.5 * sl_points)))
                target_2_points = max(target_2_points, int(round(2.5 * sl_points)))
                target_3_points = max(target_3_points, int(round(3.5 * sl_points)))
            target   = round(entry_price + target_points,   2)
            target_2 = round(entry_price + target_2_points, 2)
            target_3 = round(entry_price + target_3_points, 2)
            sl = round(max(MIN_TICK, entry_price - sl_points), 2)

            lot_value = ltp * self.lot_size
            max_loss_per_lot = sl_points * self.lot_size
            max_profit_per_lot = target_points * self.lot_size

            trade_data = {
                **t,
                'action': 'BUY',
                'symbol': f"{self.dhan_symbol} {t['strike']} {t['type']}",
                'ltp': round(ltp, 2),
                'entry_price': round(entry_price, 2),
                'sl': sl,
                'target': target,
                'target_2': target_2,
                'target_3': target_3,
                'sl_points': sl_points,
                'target_points': target_points,
                'target_2_points': target_2_points,
                'target_3_points': target_3_points,
                'oi': opt_data.get('oi', 0),
                'oi_change': opt_data.get('oi_change', 0),
                'volume': opt_data.get('volume', 0),
                'iv': opt_data.get('iv', 0),
                'bid': opt_data.get('bid', 0),
                'ask': opt_data.get('ask', 0),
                'bid_qty': opt_data.get('bid_qty', 0),
                'ask_qty': opt_data.get('ask_qty', 0),
                'change': opt_data.get('change', 0),
                'pct_change': opt_data.get('pct_change', 0),
                'prev_oi': opt_data.get('prev_oi', 0),
                'lot_size': self.lot_size,
                'lot_value': round(lot_value, 2),
                'max_loss_per_lot': round(max_loss_per_lot, 2),
                'max_profit_per_lot': round(max_profit_per_lot, 2),
                'confidence': confidence,
                'entry_mode': entry_mode,
                'risk_reward': f"1:{round(target_points / sl_points, 1)}",
            }

            if expiry_info:
                trade_data['expiry'] = expiry_info.get('date', '')
                trade_data['expiry_label'] = expiry_info.get('label', '')
                trade_data['dte'] = expiry_info.get('dte', 0)

            enriched.append(trade_data)
        return enriched

    def generate_analysis(self) -> Dict[str, Any]:
        global _analysis_cache
        now_ts = _time_mod.time()

        # Return cached live result if it exists and is fresh enough.
        # This prevents concurrent browser requests from racing against the
        # FNO monitor's Dhan API calls (cold-start HTTPS connection contention).
        cached = _analysis_cache.get(self.index)
        if cached is not None:
            cached_result, cached_ts = cached
            age = now_ts - cached_ts
            if age < _ANALYSIS_CACHE_TTL and cached_result.get('data_source', 'estimated') != 'estimated':
                logger.debug(f"generate_analysis({self.index}): returning cached result (age={age:.1f}s, src={cached_result['data_source']})")
                return cached_result

        now = datetime.now(IST)
        time_check = self._time_filter()

        data_source = 'estimated'
        current_chain = None
        next_chain = {}
        spot = None
        broker_expiry_list: List[str] = []

        # Default expiry_picks — overwritten below once real data is available
        expiry_picks: Dict[str, Any] = {
            'current': None, 'next': None,
            'current_label': 'Weekly', 'next_label': 'Monthly',
            'current_date': '', 'next_date': '',
            'current_dte': 0, 'next_dte': 0,
        }

        admin_plan = self._get_admin_data_plan()

        # ── Step 1: Admin-managed broker data sources (primary → secondary) ──
        # Admin-connected Dhan/Zerodha is the authoritative data source for
        # every user. Personal broker data is only consulted if the admin
        # pool is empty or all admin brokers failed to deliver an option chain.
        if not current_chain:
            adm_spot, adm_chain, adm_name, adm_expiry_list = self._get_admin_broker_data()
            if adm_spot and adm_chain:
                data_source = f'broker:{adm_name}'
                spot = adm_spot
                current_chain = adm_chain
                next_chain = {}
                if adm_expiry_list:
                    expiry_picks = self._pick_expiries(adm_expiry_list)
                    broker_expiry_list = adm_expiry_list

        # ── Step 2: User's own Data API broker (fallback only) ───────────────
        if not current_chain and self.user_id:
            broker_spot, broker_chain, broker_name, broker_expiry_list = self._get_broker_data()
            if broker_spot and broker_chain:
                data_source = f'broker:{broker_name}'
                spot = broker_spot
                current_chain = broker_chain
                next_chain = {}
                if broker_expiry_list:
                    expiry_picks = self._pick_expiries(broker_expiry_list)

        # ── Step 3: TrueData (when admin plan selects it) ────────────────────
        # TrueData is part of the always-on admin data tier — try it whenever an
        # API key is configured, regardless of the legacy plan_type flag.
        if not current_chain:
            td_spot, td_chain, td_name = self._get_truedata()
            if td_spot and td_chain:
                data_source = f'broker:{td_name}'
                spot = td_spot
                current_chain = td_chain
                next_chain = {}

        # ── Step 4: NSE Python (final live-data fallback) ────────────────────
        if not current_chain:
            raw_nse = self._get_nse_option_chain_raw()
            expiry_dates = self._parse_expiry_dates(raw_nse)
            expiry_picks = self._pick_expiries(expiry_dates)

            if raw_nse and expiry_picks.get('current'):
                data_source = 'live'
                spot_val = raw_nse.get('records', {}).get('underlyingValue', 0) or raw_nse.get('filtered', {}).get('data', [{}])[0].get('PE', {}).get('underlyingValue', 0) if raw_nse.get('filtered', {}).get('data') else 0
                if not spot_val:
                    spot_val = self.default_spot
                spot = float(spot_val)
                current_chain = self._build_chain_for_expiry(raw_nse, expiry_picks['current'], spot)
                next_chain = self._build_chain_for_expiry(raw_nse, expiry_picks['next'], spot) if expiry_picks.get('next') else {}

        if not current_chain:
            data_source = 'estimated'
            spot = float(self.default_spot)
            # yfinance fallback for spot
            try:
                import yfinance as yf
                hist = yf.Ticker(self.yf_ticker).history(period="1d")
                if not hist.empty:
                    spot = float(hist['Close'].iloc[-1])
            except Exception:
                pass
            logger.warning(f"All data sources unavailable — using estimated data ({self.index}). Spot: {spot}. Trade signals BLOCKED.")
            current_chain = self._get_sample_chain(spot)
            next_chain = {}

        # ── HARD BLOCK: never generate trade signals on estimated option prices ──
        _estimated_data = (data_source == 'estimated')

        atm = int(round(spot / self.strike_interval) * self.strike_interval)

        oi_metrics = self._compute_oi_metrics(current_chain, atm, spot)
        oi_diff        = oi_metrics['oi_diff']
        oi_window_diff = oi_metrics.get('oi_window_diff', 0.0)
        oi_signal      = oi_metrics['oi_signal']
        strength = self._strength_engine(spot)
        # Pass EMA data from Layer 3 into Layer 2 so direction uses EMA trend
        direction = self._direction_engine(
            spot,
            oi_window_diff=oi_window_diff,
            adx=strength.get('adx', 22.0),
            ema_data=strength.get('ema', {}),
        )

        # Market Regime Filter — uses EMA gap for chop detection
        candles_df = self._fetch_intraday_candles()
        regime = self._market_regime_filter(
            candles_df, spot, direction.get('vwap', spot),
            ema_data=strength.get('ema', {}),
        )

        # ── HalfTrend lifecycle layer ─────────────────────────────────
        # Per spec, HalfTrend manages trade lifecycle (persistence + exits)
        # and acts as a confidence tailwind when it aligns with direction.
        try:
            from services.halftrend import compute_state as _ht_compute_state
            halftrend_state = _ht_compute_state(candles_df)
        except Exception as _e:
            logger.warning(f"HalfTrend compute_state failed: {_e}")
            halftrend_state = {
                'available': False, 'trend': 'NEUTRAL',
                'halftrend_level': 0.0, 'stability_score': 0,
                'volatility_expansion': False,
                'buy_exit': False, 'sell_exit': False,
                'trend_persistence': 0,
                'reason': 'HalfTrend module error',
            }

        # Entry Trigger Validation — runs after direction is decided.
        # Now accepts RSI + EMA-widening so the MOMENTUM entry path can fire
        # without waiting for a fresh candle break.
        trigger = self._entry_trigger_validation(
            candles_df,
            direction['direction'],
            direction.get('vwap', 0.0),
            rsi=direction.get('rsi', 50.0),
            ema_widening=strength.get('ema_distance_increasing', False),
            ema9=strength.get('ema9', 0.0),
            ema_trend=strength.get('ema_trend', 'NEUTRAL'),
        )

        confidence = self._confidence_score(direction, strength, oi_window_diff,
                                            halftrend=halftrend_state,
                                            trigger=trigger, time_check=time_check,
                                            spot=spot, regime=regime)

        # Volatility-expansion can bypass the WEAK_TREND no-trade gate
        # (breakout days often start with EMA spread still tight).
        weak_trend_bypass = (
            regime.get('vol_expansion', False)
            and strength.get('tier') == 'WEAK_TREND'
            and direction['indicators_aligned']
        )
        strength_gate_pass = (not strength['no_trade_zone']) or weak_trend_bypass

        entry_mode = 'NO TRADE'
        if (time_check['pass'] and strength_gate_pass
                and regime['pass'] and direction['indicators_aligned'] and trigger['triggered']):
            if strength.get('ema_distance_pct', 0) >= 0.15 and strength.get('ema_crossover'):
                entry_mode = 'CONFIRMED'
            else:
                entry_mode = 'EARLY'

        # HARD BLOCK: if option prices are estimated (no live data), never signal a trade.
        # Estimated premiums can be 30–100% off real market prices, making entry/SL/target useless.
        if _estimated_data:
            entry_mode = 'NO TRADE'

        # HalfTrend lifecycle hand-off — when HalfTrend has flipped against
        # the chosen direction, suppress new entries (existing positions move
        # to the EXIT_MANAGED_BY_HALFTREND state below).
        if (halftrend_state.get('available') and direction['direction'] != 'NEUTRAL'):
            ht_dir_now = halftrend_state.get('trend')
            ht_against = (
                (direction['direction'] in ('BULLISH', 'BOTH') and ht_dir_now == 'BEARISH') or
                (direction['direction'] in ('BEARISH', 'BOTH') and ht_dir_now == 'BULLISH')
            )
            if ht_against and entry_mode != 'NO TRADE':
                entry_mode = 'NO TRADE'
                block_reasons.append(
                    f"HalfTrend flipped {ht_dir_now} — opposes {direction['direction']} entry"
                )

        bull_active    = direction.get('bull_active', False)
        bear_active    = direction.get('bear_active', False)
        trade_direction = direction['direction']

        block_reasons = []
        if _estimated_data:
            block_reasons.append("No live option data — connect a Data API broker or check NSE connectivity")
        if not time_check['pass']:
            block_reasons.append(time_check['reason'])
        # Regime filter reasons (chop / vwap-distance / compression / overlap)
        for r in regime.get('reasons', []):
            block_reasons.append(r)
        # EMA momentum tier
        ema_gap = strength.get('ema_distance_pct', 0.0)
        if strength.get('tier') == 'NO_TRADE_ZONE':
            block_reasons.append(f"Choppy market — EMA 9/21 gap {ema_gap:.3f}% (below 0.03% threshold)")
        elif strength.get('tier') == 'WEAK_TREND' and not weak_trend_bypass:
            block_reasons.append(f"Weak EMA momentum — gap {ema_gap:.3f}%, no fresh crossover or distance still narrowing")
        # RSI gate (advisory only — direction engine already enforces 4-of-6)
        # No longer blocks; flagged informationally if neutral
        # OI as advisory only (never blocks)
        if direction.get('oi_role') and 'OPPOSE' in direction.get('oi_role', '') and direction['direction'] != 'NEUTRAL':
            block_reasons.append("OI advisory: opposing flow detected (confidence reduced, not blocked)")
        # Direction not aligned
        if not direction['indicators_aligned'] and direction['direction'] == 'NEUTRAL':
            block_reasons.append("No clear direction — fewer than 3 of 5 indicators agree")

        # Setup state machine — per MVLA + HalfTrend spec:
        #   NO_TRADE → EARLY_MOMENTUM → SETUP_READY → TRADE_ACTIVE → EXIT_MANAGED_BY_HALFTREND
        # We retain the legacy state names (SETUP_FORMING, SETUP_READY_WAIT_TRIGGER,
        # TRADE_RECOMMENDED) for backward compatibility with any consumers and
        # add the new states alongside.
        ht_aligned_now = (
            halftrend_state.get('available') and (
                (direction['direction'] in ('BULLISH', 'BOTH') and halftrend_state.get('trend') == 'BULLISH') or
                (direction['direction'] in ('BEARISH', 'BOTH') and halftrend_state.get('trend') == 'BEARISH')
            )
        )
        ht_exit_fired = bool(
            halftrend_state.get('available') and (
                (direction['direction'] in ('BULLISH', 'BOTH') and halftrend_state.get('buy_exit')) or
                (direction['direction'] in ('BEARISH', 'BOTH') and halftrend_state.get('sell_exit'))
            )
        )

        setup_state = 'NO_TRADE'
        if not time_check['pass'] or not regime['pass'] or (strength['no_trade_zone'] and not weak_trend_bypass):
            setup_state = 'NO_TRADE'
        elif not direction['indicators_aligned']:
            # EARLY_MOMENTUM = building setup with at least 2/5 votes on the leading side
            leading_votes = max(direction.get('bull_score', 0), direction.get('bear_score', 0))
            setup_state = 'EARLY_MOMENTUM' if leading_votes >= 2 else 'SETUP_FORMING'
        elif direction['indicators_aligned'] and not trigger['triggered']:
            setup_state = 'SETUP_READY'  # spec name; was SETUP_READY_WAIT_TRIGGER
            block_reasons.append(trigger['reason'])
        elif direction['indicators_aligned'] and trigger['triggered']:
            # If a HalfTrend exit has just fired against the direction, hand
            # over to the lifecycle layer instead of recommending a fresh entry.
            if ht_exit_fired:
                setup_state = 'EXIT_MANAGED_BY_HALFTREND'
                block_reasons.append(
                    f"HalfTrend exit fired — close crossed {halftrend_state.get('halftrend_level')}"
                )
            else:
                setup_state = 'TRADE_ACTIVE'  # spec name; was TRADE_RECOMMENDED

        # Unified decision — confidence tiering (per product spec):
        #   ≥75 : Tier 1 — High conviction (Telegram alerts)
        #   60-74: Tier 2 — Regular signals shown in app/UI
        #   50-59: Tier 3 — Aggressive traders (shown with caution badge)
        #   <50 : Blocked
        TIER_3_FLOOR = 50
        if entry_mode != 'NO TRADE' and confidence < TIER_3_FLOOR:
            block_reasons.append(f"Confidence too low ({confidence}/100, need {TIER_3_FLOOR}+)")
        is_blocked = entry_mode == 'NO TRADE' or confidence < TIER_3_FLOOR
        final_decision = 'NO TRADE' if is_blocked else 'TRADE'

        # Confidence tier label for UI
        if confidence >= 75:
            confidence_tier = 'HIGH_CONVICTION'
        elif confidence >= 60:
            confidence_tier = 'REGULAR'
        elif confidence >= TIER_3_FLOOR:
            confidence_tier = 'AGGRESSIVE'
        else:
            confidence_tier = 'BLOCKED'
        if is_blocked and not block_reasons:
            block_reasons.append("Entry conditions not met")

        # Momentum gauge — EMA-based (replaces ADX/40 formula)
        _ema_dist    = strength.get('ema_distance_pct', 0.0)
        _ema_xover   = strength.get('ema_crossover', False)
        _ema_rising  = strength.get('ema_distance_increasing', False)
        momentum_pct = min(100, max(0, int(
            min(40, _ema_dist / 0.20 * 40) +   # up to 40 pts from EMA gap size (rescaled to 0.20%)
            (20 if _ema_xover else 0) +          # 20 pts for fresh crossover
            (20 if _ema_rising else 0) +          # 20 pts for increasing distance
            (20 if direction['indicators_aligned'] else 0)  # 20 pts for full alignment
        )))
        momentum_label = 'STRONG' if momentum_pct >= 70 else ('MODERATE' if momentum_pct >= 40 else 'WEAK')

        current_trades = []
        next_trades = []

        # Surface ONLY the side that matches the current market direction.
        # BULLISH → 3 CE strikes (ATM/OTM/ITM).  BEARISH → 3 PE strikes.
        # BOTH / NEUTRAL (rare — both sides aligned or neither): show both
        # so the trader still sees the full ladder and can pick.
        raw_strikes = []
        if trade_direction in ('BULLISH', 'BOTH', 'NEUTRAL'):
            ce_strikes = self._select_strikes(spot, 'BULLISH')
            for s in ce_strikes:
                s['aligned'] = bull_active or trade_direction in ('BULLISH', 'BOTH')
                s['side']    = 'CALL'
            raw_strikes += ce_strikes
        if trade_direction in ('BEARISH', 'BOTH', 'NEUTRAL'):
            pe_strikes = self._select_strikes(spot, 'BEARISH')
            for s in pe_strikes:
                s['aligned'] = bear_active or trade_direction in ('BEARISH', 'BOTH')
                s['side']    = 'PUT'
            raw_strikes += pe_strikes

        current_expiry_info = {
            'date': expiry_picks.get('current_date', ''),
            'label': expiry_picks.get('current_label', 'Weekly'),
            'dte': expiry_picks.get('current_dte', 0),
        }
        next_expiry_info = {
            'date': expiry_picks.get('next_date', ''),
            'label': expiry_picks.get('next_label', 'Monthly'),
            'dte': expiry_picks.get('next_dte', 0),
        }

        current_trades = self._enrich_trades(raw_strikes, current_chain, confidence, entry_mode, current_expiry_info)
        if next_chain:
            next_trades = self._enrich_trades(raw_strikes, next_chain, confidence, entry_mode, next_expiry_info)

        for t in current_trades:
            t['trade_reasons'] = self._generate_trade_reasons(t, direction, strength, oi_signal, trigger=trigger)
        for t in next_trades:
            t['trade_reasons'] = self._generate_trade_reasons(t, direction, strength, oi_signal, trigger=trigger)

        # Per-trade HalfTrend exit hint — UI/Trade Now can use this to manage
        # an open position. CALL trades watch buy_exit, PUT trades watch sell_exit.
        ht_level = halftrend_state.get('halftrend_level', 0.0)
        ht_avail = halftrend_state.get('available', False)
        for t in current_trades + next_trades:
            side = t.get('side')
            exit_now = bool(
                ht_avail and (
                    (side == 'CALL' and halftrend_state.get('buy_exit')) or
                    (side == 'PUT'  and halftrend_state.get('sell_exit'))
                )
            )
            t['halftrend_exit'] = {
                'available':       ht_avail,
                'level':           ht_level,
                'exit_now':        exit_now,
                'stability_score': halftrend_state.get('stability_score', 0),
                'trail_to':        ht_level if ht_avail else None,
            }

        # OI status uses confirmation classification (not the old hard threshold)
        oi_role = direction.get('oi_role', 'NEUTRAL')
        if oi_role == 'STRONG_SUPPORT':
            oi_status = 'pass'
        elif oi_role == 'MILD_SUPPORT':
            oi_status = 'warn'
        elif oi_role == 'OPPOSE' or direction.get('oi_blocks_candidate'):
            oi_status = 'fail'
        else:
            oi_status = 'warn' if abs(oi_window_diff) > 0.10 else 'pass'  # neutral OI is allowed

        rsi_v = direction.get('rsi', 50) or 50
        # HalfTrend layer status — pass when aligned with direction & stable,
        # fail when an exit has just fired, warn otherwise.
        if not halftrend_state.get('available'):
            ht_layer_status = 'warn'
        elif ht_exit_fired:
            ht_layer_status = 'fail'
        elif ht_aligned_now and halftrend_state.get('stability_score', 0) >= 50:
            ht_layer_status = 'pass'
        elif ht_aligned_now:
            ht_layer_status = 'warn'
        else:
            ht_layer_status = 'fail' if direction['direction'] != 'NEUTRAL' else 'warn'

        layer_status = {
            'time': 'pass' if time_check['pass'] else 'fail',
            'regime': 'pass' if regime['pass'] else 'fail',
            'direction': 'pass' if direction['indicators_aligned'] else ('warn' if direction['direction'] != 'NEUTRAL' else 'fail'),
            'strength': 'pass' if strength.get('tier') in ('STRONG_TREND',) else ('warn' if strength.get('tier') == 'TRADABLE_TREND' else 'fail'),
            'rsi': 'pass' if (rsi_v > 55 or rsi_v < 45) else 'fail',
            'trigger': 'pass' if trigger.get('triggered') else 'fail',
            'oi': oi_status,
            'halftrend': ht_layer_status,
        }

        # Display confidence — must reflect tradability, not just raw signal strength.
        # A "100 / Strong Signal / NO TRADE" combo is misleading: when gates block,
        # the actionable confidence is 0. Keep the raw score as `signal_strength`
        # for transparency and tier classification.
        signal_strength = confidence
        if is_blocked:
            display_confidence = 0
            confidence_grade   = 'Blocked'
        else:
            display_confidence = confidence
            confidence_grade   = ('Strong'    if confidence >= 75
                             else 'Medium'    if confidence >= 60
                             else 'Aggressive' if confidence >= 50
                             else 'Weak')

        result = {
            'timestamp': now.strftime('%Y-%m-%d %H:%M:%S IST'),
            'data_source': data_source,
            'spot_price': spot,
            'atm_strike': atm,
            'time_filter': time_check,
            'direction': direction,
            'strength': strength,
            'oi_analysis': oi_metrics,
            'regime': regime,
            'trigger': trigger,
            'halftrend': halftrend_state,
            'setup_state': setup_state,
            'confidence': display_confidence,
            'signal_strength': signal_strength,
            'confidence_grade': confidence_grade,
            'confidence_tier': confidence_tier,
            'entry_mode': entry_mode,
            'trade_direction': trade_direction,
            'final_decision': final_decision,
            'is_blocked': is_blocked,
            'block_reasons': block_reasons,
            'momentum': {'pct': momentum_pct, 'label': momentum_label},
            'layer_status': layer_status,
            'trades': current_trades,
            'next_expiry_trades': next_trades,
            'expiry_info': {
                'current': expiry_picks.get('current_date', ''),
                'current_label': expiry_picks.get('current_label', ''),
                'current_dte': expiry_picks.get('current_dte', 0),
                'next': expiry_picks.get('next_date', ''),
                'next_label': expiry_picks.get('next_label', ''),
                'next_dte': expiry_picks.get('next_dte', 0),
                'has_next': bool(next_chain and next_trades),
            },
            'risk_rules': {
                'max_trades_per_day': 3,
                'stop_on_consecutive_losses': 2,
                'daily_loss_limit': '3%',
                'risk_per_trade': '1% of capital',
            },
            'configured_source': self.data_source,
            'index':             self.index,
            'lot_size':          self.lot_size,
            'strike_interval':   self.strike_interval,
            'display_name':      self.display_name,
        }

        # Cache live-broker results so concurrent browser requests reuse them
        # instead of firing competing Dhan connections (cold-start race condition).
        if data_source != 'estimated':
            _analysis_cache[self.index] = (result, _time_mod.time())
            logger.debug(f"generate_analysis({self.index}): cached live result (src={data_source})")

        return result

    # ------------------------------------------------------------------
    # Lightweight market direction — no option chain needed
    # Used by Market Intelligence page for the 4-index direction bar
    # ------------------------------------------------------------------

    _direction_cache: dict = {}
    _DIRECTION_CACHE_TTL = 58   # 58 s — just under the 60 s monitor cycle

    def get_market_direction(self) -> dict:
        """Return BULLISH / BEARISH / SIDEWAYS for this index using the same
        EMA 9/21 + Supertrend + VWAP + RSI logic as the F&O engine.

        Uses module-level candle cache so repeated calls are nearly free.
        Returns a compact dict suitable for the Market Intelligence page.
        """
        # Check module-level direction cache first
        now_ts = _time_mod.time()
        cached = NiftyOptionsEngine._direction_cache.get(self.index)
        if cached:
            res_c, ts_c = cached
            if (now_ts - ts_c) < NiftyOptionsEngine._DIRECTION_CACHE_TTL:
                return res_c

        df = self._fetch_intraday_candles()

        _no_data = df is None or len(df) < 10

        if _no_data:
            result = {
                'index':     self.index,
                'label':     self.display_name,
                'direction': 'SIDEWAYS',
                'reason':    'Insufficient candle data',
                'score':     {'bull': 0, 'bear': 0},
                'signals':   {},
                'data_ok':   False,
            }
            NiftyOptionsEngine._direction_cache[self.index] = (result, now_ts)
            return result

        # ── Indicators ────────────────────────────────────────────────
        spot  = float(df['Close'].iloc[-1])
        vwap  = self._calculate_vwap(df)
        st    = self._calculate_supertrend(df)
        rsi, rsi_up, rsi_down = self._calculate_rsi(df, period=7)

        ema = {}
        if len(df) >= 22:
            ema = self._calculate_ema_momentum(df)

        ema_trend      = ema.get('trend', 'NEUTRAL')
        ema_crossover  = ema.get('crossover', False)
        ema_dist_rising = ema.get('distance_increasing', False)
        ema_dist_pct   = ema.get('distance_pct', 0.0)
        ema_tier       = ema.get('tier', 'WEAK_TREND')

        ema_bull = ema_trend == 'BULLISH' and (ema_crossover or ema_dist_rising)
        ema_bear = ema_trend == 'BEARISH' and (ema_crossover or ema_dist_rising)

        # ── Score: max 6 (each signal worth 1–2 pts) ──────────────────
        bull = 0
        bear = 0

        # EMA momentum (2 pts — strongest signal)
        if ema_bull:   bull += 2
        elif ema_bear: bear += 2

        # Supertrend (2 pts)
        if st == 'BUY':  bull += 2
        else:            bear += 2

        # VWAP position (1 pt)
        if spot > vwap:  bull += 1
        else:            bear += 1

        # RSI (1 pt)
        if rsi > 55:     bull += 1
        elif rsi < 45:   bear += 1

        # ── Decision ──────────────────────────────────────────────────
        # Need ≥ 4/6 to call a clear direction (Supertrend + EMA agree)
        if bull >= 4:
            direction = 'BULLISH'
        elif bear >= 4:
            direction = 'BEARISH'
        else:
            direction = 'SIDEWAYS'

        # ── Human-readable reason ──────────────────────────────────────
        if direction == 'BULLISH':
            if ema_crossover:
                reason = f"EMA 9 crossed above EMA 21, gap {ema_dist_pct:.2f}% rising"
            elif ema_bull:
                reason = f"EMA 9 above EMA 21 ({ema_dist_pct:.2f}%), price above VWAP"
            else:
                reason = "Supertrend bullish, price above VWAP"
        elif direction == 'BEARISH':
            if ema_crossover:
                reason = f"EMA 9 crossed below EMA 21, gap {ema_dist_pct:.2f}% widening"
            elif ema_bear:
                reason = f"EMA 9 below EMA 21 ({ema_dist_pct:.2f}%), price below VWAP"
            else:
                reason = "Supertrend bearish, price below VWAP"
        else:
            reason = "EMA 9/21 gap narrow or mixed signals — wait for clarity"

        result = {
            'index':     self.index,
            'label':     self.display_name,
            'direction': direction,
            'reason':    reason,
            'score':     {'bull': bull, 'bear': bear, 'max': 6},
            'signals': {
                'ema_trend':       ema_trend,
                'ema_crossover':   ema_crossover,
                'ema_dist_pct':    round(ema_dist_pct, 3),
                'ema_dist_rising': ema_dist_rising,
                'ema_tier':        ema_tier,
                'supertrend':      st,
                'spot_vs_vwap':    'ABOVE' if spot > vwap else 'BELOW',
                'rsi':             rsi,
                'spot':            round(spot, 2),
                'vwap':            round(vwap, 2),
            },
            'data_ok': True,
        }

        NiftyOptionsEngine._direction_cache[self.index] = (result, now_ts)
        logger.info(
            f"market_direction({self.index}): {direction} "
            f"bull={bull}/bear={bear} EMA={ema_trend} ST={st} RSI={rsi:.0f}"
        )
        return result
