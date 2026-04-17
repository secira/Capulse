"""
Dhan Broker Adapter — uses the official dhanhq Python SDK.
Covers: LTP, OHLC, Option Chain, Expiry List, Quotes, Order Placement.

Response Parsing Note:
  The dhanhq SDK's _parse_response wraps the raw Dhan API JSON under the
  'data' key:  resp = {"status": "success", "data": <raw_api_json>}
  So the actual payload is always at resp["data"]["data"] (one extra level
  compared to what the raw Dhan API docs show).
"""
import logging
from typing import Dict, List, Any, Optional
from brokers.base import BrokerBase

logger = logging.getLogger(__name__)

# Dhan security IDs for major indices (exchange segment: IDX_I)
INDEX_SECURITY_IDS = {
    "NIFTY":      13,
    "NIFTY 50":   13,
    "NIFTY50":    13,
    "BANKNIFTY":  25,
    "BANK NIFTY": 25,
    "FINNIFTY":   27,
    "FIN NIFTY":  27,
    "MIDCPNIFTY": 11,
    "SENSEX":     51,
    "INDIA VIX":  26000,
    "VIX":        26000,
}


def _unwrap(resp: dict, segment: str = None) -> Any:
    """
    Unwrap the dhanhq SDK response.

    The SDK returns:
        {"status": "success", "data": <raw_api_json>}
    where <raw_api_json> is the full JSON body from Dhan, typically:
        {"status": "success", "data": <actual_payload>}

    Pass segment="IDX_I" to return resp["data"]["data"]["IDX_I"].
    Pass segment=None to return resp["data"]["data"] (the payload dict).
    """
    outer = resp.get("data", {})
    # outer might be a string "" when status != success
    if not isinstance(outer, dict):
        return {} if segment is None else []
    payload = outer.get("data", outer)   # graceful: if no nested 'data', use outer
    if segment is None:
        return payload if isinstance(payload, dict) else {}
    if isinstance(payload, dict):
        return payload.get(segment, [])
    if isinstance(payload, list):
        return payload  # some endpoints return a top-level list
    return []


