"""
Data Normalizer — converts raw broker/NSE API responses into canonical
dicts used throughout the app (holdings, positions, orders, quotes).
All fields are type-safe with sensible defaults.
"""
from typing import Dict, List, Any, Optional


# ---------------------------------------------------------------------------
# Holdings
# ---------------------------------------------------------------------------

def normalize_holding(raw: Dict, broker: str = "") -> Dict:
    """Normalize a single holding row from any broker into a canonical dict."""
    return {
        "symbol":        str(raw.get("tradingSymbol") or raw.get("symbol") or raw.get("scripName") or ""),
        "isin":          str(raw.get("isin") or raw.get("ISIN") or ""),
        "exchange":      str(raw.get("exchange") or raw.get("exchangeSegment") or "NSE"),
        "quantity":      _int(raw.get("totalQty") or raw.get("quantity") or raw.get("netQty") or 0),
        "avg_price":     _float(raw.get("avgCostPrice") or raw.get("averagePrice") or raw.get("buyAvgPrice") or 0),
        "ltp":           _float(raw.get("ltp") or raw.get("lastPrice") or raw.get("currentPrice") or 0),
        "pnl":           _float(raw.get("unrealizedProfit") or raw.get("pnl") or 0),
        "day_change":    _float(raw.get("dayChange") or 0),
        "day_change_pct":_float(raw.get("dayChangePercentage") or 0),
        "broker":        broker,
    }


def normalize_holdings(raw_list: List[Dict], broker: str = "") -> List[Dict]:
    return [normalize_holding(r, broker) for r in (raw_list or [])]


# ---------------------------------------------------------------------------
# Positions
# ---------------------------------------------------------------------------

def normalize_position(raw: Dict, broker: str = "") -> Dict:
    return {
        "symbol":        str(raw.get("tradingSymbol") or raw.get("symbol") or ""),
        "exchange":      str(raw.get("exchange") or raw.get("exchangeSegment") or "NSE"),
        "product":       str(raw.get("productType") or raw.get("product") or ""),
        "quantity":      _int(raw.get("netQty") or raw.get("quantity") or 0),
        "avg_price":     _float(raw.get("costPrice") or raw.get("averagePrice") or raw.get("buyAvg") or 0),
        "ltp":           _float(raw.get("ltp") or raw.get("lastPrice") or 0),
        "pnl":           _float(raw.get("unrealizedProfit") or raw.get("dayChange") or raw.get("pnl") or 0),
        "realized_pnl":  _float(raw.get("realizedProfit") or raw.get("realizedPnl") or 0),
        "broker":        broker,
    }


def normalize_positions(raw_list: List[Dict], broker: str = "") -> List[Dict]:
    return [normalize_position(r, broker) for r in (raw_list or [])]


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

def normalize_order(raw: Dict, broker: str = "") -> Dict:
    return {
        "order_id":      str(raw.get("orderId") or raw.get("order_id") or raw.get("norenordno") or ""),
        "symbol":        str(raw.get("tradingSymbol") or raw.get("symbol") or raw.get("tsym") or ""),
        "exchange":      str(raw.get("exchange") or raw.get("exchangeSegment") or "NSE"),
        "side":          str(raw.get("transactionType") or raw.get("transaction_type") or raw.get("trantype") or ""),
        "quantity":      _int(raw.get("quantity") or raw.get("qty") or 0),
        "price":         _float(raw.get("price") or raw.get("prc") or 0),
        "trigger_price": _float(raw.get("triggerPrice") or raw.get("trgprc") or 0),
        "status":        str(raw.get("orderStatus") or raw.get("status") or ""),
        "order_type":    str(raw.get("orderType") or raw.get("order_type") or raw.get("prctyp") or "MARKET"),
        "product":       str(raw.get("productType") or raw.get("product") or raw.get("prd") or ""),
        "filled_qty":    _int(raw.get("filledQty") or raw.get("fillQty") or 0),
        "avg_fill_price":_float(raw.get("avgTradedPrice") or raw.get("average_price") or 0),
        "broker":        broker,
    }


