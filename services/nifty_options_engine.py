"""
NIFTY Options Trading Engine — MVLA Model (Momentum-Validated, Loss-Averse)
3-Layer Decision Engine for high-probability NIFTY options trades.

Layers:
  1. Time Filter (mandatory)
  2. Direction Engine (VWAP + Supertrend + DMI)
  3. Strength & Momentum (ADX + ATR + OI confirmation)

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

NIFTY_LOT_SIZE = 50
STRIKE_INTERVAL = 50

# Module-level candle cache — shared across all engine instances (TTL 5 min)
_candle_cache_data = None
_candle_cache_ts = 0.0
_CANDLE_CACHE_TTL = 300


class NiftyOptionsEngine:

    def __init__(self, user_id: int = None):
        self.data_source = self._get_active_data_source()
        self.user_id = user_id
        self._broker_adapter = None

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
                db.text("SELECT truedata_api_key, truedata_api_secret FROM data_api_plan WHERE is_active = true AND plan_type = 'nse_truedata' LIMIT 1")
            ).fetchone()
            if not row or not row[0]:
                return None, None, None

            api_key = row[0]
            import requests
            headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
            spot_resp = requests.get(
                'https://api.truedata.in/v1/getltp',
                params={'symbol': 'NIFTY 50'},
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
                params={'symbol': 'NIFTY'},
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
                    broker_expiries = broker.get_expiry_list("NIFTY") or []
                except Exception:
                    pass

            # For brokers that return a direct option chain (Dhan), we use the
            # first available expiry so that spot+chain are always consistent.
            if broker_expiries:
                nearest_expiry = broker_expiries[0]
                chain_raw = broker.get_option_chain("NIFTY", nearest_expiry)
                # Spot is embedded in each chain row
                spot = float(chain_raw[0].get("spot", 0)) if chain_raw else 0.0
                if not spot or spot <= 0:
                    # Fall back to separate price call
                    spot = broker.get_price("NIFTY")
            else:
                spot = broker.get_price("NIFTY")
                chain_raw = broker.get_option_chain("NIFTY") if spot and spot > 0 else []

            if not spot or spot <= 0:
                logger.warning(f"Data broker {broker.BROKER_NAME} returned no spot price")
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
                logger.warning(f"Data broker {broker.BROKER_NAME} returned empty chain")
                return None, None, None, []
        except Exception as e:
            logger.error(f"Broker data API error: {e}")
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
        sensex_price = 0.0
        vix_price = 0.0

        # ── Priority 1: Dhan DataApiBroker ──
        try:
            from services.dhan_service import get_index_quotes
            dhan_data = get_index_quotes(self.user_id)
            if dhan_data.get('NIFTY', {}).get('ltp', 0) > 0:
                d = dhan_data['NIFTY']
                nifty_price  = float(d['ltp'])
                nifty_change = float(d.get('change', 0))
                nifty_pct    = float(d.get('pct_change', 0))
            if dhan_data.get('BANKNIFTY', {}).get('ltp', 0) > 0:
                bn_price = float(dhan_data['BANKNIFTY']['ltp'])
            logger.info(f"Dhan market indices: NIFTY={nifty_price}, BankNIFTY={bn_price}")
        except Exception as e:
            logger.warning(f"Dhan market indices error: {e}")

        # ── Priority 2: yfinance for any missing values ──
        if not nifty_price or not bn_price or not sensex_price:
            try:
                import yfinance as yf
                if not nifty_price:
                    ni = yf.Ticker("^NSEI").fast_info
                    nifty_price  = float(getattr(ni, 'last_price', 0) or 0)
                    prev = float(getattr(ni, 'previous_close', 0) or 0)
                    if nifty_price and prev:
                        nifty_pct = round((nifty_price - prev) / prev * 100, 2)
                        nifty_change = round(nifty_price - prev, 2)
                if not bn_price:
                    bn_price = float(getattr(yf.Ticker("^NSEBANK").fast_info, 'last_price', 0) or 0)
                if not sensex_price:
                    sensex_price = float(getattr(yf.Ticker("^BSESN").fast_info, 'last_price', 0) or 0)
                if not vix_price:
                    vix_price = float(getattr(yf.Ticker("^INDIAVIX").fast_info, 'last_price', 0) or 0)
            except Exception as e:
                logger.warning(f"yfinance fallback error: {e}")

        return {
            'nifty':       {'price': round(float(nifty_price or 23500), 2),  'change': round(float(nifty_change), 2), 'pct': round(float(nifty_pct), 2)},
            'sensex':      {'price': round(float(sensex_price or 77500), 2), 'change': 0, 'pct': 0},
            'banknifty':   {'price': round(float(bn_price or 50200), 2),     'change': 0, 'pct': 0},
            'vix':         {'price': round(float(vix_price or 13.5), 2),     'change': 0, 'pct': 0},
            'nifty_fut':   {'price': round(float(nifty_price or 23500) + 15, 2), 'change': 0, 'pct': 0},
            'banknifty_fut':{'price': round(float(bn_price or 50200) + 25, 2),   'change': 0, 'pct': 0},
        }

    def _get_nse_option_chain_raw(self) -> dict:
        try:
            from nsepython import option_chain as nse_oc
            raw = nse_oc("NIFTY")
            if raw and isinstance(raw, dict) and raw.get('records', {}).get('data'):
                logger.info(f"NSE option chain via nsepython: {len(raw['records']['data'])} entries")
                return raw
            else:
                logger.warning("nsepython returned empty/invalid data")
        except Exception as e:
            logger.warning(f"nsepython option_chain error: {e}")

        try:
            import requests
            session = requests.Session()
            session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'application/json',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'Referer': 'https://www.nseindia.com/option-chain',
            })
            session.get('https://www.nseindia.com', timeout=10)
            resp = session.get(
                'https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY',
                timeout=15,
            )
            if resp.status_code == 200:
                raw = resp.json()
                if raw and isinstance(raw, dict) and raw.get('records', {}).get('data'):
                    logger.info(f"NSE option chain via direct API: {len(raw['records']['data'])} entries")
                    return raw
                else:
                    logger.warning("NSE direct API returned empty data (likely geo-blocked)")
            else:
                logger.warning(f"NSE direct API status: {resp.status_code}")
        except Exception as e:
            logger.warning(f"NSE direct API error: {e}")

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
            data = svc.get_option_chain('NIFTY')
            if data and data.get('option_chain'):
                return data
        except Exception as e:
            logger.warning(f"Option chain fetch error: {e}")
        return self._get_sample_option_chain()

    def _get_sample_chain(self, spot: float) -> dict:
        atm = round(spot / STRIKE_INTERVAL) * STRIKE_INTERVAL
        chain = {}
        base_iv = 13.0
        days_to_expiry = 4
        time_val = (base_iv / 100) * spot * (days_to_expiry / 365) ** 0.5 * 0.4

        for i in range(-6, 7):
            strike = atm + i * STRIKE_INTERVAL
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
        spot = 23500
        try:
            from services.dhan_service import get_nifty_spot
            s = get_nifty_spot(user_id=self.user_id)
            if s and s > 0:
                spot = s
        except Exception:
            pass
        if spot == 23500:
            try:
                import yfinance as yf
                hist = yf.Ticker("^NSEI").history(period="1d")
                if not hist.empty:
                    spot = float(hist['Close'].iloc[-1])
            except Exception:
                pass
        chain = self._get_sample_chain(spot)
        return {
            'spot_price': spot,
            'previous_close': spot - 30,
            'lot_size': NIFTY_LOT_SIZE,
            'strike_interval': STRIKE_INTERVAL,
            'option_chain': chain,
            'expiry_dates': [],
        }

    def _compute_oi_differential(self, chain: dict, atm: int) -> float:
        total_put_oi = 0
        total_call_oi = 0
        for i in range(-6, 7):
            strike = atm + i * STRIKE_INTERVAL
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
        atm_range = [s for s in strikes if abs(s - atm) <= 3 * STRIKE_INTERVAL]
        atm_call  = sum(chain.get(f'{s}CE', {}).get('oi', 0) for s in atm_range)
        atm_put   = sum(chain.get(f'{s}PE', {}).get('oi', 0) for s in atm_range)
        atm_pcr   = round(atm_put / atm_call, 2) if atm_call else 0

        # ── OI Differential ──────────────────────────────────────────────
        oi_diff   = (total_put_oi - total_call_oi) / total_call_oi if total_call_oi else 0
        oi_signal = 'BULLISH' if oi_diff > 0.20 else ('BEARISH' if oi_diff < -0.20 else 'NEUTRAL')

        # ── Max Pain ─────────────────────────────────────────────────────
        # Strike where total loss for option buyers (CE+PE combined) is maximum
        pain = {}
        for test_s in strikes:
            ce_pain = sum(max(0, test_s - s) * chain.get(f'{s}CE', {}).get('oi', 0) for s in strikes)
            pe_pain = sum(max(0, s - test_s) * chain.get(f'{s}PE', {}).get('oi', 0) for s in strikes)
            pain[test_s] = ce_pain + pe_pain
        max_pain_strike = max(pain, key=pain.get) if pain else atm
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
        if current_time < dtime(9, 30):
            return {'pass': False, 'reason': 'Pre-market — no trades before 9:30 AM', 'status': 'blocked'}
        if current_time > dtime(14, 45):
            return {'pass': False, 'reason': 'Market closing — no new trades after 2:45 PM', 'status': 'blocked'}
        if now.weekday() >= 5:
            return {'pass': False, 'reason': 'Weekend — market closed', 'status': 'blocked'}
        return {'pass': True, 'reason': 'Trading window active', 'status': 'active'}

    # ------------------------------------------------------------------
    # Real indicator calculations — intraday 5-min candles
    # Priority 1: Dhan  |  Priority 2: yfinance (fallback)
    # ------------------------------------------------------------------

    def _fetch_intraday_candles(self):
        """
        Fetch today's 5-min NIFTY candles.
        Tries Dhan first (paid, reliable), falls back to yfinance.
        Result is cached at module level for _CANDLE_CACHE_TTL seconds.
        """
        global _candle_cache_data, _candle_cache_ts
        now_ts = _time_mod.time()
        if _candle_cache_data is not None and (now_ts - _candle_cache_ts) < _CANDLE_CACHE_TTL:
            return _candle_cache_data

        df = None

        # ── Priority 1: Dhan intraday candles ──────────────────────────
        try:
            from services.dhan_service import get_nifty_intraday_candles
            df_dhan = get_nifty_intraday_candles(interval=5, user_id=self.user_id)
            if df_dhan is not None and not df_dhan.empty and len(df_dhan) >= 5:
                df = df_dhan
                logger.info(f"_fetch_intraday_candles: {len(df)} candles from Dhan")
        except Exception as e:
            logger.warning(f"Dhan intraday candles error: {e}")

        # ── Priority 2: yfinance fallback ──────────────────────────────
        if df is None or df.empty:
            try:
                import yfinance as yf
                ticker = yf.Ticker("^NSEI")
                df_yf = ticker.history(period="1d", interval="5m")
                if df_yf is not None and not df_yf.empty and len(df_yf) >= 5:
                    df = df_yf
                    logger.info(f"_fetch_intraday_candles: {len(df)} candles from yfinance (fallback)")
                else:
                    logger.warning("_fetch_intraday_candles: yfinance also returned no data")
            except Exception as e:
                logger.warning(f"yfinance intraday candles error: {e}")

        _candle_cache_data = df if (df is not None and not df.empty) else None
        _candle_cache_ts = now_ts
        return _candle_cache_data

    def _calculate_vwap(self, df) -> float:
        """Cumulative VWAP from today's candles."""
        try:
            tp = (df['High'] + df['Low'] + df['Close']) / 3
            vol = df['Volume'].replace(0, 1)
            vwap = (tp * vol).cumsum() / vol.cumsum()
            return float(vwap.iloc[-1])
        except Exception:
            return float(df['Close'].iloc[-1])

    def _calculate_adx(self, df, period: int = 14):
        """Wilder's ADX, +DI, -DI. Returns (adx, dmi_plus, dmi_minus, adx_rising)."""
        try:
            import pandas as pd
            h = df['High']
            l = df['Low']
            c = df['Close']

            tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
            up = h.diff()
            down = -l.diff()
            dm_p = up.where((up > 0) & (up > down), 0.0)
            dm_m = down.where((down > 0) & (down > up), 0.0)

            # Drop the initial NaN row from diff so rolling().sum() gives correct first window
            tr = tr.dropna()
            dm_p = dm_p.dropna()
            dm_m = dm_m.dropna()

            if len(tr) < period * 2:
                return 22.0, 22.0, 22.0, False

            # Wilder smoothing: first window = simple sum, subsequent = prev - prev/n + current
            def wilder_smooth(series, n):
                vals = series.values.astype(float)
                out = [float('nan')] * len(vals)
                out[n - 1] = float(vals[:n].sum())
                for i in range(n, len(vals)):
                    out[i] = out[i - 1] - out[i - 1] / n + vals[i]
                return pd.Series(out, index=series.index)

            atr_w = wilder_smooth(tr, period)
            dmp_w = wilder_smooth(dm_p, period)
            dmm_w = wilder_smooth(dm_m, period)

            safe_atr = atr_w.replace(0, 0.01)
            di_p = (100 * dmp_w / safe_atr).clip(0, 100)
            di_m = (100 * dmm_w / safe_atr).clip(0, 100)
            dx = (100 * (di_p - di_m).abs() / (di_p + di_m).replace(0, 0.01)).clip(0, 100)
            # ADX = EMA of DX (Wilder's alpha = 1/period, com = period-1)
            adx_s = dx.dropna().ewm(com=period - 1, adjust=False).mean()

            valid = adx_s.dropna()
            if len(valid) < 2:
                return 22.0, 22.0, 22.0, False

            adx = min(float(valid.iloc[-1]), 100.0)
            dmi_p = min(float(di_p.iloc[-1]), 100.0)
            dmi_m = min(float(di_m.iloc[-1]), 100.0)
            rising = bool(valid.iloc[-1] > valid.iloc[-3]) if len(valid) >= 3 else False
            return round(adx, 1), round(dmi_p, 1), round(dmi_m, 1), rising
        except Exception as e:
            logger.warning(f"ADX calculation error: {e}")
            return 22.0, 22.0, 22.0, False

    def _calculate_atr(self, df, period: int = 14):
        """EMA-based ATR. Returns (atr, atr_rising)."""
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
        """Simple Supertrend signal. Returns 'BUY' or 'SELL'."""
        try:
            import pandas as pd
            h = df['High']
            l = df['Low']
            c = df['Close']
            tr = pd.concat([h - l, (h - c.shift(1)).abs(), (l - c.shift(1)).abs()], axis=1).max(axis=1)
            atr = tr.ewm(span=atr_period, adjust=False).mean()
            mid = (h + l) / 2
            upper = mid + multiplier * atr
            lower = mid - multiplier * atr
            last_close = float(c.iloc[-1])
            last_upper = float(upper.iloc[-1])
            last_lower = float(lower.iloc[-1])
            return 'BUY' if last_close > last_lower and last_close > (last_upper + last_lower) / 2 else 'SELL'
        except Exception as e:
            logger.warning(f"Supertrend calculation error: {e}")
            return 'BUY'

    def _direction_engine(self, spot: float) -> Dict[str, Any]:
        df = self._fetch_intraday_candles()
        if df is not None and len(df) >= 10:
            vwap = self._calculate_vwap(df)
            supertrend_signal = self._calculate_supertrend(df)
            _, dmi_plus, dmi_minus, _ = self._calculate_adx(df)
        else:
            vwap = spot
            supertrend_signal = 'BUY'
            dmi_plus = 22.0
            dmi_minus = 22.0

        bullish = spot > vwap and supertrend_signal == 'BUY' and dmi_plus > dmi_minus
        bearish = spot < vwap and supertrend_signal == 'SELL' and dmi_minus > dmi_plus
        direction = 'BULLISH' if bullish else ('BEARISH' if bearish else 'NEUTRAL')

        return {
            'direction': direction,
            'vwap': round(vwap, 2),
            'spot_vs_vwap': 'ABOVE' if spot > vwap else 'BELOW',
            'supertrend': supertrend_signal,
            'dmi_plus': round(dmi_plus, 1),
            'dmi_minus': round(dmi_minus, 1),
            'indicators_aligned': bullish or bearish,
        }

    def _strength_engine(self, spot: float) -> Dict[str, Any]:
        df = self._fetch_intraday_candles()
        if df is not None and len(df) >= 10:
            adx, _, _, adx_rising = self._calculate_adx(df)
            atr, atr_rising = self._calculate_atr(df)
        else:
            adx, adx_rising = 22.0, False
            atr, atr_rising = 100.0, False

        strong = adx > 25 and adx_rising and atr_rising
        no_trade_zone = adx < 20 or (not atr_rising and not adx_rising)

        return {
            'adx': round(adx, 1),
            'adx_rising': adx_rising,
            'atr': round(atr, 2),
            'atr_rising': atr_rising,
            'strength': 'STRONG' if strong else ('WEAK' if no_trade_zone else 'MODERATE'),
            'no_trade_zone': no_trade_zone,
        }

    def _confidence_score(self, direction: dict, strength: dict, oi_diff: float) -> int:
        score = 0
        if direction['spot_vs_vwap'] in ['ABOVE', 'BELOW'] and direction['direction'] != 'NEUTRAL':
            score += 20
        if direction['indicators_aligned']:
            score += 15
        if direction['dmi_plus'] != direction['dmi_minus']:
            score += 15
        if abs(oi_diff) > 0.20:
            score += 20
        if strength['adx'] > 25:
            score += 15
        if strength['atr_rising']:
            score += 15
        return min(score, 100)

    def _select_strikes(self, spot: float, direction: str) -> List[Dict[str, Any]]:
        atm = int(round(spot / STRIKE_INTERVAL) * STRIKE_INTERVAL)
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
            otm_strike = atm + STRIKE_INTERVAL
            itm_strike = atm - STRIKE_INTERVAL
        else:
            otm_strike = atm - STRIKE_INTERVAL
            itm_strike = atm + STRIKE_INTERVAL

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

    def _generate_trade_reasons(self, trade: dict, direction: dict, strength: dict, oi_signal: str) -> List[str]:
        reasons = []
        m = trade.get('moneyness', '')
        if direction['indicators_aligned']:
            reasons.append(f"All direction indicators aligned ({direction['direction']})")
        if strength['adx'] > 25:
            reasons.append(f"Strong trend — ADX {strength['adx']}")
        if strength['adx_rising']:
            reasons.append("ADX rising — trend gaining strength")
        if strength['atr_rising']:
            reasons.append("ATR rising — volatility expanding")
        if oi_signal != 'NEUTRAL':
            reasons.append(f"OI confirms {oi_signal.lower()} bias")
        if m == 'ATM':
            reasons.append("Best delta exposure — highest probability of profit")
        elif m == 'OTM':
            reasons.append("Lower premium cost — higher leverage if move extends")
        elif m == 'ITM':
            reasons.append("Higher intrinsic value — safer with more delta")
        return reasons[:4]

    def _enrich_trades(self, trades: List[Dict], chain: dict, confidence: int, entry_mode: str, expiry_info: dict = None) -> List[Dict]:
        enriched = []
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

            sl_points = 10
            target_points = 20
            entry_price = ltp
            target = round(entry_price + target_points, 2)
            sl = round(entry_price - sl_points, 2)

            lot_value = ltp * NIFTY_LOT_SIZE
            max_loss_per_lot = sl_points * NIFTY_LOT_SIZE
            max_profit_per_lot = target_points * NIFTY_LOT_SIZE

            trade_data = {
                **t,
                'action': 'BUY',
                'symbol': f"NIFTY {t['strike']} {t['type']}",
                'ltp': round(ltp, 2),
                'entry_price': round(entry_price, 2),
                'sl': sl,
                'target': target,
                'sl_points': sl_points,
                'target_points': target_points,
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
                'lot_size': NIFTY_LOT_SIZE,
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

        if admin_plan == 'nse_truedata':
            td_spot, td_chain, td_name = self._get_truedata()
            if td_spot and td_chain:
                data_source = f'broker:{td_name}'
                spot = td_spot
                current_chain = td_chain
                next_chain = {}
        elif admin_plan == 'user_data':
            broker_spot, broker_chain, broker_name, broker_expiry_list = self._get_broker_data()
            if broker_spot and broker_chain:
                data_source = f'broker:{broker_name}'
                spot = broker_spot
                current_chain = broker_chain
                next_chain = {}
                # Build expiry_picks from the broker's expiry list
                if broker_expiry_list:
                    expiry_picks = self._pick_expiries(broker_expiry_list)

        if not current_chain:
            raw_nse = self._get_nse_option_chain_raw()
            expiry_dates = self._parse_expiry_dates(raw_nse)
            expiry_picks = self._pick_expiries(expiry_dates)

            if raw_nse and expiry_picks.get('current'):
                data_source = 'live'
                spot_val = raw_nse.get('records', {}).get('underlyingValue', 0) or raw_nse.get('filtered', {}).get('data', [{}])[0].get('PE', {}).get('underlyingValue', 0) if raw_nse.get('filtered', {}).get('data') else 0
                if not spot_val:
                    spot_val = 23500
                spot = float(spot_val)
                current_chain = self._build_chain_for_expiry(raw_nse, expiry_picks['current'], spot)
                next_chain = self._build_chain_for_expiry(raw_nse, expiry_picks['next'], spot) if expiry_picks.get('next') else {}

        if not current_chain:
            data_source = 'estimated'
            spot = 23500
            # Priority 1: Dhan spot price
            try:
                from services.dhan_service import get_nifty_spot
                s = get_nifty_spot(user_id=self.user_id)
                if s and s > 0:
                    spot = s
            except Exception:
                pass
            # Priority 2: yfinance fallback
            if spot == 23500:
                try:
                    import yfinance as yf
                    hist = yf.Ticker("^NSEI").history(period="1d")
                    if not hist.empty:
                        spot = float(hist['Close'].iloc[-1])
                except Exception:
                    pass
            logger.warning(f"All data sources unavailable — using estimated data. Spot: {spot}")
            current_chain = self._get_sample_chain(spot)
            next_chain = {}

        atm = int(round(spot / STRIKE_INTERVAL) * STRIKE_INTERVAL)

        direction = self._direction_engine(spot)
        strength = self._strength_engine(spot)
        oi_metrics = self._compute_oi_metrics(current_chain, atm, spot)
        oi_diff   = oi_metrics['oi_diff']
        oi_signal = oi_metrics['oi_signal']
        confidence = self._confidence_score(direction, strength, oi_diff)

        entry_mode = 'NO TRADE'
        if time_check['pass'] and not strength['no_trade_zone']:
            if strength['adx'] > 28:
                entry_mode = 'CONFIRMED'
            elif strength['adx'] > 25:
                entry_mode = 'EARLY'

        trade_direction = direction['direction']
        if oi_signal != 'NEUTRAL' and direction['direction'] == 'NEUTRAL':
            trade_direction = oi_signal

        block_reasons = []
        if not time_check['pass']:
            block_reasons.append(time_check['reason'])
        if strength['no_trade_zone']:
            block_reasons.append(f"Weak momentum — ADX {strength['adx']:.1f} (below 20)")
        if not direction['indicators_aligned'] and direction['direction'] == 'NEUTRAL':
            block_reasons.append("No clear direction — indicators not aligned")

        is_blocked = entry_mode == 'NO TRADE'
        final_decision = 'TRADE' if not is_blocked and confidence >= 60 else 'NO TRADE'
        if is_blocked and not block_reasons:
            if confidence < 60:
                block_reasons.append(f"Confidence too low ({confidence}/100, need 60+)")
            else:
                block_reasons.append("Entry conditions not met")

        momentum_pct = min(100, max(0, int(
            (strength['adx'] / 40 * 40) +
            (20 if strength['adx_rising'] else 0) +
            (20 if strength['atr_rising'] else 0) +
            (20 if direction['indicators_aligned'] else 0)
        )))
        momentum_label = 'STRONG' if momentum_pct >= 70 else ('MODERATE' if momentum_pct >= 40 else 'WEAK')

        current_trades = []
        next_trades = []
        raw_strikes = self._select_strikes(spot, trade_direction)

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
            t['trade_reasons'] = self._generate_trade_reasons(t, direction, strength, oi_signal)
        for t in next_trades:
            t['trade_reasons'] = self._generate_trade_reasons(t, direction, strength, oi_signal)

        layer_status = {
            'time': 'pass' if time_check['pass'] else 'fail',
            'direction': 'pass' if direction['indicators_aligned'] else ('warn' if direction['direction'] != 'NEUTRAL' else 'fail'),
            'strength': 'pass' if strength['strength'] == 'STRONG' else ('warn' if strength['strength'] == 'MODERATE' else 'fail'),
            'oi': 'pass' if abs(oi_diff) > 0.20 else ('warn' if abs(oi_diff) > 0.10 else 'fail'),
        }

        return {
            'timestamp': now.strftime('%Y-%m-%d %H:%M:%S IST'),
            'data_source': data_source,
            'spot_price': spot,
            'atm_strike': atm,
            'time_filter': time_check,
            'direction': direction,
            'strength': strength,
            'oi_analysis': oi_metrics,
            'confidence': confidence,
            'confidence_grade': 'Strong' if confidence >= 80 else ('Medium' if confidence >= 60 else 'Weak'),
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
        }