class DhanBroker(BrokerBase):

    BROKER_NAME = "dhan"
    SUPPORTS_DIRECT_CHAIN = True

    def __init__(self, credentials: Dict[str, str]):
        super().__init__(credentials)
        self.client_id = credentials.get("client_id", "")
        self.access_token = credentials.get("access_token", "")
        self._sdk = None

    # ------------------------------------------------------------------
    # SDK helper — creates / reuses dhanhq instance
    # ------------------------------------------------------------------
    def _get_sdk(self):
        if self._sdk is None:
            from dhanhq import dhanhq
            self._sdk = dhanhq(self.client_id, self.access_token)
        return self._sdk

    # ------------------------------------------------------------------
    # BrokerBase interface
    # ------------------------------------------------------------------
    def connect(self) -> bool:
        try:
            resp = self._get_sdk().get_fund_limits()
            if resp.get("status") == "success":
                self._connected = True
                logger.info("Dhan connected successfully")
                return True
            logger.warning(f"Dhan connect failed: {resp.get('remarks', '')}")
            return False
        except Exception as e:
            logger.error(f"Dhan connect error: {e}")
            return False

    def get_price(self, symbol: str) -> float:
        """
        Return LTP for an index symbol via /v2/marketfeed/ohlc (IDX_I segment).
        Using ohlc_data instead of ticker_data because the LTP endpoint does
        not reliably return index prices in the IDX_I segment.
        """
        try:
            sec_id = INDEX_SECURITY_IDS.get(symbol.upper())
            if sec_id is None:
                logger.warning(f"Unknown index symbol for Dhan: {symbol}")
                return 0.0
            resp = self._get_sdk().ohlc_data({"IDX_I": [sec_id]})
            if resp.get("status") == "success":
                items = _unwrap(resp, "IDX_I")
                logger.debug(f"Dhan get_price({symbol}) ohlc items: {items}")
                for item in (items if isinstance(items, list) else []):
                    sid = str(item.get("security_id", ""))
                    if sid == str(sec_id):
                        ltp = float(item.get("ltp", item.get("last_price", 0)))
                        if ltp > 0:
                            return ltp
                # fallback: first item
                if isinstance(items, list) and items:
                    ltp = float(items[0].get("ltp", items[0].get("last_price", 0)))
                    if ltp > 0:
                        return ltp
            logger.warning(f"Dhan get_price({symbol}): no LTP in response, resp={resp.get('remarks','')}")
            return 0.0
        except Exception as e:
            logger.error(f"Dhan get_price({symbol}) error: {e}")
            return 0.0

    def get_index_ohlc(self, symbols: List[str]) -> Dict[str, Dict]:
        """Get OHLC + LTP for a list of index symbols in one call."""
        try:
            sec_map = {INDEX_SECURITY_IDS[s.upper()]: s for s in symbols if s.upper() in INDEX_SECURITY_IDS}
            if not sec_map:
                return {}
            resp = self._get_sdk().ohlc_data({"IDX_I": list(sec_map.keys())})
            result = {}
            if resp.get("status") == "success":
                items = _unwrap(resp, "IDX_I")
                logger.debug(f"Dhan get_index_ohlc items: {items}")
                for item in (items if isinstance(items, list) else []):
                    sid = int(item.get("security_id", -1))
                    sym = sec_map.get(sid, str(sid))
                    ltp = float(item.get("ltp", item.get("last_price", 0)))
                    result[sym] = {
                        "ltp":        ltp,
                        "open":       float(item.get("open", 0)),
                        "high":       float(item.get("high", 0)),
                        "low":        float(item.get("low", 0)),
                        "close":      float(item.get("previous_close", item.get("close", 0))),
                        "change":     float(item.get("net_change", item.get("change", 0))),
                        "pct_change": float(item.get("percent_change", 0)),
                    }
            return result
        except Exception as e:
            logger.error(f"Dhan get_index_ohlc error: {e}")
            return {}

    def get_expiry_list(self, symbol: str = "NIFTY") -> List[str]:
        """Return sorted list of upcoming expiry dates (YYYY-MM-DD) for an index."""
        try:
            sec_id = INDEX_SECURITY_IDS.get(symbol.upper(), 13)
            resp = self._get_sdk().expiry_list(sec_id, "IDX_I")
            logger.debug(f"Dhan expiry_list raw resp status={resp.get('status')} data_type={type(resp.get('data'))}")
            if resp.get("status") == "success":
                # The SDK wraps the API response: resp["data"] = <api_json>
                # Dhan API returns {"status":"success","data":["YYYY-MM-DD",...]}
                # So resp["data"]["data"] = the list of dates
                payload = _unwrap(resp)  # resp["data"]["data"]
                if isinstance(payload, list):
                    dates = payload
                else:
                    # payload might itself be the outer api dict with a "data" key
                    outer_data = resp.get("data", {})
                    if isinstance(outer_data, dict):
                        dates = outer_data.get("data", [])
                    else:
                        dates = []
                logger.info(f"Dhan expiry_list for {symbol}: {dates[:5]}...")
                return sorted([d for d in dates if isinstance(d, str) and len(d) >= 8])
            logger.warning(f"Dhan expiry_list failed: {resp.get('remarks', '')}")
            return []
        except Exception as e:
            logger.error(f"Dhan get_expiry_list error: {e}")
            return []

    def get_option_chain(self, symbol: str, expiry: Optional[str] = None) -> List[Dict]:
        """
        Fetch the complete option chain for an index.
        Returns a list of dicts compatible with option_chain_builder.normalize_chain().

        If expiry is None, the nearest available expiry from Dhan is used.
        """
        try:
            sec_id = INDEX_SECURITY_IDS.get(symbol.upper(), 13)

            # Step 1: resolve expiry
            if not expiry:
                dates = self.get_expiry_list(symbol)
                if not dates:
                    logger.warning(f"No expiry dates from Dhan for {symbol}")
                    return []
                expiry = dates[0]

            logger.info(f"Fetching Dhan option chain: {symbol} (sec_id={sec_id}) expiry={expiry}")

            # Step 2: fetch option chain
            resp = self._get_sdk().option_chain(sec_id, "IDX_I", expiry)
            if resp.get("status") != "success":
                logger.warning(f"Dhan option chain failed (HTTP!=200): {resp.get('remarks', '')}")
                return []

            # SDK wraps: resp["data"] = <api_json>
            # Dhan API: {"status":"success","data":{"last_price":...,"oc":{...}}}
            # So actual payload is resp["data"]["data"]
            oc_data = _unwrap(resp)   # resp["data"]["data"] = {"last_price":..,"oc":{..}}
            logger.debug(f"Dhan option chain oc_data keys: {list(oc_data.keys()) if isinstance(oc_data, dict) else type(oc_data)}")

            oc = oc_data.get("oc", {})
            spot = float(oc_data.get("last_price", 0))

            if not oc:
                logger.warning(f"Dhan option chain: empty 'oc' in response for {symbol} expiry={expiry}")
                return []

            chain = []
            for strike_str, options in oc.items():
                try:
                    strike = int(float(strike_str))
                    call_md = options.get("call_options", {}).get("market_data", {})
                    put_md  = options.get("put_options",  {}).get("market_data", {})
                    call_od = options.get("call_options", {}).get("option_data", {})
                    put_od  = options.get("put_options",  {}).get("option_data", {})
                    chain.append({
                        "strike":           strike,
                        "call_ltp":         float(call_md.get("ltp", 0)),
                        "put_ltp":          float(put_md.get("ltp", 0)),
                        "call_oi":          int(call_md.get("open_interest", 0)),
                        "put_oi":           int(put_md.get("open_interest", 0)),
                        "call_iv":          float(call_md.get("iv", 0)),
                        "put_iv":           float(put_md.get("iv", 0)),
                        "call_volume":      int(call_md.get("volume", 0)),
                        "put_volume":       int(put_md.get("volume", 0)),
                        "call_bid":         float(call_md.get("best_bid_price", 0)),
                        "call_ask":         float(call_md.get("best_ask_price", 0)),
                        "put_bid":          float(put_md.get("best_bid_price", 0)),
                        "put_ask":          float(put_md.get("best_ask_price", 0)),
                        "call_oi_change":   0,
                        "put_oi_change":    0,
                        "call_security_id": call_od.get("security_id"),
                        "put_security_id":  put_od.get("security_id"),
                        "spot":             spot,
                        "expiry":           expiry,
                    })
                except (ValueError, TypeError) as exc:
                    logger.debug(f"Skipping strike {strike_str}: {exc}")
                    continue

            chain_sorted = sorted(chain, key=lambda x: x["strike"])
            logger.info(f"Dhan option chain for {symbol} [{expiry}]: {len(chain_sorted)} strikes, spot={spot}")
            return chain_sorted

        except Exception as e:
            logger.error(f"Dhan get_option_chain error: {e}")
            return []

    def get_quotes(self, tokens: list) -> Dict[str, Dict]:
        """Fetch quote data for a list of NSE_FNO security IDs."""
        try:
            if not tokens:
                return {}
            int_tokens = [int(t) for t in tokens if t]
            resp = self._get_sdk().quote_data({"NSE_FNO": int_tokens})
            if resp.get("status") == "success":
                items = _unwrap(resp, "NSE_FNO")
                result = {}
                for item in (items if isinstance(items, list) else []):
                    tid = str(item.get("security_id", ""))
                    result[tid] = {
                        "ltp":    float(item.get("ltp", item.get("last_price", 0))),
                        "oi":     int(item.get("oi", item.get("open_interest", 0))),
                        "volume": int(item.get("volume", 0)),
                        "bid":    float(item.get("best_bid_price", 0)),
                        "ask":    float(item.get("best_ask_price", 0)),
                    }
                return result
            return {}
        except Exception as e:
            logger.error(f"Dhan get_quotes error: {e}")
            return {}

    def get_eq_ohlc(self, security_ids: List[int]) -> Dict[str, Dict]:
        """Fetch OHLC + LTP for a list of NSE_EQ security IDs."""
        try:
            if not security_ids:
                return {}
            resp = self._get_sdk().ohlc_data({"NSE_EQ": security_ids})
            result = {}
            if resp.get("status") == "success":
                items = _unwrap(resp, "NSE_EQ")
                for item in (items if isinstance(items, list) else []):
                    sid = str(item.get("security_id", ""))
                    result[sid] = {
                        "ltp":        float(item.get("ltp", item.get("last_price", 0))),
                        "open":       float(item.get("open", 0)),
                        "high":       float(item.get("high", 0)),
                        "low":        float(item.get("low", 0)),
                        "close":      float(item.get("previous_close", 0)),
                        "change":     float(item.get("net_change", item.get("change", 0))),
                        "pct_change": float(item.get("percent_change", 0)),
                    }
            return result
        except Exception as e:
            logger.error(f"Dhan get_eq_ohlc error: {e}")
            return {}

    # ------------------------------------------------------------------
    # Historical & Intraday Candle Data
    # ------------------------------------------------------------------

    def get_intraday_candles(self, security_id: int, exchange_segment: str,
                             instrument_type: str, from_date: str,
                             to_date: str, interval: int = 5) -> List[Dict]:
        """
        Fetch intraday OHLCV candles from Dhan's /charts/intraday endpoint.

        Args:
            security_id:      Dhan security ID (e.g. 13 for NIFTY index)
            exchange_segment: "IDX_I", "NSE_EQ", "NSE_FNO", etc.
            instrument_type:  "INDEX", "EQUITY", "FUTIDX", "OPTIDX", etc.
            from_date:        "YYYY-MM-DD"
            to_date:          "YYYY-MM-DD"
            interval:         candle size in minutes — 1, 5, 15, 25, or 60

        Returns:
            List of dicts: [{"open": f, "high": f, "low": f, "close": f, "volume": i, "timestamp": str}, ...]
            Empty list on failure.
        """
        try:
            sdk = self._get_sdk()
            resp = sdk.intraday_minute_data(
                security_id=str(security_id),
                exchange_segment=exchange_segment,
                instrument_type=instrument_type,
                from_date=from_date,
                to_date=to_date,
                interval=interval,
            )
            if resp.get("status") != "success":
                logger.warning(f"Dhan intraday_candles failed: {resp.get('remarks', '')}")
                return []
            # SDK wraps: resp["data"] = api_json; api_json["data"] = payload dict
            outer = resp.get("data", {})
            payload = outer.get("data", outer) if isinstance(outer, dict) else {}
            if not isinstance(payload, dict):
                return []
            opens  = payload.get("open",      [])
            highs  = payload.get("high",      [])
            lows   = payload.get("low",       [])
            closes = payload.get("close",     [])
            vols   = payload.get("volume",    [])
            stamps = payload.get("timestamp", [])
            rows = []
            for i in range(min(len(opens), len(closes))):
                rows.append({
                    "timestamp": stamps[i] if i < len(stamps) else "",
                    "open":   float(opens[i]),
                    "high":   float(highs[i]) if i < len(highs) else float(opens[i]),
                    "low":    float(lows[i])  if i < len(lows)  else float(opens[i]),
                    "close":  float(closes[i]),
                    "volume": int(vols[i])    if i < len(vols)  else 0,
                })
            logger.info(f"Dhan intraday_candles: {len(rows)} candles for sec_id={security_id}")
            return rows
        except Exception as e:
            logger.error(f"Dhan get_intraday_candles error: {e}")
            return []

    def get_historical_daily_data(self, security_id: int, exchange_segment: str,
                                  instrument_type: str, from_date: str,
                                  to_date: str) -> List[Dict]:
        """
        Fetch daily OHLCV candles from Dhan's /charts/historical endpoint.

        Args:
            security_id:      Dhan security ID
            exchange_segment: "IDX_I", "NSE_EQ", etc.
            instrument_type:  "INDEX", "EQUITY", etc.
            from_date:        "YYYY-MM-DD"
            to_date:          "YYYY-MM-DD"

        Returns:
            List of dicts: [{"open": f, "high": f, "low": f, "close": f, "volume": i, "timestamp": str}, ...]
            Empty list on failure.
        """
        try:
            sdk = self._get_sdk()
            resp = sdk.historical_daily_data(
                security_id=str(security_id),
                exchange_segment=exchange_segment,
                instrument_type=instrument_type,
                from_date=from_date,
                to_date=to_date,
                expiry_code=0,
            )
            if resp.get("status") != "success":
                logger.warning(f"Dhan historical_daily_data failed: {resp.get('remarks', '')}")
                return []
            outer = resp.get("data", {})
            payload = outer.get("data", outer) if isinstance(outer, dict) else {}
            if not isinstance(payload, dict):
                return []
            opens  = payload.get("open",      [])
            highs  = payload.get("high",      [])
            lows   = payload.get("low",       [])
            closes = payload.get("close",     [])
            vols   = payload.get("volume",    [])
            stamps = payload.get("timestamp", [])
            rows = []
            for i in range(min(len(opens), len(closes))):
                rows.append({
                    "timestamp": stamps[i] if i < len(stamps) else "",
                    "open":   float(opens[i]),
                    "high":   float(highs[i]) if i < len(highs) else float(opens[i]),
                    "low":    float(lows[i])  if i < len(lows)  else float(opens[i]),
                    "close":  float(closes[i]),
                    "volume": int(vols[i])    if i < len(vols)  else 0,
                })
            logger.info(f"Dhan historical_daily: {len(rows)} rows for sec_id={security_id}")
            return rows
        except Exception as e:
            logger.error(f"Dhan get_historical_daily_data error: {e}")
            return []

    # ------------------------------------------------------------------
    # Instruments (not implemented via Dhan for now — too heavy)
    # ------------------------------------------------------------------
    def get_instruments(self, exchange: str = "NFO") -> List[Dict]:
        return []

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------
    def place_order(self, symbol: str, qty: int, side: str,
                    order_type: str = "MARKET", product: str = "INTRADAY",
                    price: float = 0, trigger_price: float = 0) -> Dict:
        try:
            sdk = self._get_sdk()
            txn = sdk.BUY if side.upper() == "BUY" else sdk.SELL
            ptype = sdk.INTRA if product.upper() in ("INTRADAY", "MIS", "INTRA") else sdk.CNC
            otype = sdk.MARKET if order_type.upper() == "MARKET" else sdk.LIMIT
            resp = sdk.place_order(
                security_id=symbol,
                exchange_segment=sdk.NSE_FNO,
                transaction_type=txn,
                quantity=qty,
                order_type=otype,
                product_type=ptype,
                price=price,
                trigger_price=trigger_price,
            )
            return {
                "status": resp.get("status", "unknown"),
                "order_id": resp.get("data", {}).get("orderId") if isinstance(resp.get("data"), dict) else None,
                "raw": resp,
            }
        except Exception as e:
            logger.error(f"Dhan place_order error: {e}")
            return {"status": "error", "message": str(e)}

    # ------------------------------------------------------------------
    # Portfolio data
    # ------------------------------------------------------------------
    def get_holdings(self) -> List[Dict]:
        try:
            resp = self._get_sdk().get_holdings()
            return resp.get("data", []) if resp.get("status") == "success" else []
        except Exception as e:
            logger.error(f"Dhan get_holdings error: {e}")
            return []

    def get_positions(self) -> List[Dict]:
        try:
            resp = self._get_sdk().get_positions()
            return resp.get("data", []) if resp.get("status") == "success" else []
        except Exception as e:
            logger.error(f"Dhan get_positions error: {e}")
            return []

    def get_orders(self) -> List[Dict]:
        try:
            resp = self._get_sdk().get_order_list()
            return resp.get("data", []) if resp.get("status") == "success" else []
        except Exception as e:
            logger.error(f"Dhan get_orders error: {e}")
            return []
