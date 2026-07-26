"""
Zerodha MF Service — enriches mutual fund data using the Kite Connect
/mf/instruments endpoint from the admin Zerodha broker pool slot.

Priority:
  1. Kite /mf/instruments  → current NAV, scheme metadata, purchase/redemption
                             status, min purchase amount, settlement type.
  2. mfapi.in             → historical NAV series for return/CAGR/risk calcs.

Falls back gracefully to mfapi-only when no admin Zerodha slot is configured
or its access token has expired.
"""

import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def _get_admin_zerodha():
    """
    Return a (ZerodhaBroker instance, row) from the admin data-broker pool,
    specifically the Zerodha slot. Returns (None, None) if unavailable.
    """
    try:
        from services.admin_data_broker_pool import get_admin_brokers_by_runway
        for priority, broker_type, broker_name, broker, row in get_admin_brokers_by_runway():
            if broker_type.lower() == "zerodha":
                return broker, row
    except Exception as e:
        logger.warning(f"zerodha_mf_service: could not access admin pool: {e}")
    return None, None


def get_mf_data(fund_query: str) -> Dict[str, Any]:
    """
    Main entry point.  Returns a dict ready to be placed in card_data.

    Fields always present (from mfapi):
        scheme_name, fund_house, scheme_category, scheme_type,
        current_nav, nav_date,
        returns_1y, returns_3y, returns_5y,
        cagr_1y, cagr_3y, cagr_5y,
        volatility, sharpe_ratio, annualized_return,
        data_source  ('kite+mfapi' | 'mfapi')

    Extra fields when Kite instruments are available:
        amc, fund_type, fund_category, scheme_plan,
        purchase_allowed, redemption_allowed,
        min_purchase_amount, settlement_type,
        kite_nav, kite_nav_date, kite_tradingsymbol
    """
    result: Dict[str, Any] = {"data_source": "mfapi"}

    # ── Step 1: mfapi for search + historical returns ─────────────────────
    from services.mfapi_service import MFApiService
    mfsvc = MFApiService()
    mf_results = mfsvc.search_fund(fund_query)
    if not mf_results:
        return {}   # caller handles "not found"

    # Prefer Direct Growth from mfapi results
    direct_growth = [f for f in mf_results
                     if "DIRECT" in f.get("schemeName", "").upper()
                     and "GROWTH" in f.get("schemeName", "").upper()]
    best_mfapi = direct_growth[0] if direct_growth else mf_results[0]
    scheme_code = best_mfapi.get("schemeCode")

    details = mfsvc.get_fund_details(scheme_code) if scheme_code else {}
    if not details.get("success"):
        return {}

    # Populate from mfapi
    result.update({
        "scheme_name":       details.get("scheme_name", best_mfapi.get("schemeName", "")),
        "fund_house":        details.get("fund_house", ""),
        "scheme_category":   details.get("scheme_category", ""),
        "scheme_type":       details.get("scheme_type", ""),
        "current_nav":       details.get("current_nav", 0),
        "nav_date":          details.get("nav_date", ""),
        # returns keys match what the card template reads
        "returns_1y":        details.get("return_1y"),
        "returns_3y":        details.get("return_3y"),
        "returns_5y":        details.get("return_5y"),
        "cagr_1y":           details.get("cagr_1y"),
        "cagr_3y":           details.get("cagr_3y"),
        "cagr_5y":           details.get("cagr_5y"),
        "volatility":        details.get("volatility"),
        "sharpe_ratio":      details.get("sharpe_ratio"),
        "annualized_return": details.get("annualized_return"),
        "mfapi_scheme_code": scheme_code,
    })

    # ── Step 2: Zerodha Kite enrichment ───────────────────────────────────
    try:
        broker, row = _get_admin_zerodha()
        if broker is not None:
            kite_matches = broker.search_mf(fund_query)
            if not kite_matches:
                # Try with just the first two meaningful words from the mfapi name
                words = [w for w in result["scheme_name"].split()
                         if w.lower() not in {"fund", "direct", "regular", "growth",
                                               "idcw", "dividend", "plan", "option"}]
                fallback_query = " ".join(words[:3])
                kite_matches = broker.search_mf(fallback_query)

            if kite_matches:
                k = kite_matches[0]
                # Parse nav safely
                try:
                    kite_nav = float(k["last_price"]) if k.get("last_price") else None
                except (ValueError, TypeError):
                    kite_nav = None

                # Parse min purchase
                try:
                    min_purchase = float(k["minimum_purchase_amount"]) if k.get("minimum_purchase_amount") else None
                except (ValueError, TypeError):
                    min_purchase = None

                # If Kite NAV is more recent, use it
                if kite_nav and kite_nav > 0:
                    result["current_nav"]  = kite_nav
                    result["nav_date"]     = k.get("last_price_date", result["nav_date"])
                    result["kite_nav"]     = kite_nav
                    result["kite_nav_date"] = k.get("last_price_date", "")

                result.update({
                    "data_source":         "kite+mfapi",
                    "amc":                 k.get("amc", result["fund_house"]),
                    "fund_type":           k.get("fund_type", ""),
                    # Prefer Kite's fund_category; mfapi's scheme_category as fallback
                    "fund_category":       k.get("fund_category") or result.get("scheme_category", ""),
                    "scheme_plan":         k.get("scheme_plan", ""),       # Direct / Regular
                    "purchase_allowed":    k.get("purchase_allowed", ""),
                    "redemption_allowed":  k.get("redemption_allowed", ""),
                    "min_purchase_amount": min_purchase,
                    "settlement_type":     k.get("settlement_type", ""),
                    "kite_tradingsymbol":  k.get("tradingsymbol", ""),
                })
                # Use Kite AMC as fund_house if it came through
                if result["amc"]:
                    result["fund_house"] = result["amc"]

                logger.info(
                    f"zerodha_mf_service: enriched '{result['scheme_name']}' "
                    f"with Kite data (NAV={kite_nav}, plan={k.get('scheme_plan')})"
                )
    except Exception as e:
        logger.warning(f"zerodha_mf_service: Kite enrichment failed, using mfapi only: {e}")

    return result
