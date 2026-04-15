import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

STRIKE_INTERVAL = 50
STRIKE_RANGE = 6


def build_option_chain(broker, instruments: List[Dict], spot_price: float,
                       symbol: str = "NIFTY", expiry: Optional[str] = None) -> List[Dict]:
    atm = round(spot_price / STRIKE_INTERVAL) * STRIKE_INTERVAL
    strikes_needed = set()
    for i in range(-STRIKE_RANGE, STRIKE_RANGE + 1):
        strikes_needed.add(int(atm + i * STRIKE_INTERVAL))

    filtered = []
    for inst in instruments:
        inst_symbol = inst.get("symbol", "")
        if symbol.upper() not in inst_symbol.upper() and inst_symbol.upper() != symbol.upper():
            continue
        strike = int(inst.get("strike", 0))
        if strike not in strikes_needed:
            continue
        if expiry and inst.get("expiry", "") != expiry:
            continue
        filtered.append(inst)

    if not filtered:
        if expiry:
            for inst in instruments:
                inst_symbol = inst.get("symbol", "")
                if symbol.upper() not in inst_symbol.upper() and inst_symbol.upper() != symbol.upper():
                    continue
                strike = int(inst.get("strike", 0))
                if strike in strikes_needed:
                    filtered.append(inst)
        if not filtered:
            logger.warning(f"No matching instruments for {symbol} around ATM {atm}")
            return []

    tokens = [inst.get("token", "") for inst in filtered if inst.get("token")]
    strike_map = {}
    for inst in filtered:
        strike = int(inst.get("strike", 0))
        opt_type = inst.get("type", "")
        if strike not in strike_map:
            strike_map[strike] = {"strike": strike, "ce_token": None, "pe_token": None}
        if opt_type == "CE":
            strike_map[strike]["ce_token"] = inst.get("token", "")
        elif opt_type == "PE":
            strike_map[strike]["pe_token"] = inst.get("token", "")

    quotes = {}
    if tokens:
        try:
            quotes = broker.get_quotes(tokens)
        except Exception as e:
            logger.error(f"Failed to fetch quotes: {e}")

    chain = []
    for strike in sorted(strike_map.keys()):
        entry = strike_map[strike]
        ce_data = quotes.get(entry["ce_token"], {}) if entry["ce_token"] else {}
        pe_data = quotes.get(entry["pe_token"], {}) if entry["pe_token"] else {}
        chain.append({
            "strike": strike,
            "call_ltp": ce_data.get("ltp", 0),
            "put_ltp": pe_data.get("ltp", 0),
            "call_oi": ce_data.get("oi", 0),
            "put_oi": pe_data.get("oi", 0),
            "call_volume": ce_data.get("volume", 0),
            "put_volume": pe_data.get("volume", 0),
            "call_bid": ce_data.get("bid", 0),
            "call_ask": ce_data.get("ask", 0),
            "put_bid": pe_data.get("bid", 0),
            "put_ask": pe_data.get("ask", 0),
        })

    return chain


def normalize_chain(raw_chain: List[Dict]) -> List[Dict]:
    normalized = []
    for item in raw_chain:
        normalized.append({
            "strike": int(item.get("strike", 0)),
            "call_ltp": float(item.get("call_ltp", 0)),
            "put_ltp": float(item.get("put_ltp", 0)),
            "call_oi": int(item.get("call_oi", 0)),
            "put_oi": int(item.get("put_oi", 0)),
            "call_iv": float(item.get("call_iv", 0)),
            "put_iv": float(item.get("put_iv", 0)),
            "call_volume": int(item.get("call_volume", 0)),
            "put_volume": int(item.get("put_volume", 0)),
            "call_bid": float(item.get("call_bid", 0)),
            "call_ask": float(item.get("call_ask", 0)),
            "put_bid": float(item.get("put_bid", 0)),
            "put_ask": float(item.get("put_ask", 0)),
            "call_oi_change": int(item.get("call_oi_change", 0)),
            "put_oi_change": int(item.get("put_oi_change", 0)),
        })
    return normalized


def chain_to_engine_format(chain: List[Dict], spot: float) -> Dict[str, Dict]:
    engine_chain = {}
    for item in chain:
        strike = int(item.get("strike", 0))
        ce_key = f"{strike}CE"
        pe_key = f"{strike}PE"
        engine_chain[ce_key] = {
            "strike": strike,
            "type": "CE",
            "ltp": item.get("call_ltp", 0),
            "oi": item.get("call_oi", 0),
            "oi_change": item.get("call_oi_change", 0),
            "volume": item.get("call_volume", 0),
            "iv": item.get("call_iv", 0),
            "bid": item.get("call_bid", 0),
            "ask": item.get("call_ask", 0),
            "bid_qty": 0,
            "ask_qty": 0,
            "change": 0,
            "pct_change": 0,
            "prev_oi": 0,
        }
        engine_chain[pe_key] = {
            "strike": strike,
            "type": "PE",
            "ltp": item.get("put_ltp", 0),
            "oi": item.get("put_oi", 0),
            "oi_change": item.get("put_oi_change", 0),
            "volume": item.get("put_volume", 0),
            "iv": item.get("put_iv", 0),
            "bid": item.get("put_bid", 0),
            "ask": item.get("put_ask", 0),
            "bid_qty": 0,
            "ask_qty": 0,
            "change": 0,
            "pct_change": 0,
            "prev_oi": 0,
        }
    return engine_chain