def normalize_orders(raw_list: List[Dict], broker: str = "") -> List[Dict]:
    return [normalize_order(r, broker) for r in (raw_list or [])]


# ---------------------------------------------------------------------------
# Market quotes
# ---------------------------------------------------------------------------

def normalize_quote(raw: Dict) -> Dict:
    """Normalize a single market quote from any broker's quote API."""
    return {
        "ltp":           _float(raw.get("ltp") or raw.get("last_price") or raw.get("lastPrice") or 0),
        "open":          _float(raw.get("open") or raw.get("openPrice") or 0),
        "high":          _float(raw.get("high") or raw.get("highPrice") or 0),
        "low":           _float(raw.get("low") or raw.get("lowPrice") or 0),
        "close":         _float(raw.get("close") or raw.get("prevClose") or raw.get("previousClose") or 0),
        "change":        _float(raw.get("change") or raw.get("netChange") or raw.get("net_change") or 0),
        "pct_change":    _float(raw.get("pct_change") or raw.get("percentChange") or raw.get("percent_change") or 0),
        "volume":        _int(raw.get("volume") or raw.get("tradedVolume") or 0),
        "oi":            _int(raw.get("oi") or raw.get("openInterest") or raw.get("open_interest") or 0),
        "bid":           _float(raw.get("bid") or raw.get("best_bid_price") or raw.get("bestBidPrice") or 0),
        "ask":           _float(raw.get("ask") or raw.get("best_ask_price") or raw.get("bestAskPrice") or 0),
    }


# ---------------------------------------------------------------------------
# Option chain row
# ---------------------------------------------------------------------------

def normalize_option_row(raw: Dict) -> Dict:
    """Normalize a single strike row from any broker's option chain response."""
    return {
        "strike":          _int(raw.get("strike") or raw.get("strikePrice") or 0),
        "call_ltp":        _float(raw.get("call_ltp") or raw.get("ce_ltp") or raw.get("CE", {}).get("lastPrice") or 0),
        "put_ltp":         _float(raw.get("put_ltp") or raw.get("pe_ltp") or raw.get("PE", {}).get("lastPrice") or 0),
        "call_oi":         _int(raw.get("call_oi") or raw.get("CE", {}).get("openInterest") or 0),
        "put_oi":          _int(raw.get("put_oi") or raw.get("PE", {}).get("openInterest") or 0),
        "call_iv":         _float(raw.get("call_iv") or raw.get("CE", {}).get("impliedVolatility") or 0),
        "put_iv":          _float(raw.get("put_iv") or raw.get("PE", {}).get("impliedVolatility") or 0),
        "call_volume":     _int(raw.get("call_volume") or raw.get("CE", {}).get("totalTradedVolume") or 0),
        "put_volume":      _int(raw.get("put_volume") or raw.get("PE", {}).get("totalTradedVolume") or 0),
        "call_bid":        _float(raw.get("call_bid") or raw.get("CE", {}).get("bidprice") or 0),
        "call_ask":        _float(raw.get("call_ask") or raw.get("CE", {}).get("askPrice") or 0),
        "put_bid":         _float(raw.get("put_bid") or raw.get("PE", {}).get("bidprice") or 0),
        "put_ask":         _float(raw.get("put_ask") or raw.get("PE", {}).get("askPrice") or 0),
        "call_oi_change":  _int(raw.get("call_oi_change") or raw.get("CE", {}).get("changeinOpenInterest") or 0),
        "put_oi_change":   _int(raw.get("put_oi_change") or raw.get("PE", {}).get("changeinOpenInterest") or 0),
    }


def normalize_option_chain(raw_list: List[Dict]) -> List[Dict]:
    return [normalize_option_row(r) for r in (raw_list or [])]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _float(val) -> float:
    try:
        return float(val) if val is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _int(val) -> int:
    try:
        return int(float(val)) if val is not None else 0
    except (TypeError, ValueError):
        return 0
