"""
Portfolio Intelligence Engine
══════════════════════════════
Generates an institutional-grade Portfolio Intelligence Report from the user's
equity holdings and pre-computed I-Scores stored in ResearchList.

Data sources (read-only, no fresh API calls):
  • ManualEquityHolding  — imported / manually entered equity positions
  • BrokerHolding        — live broker-synced positions
  • ResearchList         — cached I-Score component scores + sector (501 stocks)
  • UserFinancialMemory  — trading style, risk level, psychology notes
  • TraderProfile        — behavioural discipline / emotional scores

One Claude call (claude-haiku-4-5) generates all AI narrative sections.
Report is cached in-process for 15 minutes per user.
"""

import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── In-process report cache (15-min TTL) ─────────────────────────────────────
_REPORT_CACHE: Dict[int, Dict] = {}
_REPORT_CACHE_TTL = 900  # seconds

# ── Ideal sector allocation template for Indian equity portfolios ─────────────
IDEAL_ALLOCATION: Dict[str, float] = {
    'Banking':            20.0,
    'IT':                 20.0,
    'Capital Goods':      15.0,
    'Financial Services': 15.0,
    'Pharma':             10.0,
    'Consumption':        10.0,
    'Energy':              5.0,
    'Cash':                5.0,
}

# ResearchList sector → canonical display label
_SECTOR_MAP: Dict[str, str] = {
    'information technology':       'IT',
    'it':                           'IT',
    'technology':                   'IT',
    'software':                     'IT',
    'banking':                      'Banking',
    'bank':                         'Banking',
    'banks':                        'Banking',
    'private bank':                 'Banking',
    'financial services':           'Financial Services',
    'finance':                      'Financial Services',
    'nbfc':                         'Financial Services',
    'insurance':                    'Financial Services',
    'pharma':                       'Pharma',
    'pharmaceuticals':              'Pharma',
    'healthcare':                   'Pharma',
    'consumer staples':             'Consumption',
    'fmcg':                         'Consumption',
    'consumption':                  'Consumption',
    'consumer discretionary':       'Consumption',
    'retail':                       'Consumption',
    'capital goods':                'Capital Goods',
    'industrials':                  'Capital Goods',
    'infrastructure':               'Capital Goods',
    'engineering':                  'Capital Goods',
    'construction':                 'Capital Goods',
    'energy':                       'Energy',
    'oil':                          'Energy',
    'oil & gas':                    'Energy',
    'power':                        'Energy',
    'utilities':                    'Energy',
    'metals':                       'Metals',
    'metal':                        'Metals',
    'steel':                        'Metals',
    'mining':                       'Metals',
    'automobile':                   'Auto',
    'auto':                         'Auto',
    'automobile and auto components': 'Auto',
    'realty':                       'Realty',
    'real estate':                  'Realty',
    'cement':                       'Cement',
    'telecom':                      'Telecom',
    'telecommunications':           'Telecom',
    'media':                        'Media',
    'chemicals':                    'Chemicals',
}


def _normalise_sector(raw: Optional[str]) -> str:
    if not raw:
        return 'Other'
    return _SECTOR_MAP.get(raw.lower().strip(), raw.strip().title())


class PortfolioIntelligenceEngine:
    """
    Main report engine.  Call generate_report() to get the full report dict.
    The report is cached 15 minutes per user; call invalidate_cache() after
    an import or manual holding change to force regeneration.
    """

    def __init__(self, user_id: int):
        self.user_id = user_id

    # ── Public API ────────────────────────────────────────────────────────────

    def generate_report(self) -> Dict[str, Any]:
        """Return the full Portfolio Intelligence Report as a plain dict."""
        cached = _REPORT_CACHE.get(self.user_id)
        if cached and (time.time() - cached.get('_ts', 0)) < _REPORT_CACHE_TTL:
            return {k: v for k, v in cached.items() if k != '_ts'}

        holdings = self._load_holdings()
        if not holdings:
            report = self._empty_report()
            _REPORT_CACHE[self.user_id] = {**report, '_ts': time.time()}
            return report

        # ── Refresh live prices via Market Data Gateway ───────────────────────
        # Prices are applied in-place before any P&L or weight calculations.
        # The result is cached inside the 15-min report TTL so the gateway
        # isn't hammered on every page view.
        live_prices, price_source = self._fetch_live_prices(
            [h['symbol'] for h in holdings]
        )
        prices_ts = datetime.utcnow().strftime('%d %b %Y, %H:%M UTC')
        prices_covered = 0
        for h in holdings:
            lp = live_prices.get(h['symbol'])
            if lp and lp > 0:
                qty = h['quantity']
                inv = h['total_investment']
                h['current_price'] = lp
                h['current_value'] = round(qty * lp, 2)
                h['pnl_pct'] = round(
                    ((h['current_value'] - inv) / inv * 100) if inv > 0 else 0, 2
                )
                prices_covered += 1

        research  = self._load_research(holdings)
        memory    = self._load_behavioral()
        report    = self._build_report(holdings, research, memory)

        report['prices_updated_at'] = prices_ts
        report['price_source']      = price_source
        report['prices_covered']    = prices_covered
        report['prices_total']      = len(holdings)

        _REPORT_CACHE[self.user_id] = {**report, '_ts': time.time()}
        return report

    @classmethod
    def invalidate_cache(cls, user_id: int) -> None:
        _REPORT_CACHE.pop(user_id, None)

    # ── Live price refresh ─────────────────────────────────────────────────────

    def _fetch_live_prices(self, symbols: List[str]) -> tuple:
        """Batch-fetch live LTPs via the Market Data Gateway fallback chain.

        Returns:
            (prices_dict, source_string)
            prices_dict: symbol → float (only symbols with price > 0)
            source_string: dominant source label from the gateway
              (one of 'admin_broker', 'truedata', 'nse', 'yfinance', 'estimated',
               or 'unavailable' when the entire call fails)

        Hard-capped at 10 s so a slow yfinance / DNS-blocked NSEPython on Railway
        cannot stall the entire portfolio report (gunicorn default timeout = 30 s).
        """
        if not symbols:
            return {}, 'unavailable'

        import concurrent.futures as _cf

        def _do_fetch():
            from services.market_data_gateway import get_quotes
            return get_quotes(list(symbols), user_id=self.user_id)

        _ex = _cf.ThreadPoolExecutor(max_workers=1)
        _fut = _ex.submit(_do_fetch)
        try:
            result  = _fut.result(timeout=10)
            quotes  = result.get('quotes', {})
            prices  = {
                sym: float(q['price'])
                for sym, q in quotes.items()
                if float(q.get('price') or 0) > 0
            }
            source  = result.get('source', 'estimated')
            covered = len(prices)
            logger.info(
                f"portfolio_intelligence: live prices fetched "
                f"{covered}/{len(symbols)} symbols via {source}"
            )
            return prices, source
        except _cf.TimeoutError:
            logger.warning(
                f"portfolio_intelligence: live price fetch timed out after 10 s "
                f"for {len(symbols)} symbols — using stored prices"
            )
            return {}, 'unavailable'
        except Exception as exc:
            logger.warning(f"portfolio_intelligence: live price fetch failed: {exc}")
            return {}, 'unavailable'
        finally:
            _ex.shutdown(wait=False)

    # ── Data loaders ──────────────────────────────────────────────────────────

    def _load_holdings(self) -> List[Dict]:
        """Return unified list of active equity holding dicts."""
        from models import ManualEquityHolding
        rows: List[Dict] = []

        manual = ManualEquityHolding.query.filter_by(
            user_id=self.user_id, is_active=True
        ).all()
        for h in manual:
            cur = h.current_price or h.purchase_price or 0
            qty = h.quantity or 0
            inv = h.total_investment or (qty * (h.purchase_price or 0))
            val = h.current_value or (qty * cur)
            pnl = ((val - inv) / inv * 100) if inv > 0 else 0
            rows.append({
                'symbol':           h.symbol,
                'company_name':     h.company_name or h.symbol,
                'quantity':         qty,
                'purchase_price':   h.purchase_price or 0,
                'current_price':    cur,
                'total_investment': inv,
                'current_value':    val,
                'pnl_pct':          h.unrealized_pnl_percentage or pnl,
                'source':           'manual',
            })

        try:
            from models_broker import BrokerHolding, BrokerAccount
            accounts = BrokerAccount.query.filter_by(
                user_id=self.user_id, is_active=True
            ).all()
            if accounts:
                ids = [a.id for a in accounts]
                for h in BrokerHolding.query.filter(
                    BrokerHolding.broker_account_id.in_(ids)
                ).all():
                    qty = h.available_quantity or h.total_quantity or 0
                    inv = (h.avg_cost_price or 0) * qty
                    val = (h.current_price or h.avg_cost_price or 0) * qty
                    pnl = h.pnl_percentage or (
                        ((val - inv) / inv * 100) if inv > 0 else 0)
                    rows.append({
                        'symbol':           h.symbol,
                        'company_name':     h.company_name or h.symbol,
                        'quantity':         qty,
                        'purchase_price':   h.avg_cost_price or 0,
                        'current_price':    h.current_price or h.avg_cost_price or 0,
                        'total_investment': inv,
                        'current_value':    val,
                        'pnl_pct':          pnl,
                        'source':           'broker',
                    })
        except Exception as exc:
            logger.warning(f"portfolio_intelligence: broker holdings load failed: {exc}")

        return rows

    def _load_research(self, holdings: List[Dict]) -> Dict[str, Dict]:
        """Fetch ResearchList rows for all holding symbols. Returns symbol → data dict.

        Each returned dict includes two quality flags:
          • is_stale       — True when last_computed_at > 24 h ago (model.is_stale)
          • has_valid_score — True when i_score > 0 AND NOT stale
                              Only these holdings contribute to the PI score.
        """
        from models import ResearchList
        symbols = {h['symbol'] for h in holdings}
        rows = ResearchList.query.filter(ResearchList.symbol.in_(symbols)).all()
        result: Dict[str, Dict] = {}
        for r in rows:
            stale       = bool(r.is_stale)
            score       = float(r.i_score or 0)
            has_valid   = score > 0 and not stale
            result[r.symbol] = {
                'sector':               r.sector,
                'i_score':              score,
                'is_stale':             stale,
                'has_valid_score':      has_valid,
                'last_computed_at':     (
                    r.last_computed_at.strftime('%d %b %Y, %H:%M UTC')
                    if r.last_computed_at else None
                ),
                'recommendation':       r.recommendation or 'HOLD',
                'confidence':           float(r.confidence or 50),
                'qualitative_score':    float(r.qualitative_score or 0),
                'quantitative_score':   float(r.quantitative_score or 0),
                'search_score':         float(r.search_score or 0),
                'trend_score':          float(r.trend_score or 0),
                'risk_score':           float(r.risk_score or 0),
                'market_context_score': float(r.market_context_score or 0),
            }
        return result

    def _load_behavioral(self) -> Dict:
        result: Dict = {}
        try:
            from models import UserFinancialMemory, TraderProfile
            mem = UserFinancialMemory.query.filter_by(user_id=self.user_id).first()
            if mem:
                result.update({
                    'trading_style':   mem.trading_style,
                    'risk_level':      mem.risk_level,
                    'sectors':         mem.sectors,
                    'psychology_notes': mem.psychology_notes,
                    'goals':           mem.goals,
                })
            tp = TraderProfile.query.filter_by(user_id=self.user_id).first()
            if tp:
                result.update({
                    'trader_level':     tp.trader_level,
                    'discipline_score': tp.discipline_score,
                    'emotional_score':  tp.emotional_score,
                    'behavioural_risk': tp.behavioural_risk,
                })
        except Exception as exc:
            logger.warning(f"portfolio_intelligence: behavioral load failed: {exc}")
        return result

    # ── Core report builder ───────────────────────────────────────────────────

    def _build_report(self, holdings: List[Dict], research: Dict[str, Dict],
                      memory: Dict) -> Dict[str, Any]:
        total_inv  = sum(h['total_investment'] for h in holdings) or 1
        total_val  = sum(h['current_value'] for h in holdings)
        total_pnl  = total_val - total_inv
        pnl_pct    = (total_pnl / total_inv * 100) if total_inv > 0 else 0

        # ── Data-quality audit ────────────────────────────────────────────────
        # Three reasons a holding's I-Score may not be trustworthy:
        #   • missing   — no ResearchList row at all (small-cap, new listing)
        #   • unscored  — row exists but i_score is null/0 (scheduler not run yet)
        #   • stale     — row exists, score computed, but last_computed_at > 24 h
        # Holdings in any of these buckets are excluded from the PI score
        # weighted average and flagged with has_iscore=False on their cards.
        all_symbols     = {h['symbol'] for h in holdings}
        missing_symbols = all_symbols - set(research.keys())

        stale_symbols:    set = set()
        unscored_symbols: set = set()
        for sym, d in research.items():
            if d.get('is_stale'):
                stale_symbols.add(sym)
            elif not d.get('has_valid_score'):
                unscored_symbols.add(sym)

        not_covered     = missing_symbols | stale_symbols | unscored_symbols
        covered_count   = len(all_symbols) - len(not_covered)
        data_quality = {
            'covered':          covered_count,
            'total':            len(all_symbols),
            'missing_symbols':  sorted(missing_symbols),
            'stale_symbols':    sorted(stale_symbols),
            'unscored_symbols': sorted(unscored_symbols),
        }

        # Enrich each holding with sector + research scores + portfolio weight
        enriched: List[Dict] = []
        for h in holdings:
            r = research.get(h['symbol'], {})
            weight = (h['current_value'] / total_val * 100) if total_val > 0 else 0
            enriched.append({**h, 'sector': _normalise_sector(r.get('sector')),
                             'weight': round(weight, 2), 'research': r})

        # ── Sector allocation ─────────────────────────────────────────────────
        sector_alloc: Dict[str, float] = {}
        for h in enriched:
            s = h['sector']
            sector_alloc[s] = round(sector_alloc.get(s, 0) + h['weight'], 1)
        sector_alloc = dict(sorted(sector_alloc.items(), key=lambda x: -x[1]))

        current_sectors = list(sector_alloc.keys())
        missing_sectors = [s for s in IDEAL_ALLOCATION if s != 'Cash'
                           and s not in current_sectors]
        diversification_score = self._compute_diversification(sector_alloc)

        # ── Weighted sub-scores (by portfolio weight) ─────────────────────────
        def wavg(field: str, fallback: float = 50.0) -> float:
            total_w = sum(h['weight'] for h in enriched if h['research'].get(field, 0) > 0)
            if not total_w:
                return fallback
            return sum(h['research'].get(field, 0) * h['weight']
                       for h in enriched) / total_w

        fundamental_strength = wavg('qualitative_score')
        technical_strength   = wavg('quantitative_score')
        momentum_score       = wavg('search_score')
        trend_quality        = wavg('trend_score')
        raw_risk             = wavg('risk_score')
        market_ctx           = wavg('market_context_score')

        risk_display   = max(0.0, 100.0 - raw_risk)   # higher = safer
        quality_score  = (fundamental_strength + technical_strength + trend_quality) / 3
        valuation_score = market_ctx if market_ctx > 0 else 50.0
        max_single = max(sector_alloc.values()) if sector_alloc else 0
        stability_score = (risk_display + max(0, 100 - max_single * 1.5)) / 2

        # Portfolio Intelligence Score = weighted average of per-holding i_score.
        # Only holdings with a fresh, non-zero score contribute; stale, unscored,
        # and missing symbols are excluded so they don't distort the result.
        def _has_valid(r: Dict) -> bool:
            """Return True when the research dict has a trustworthy I-Score."""
            if 'has_valid_score' in r:
                return bool(r['has_valid_score'])
            # Backward-compat fallback for test mocks that omit the key:
            return r.get('i_score', 0) > 0 and not r.get('is_stale', False)

        total_w = sum(h['weight'] for h in enriched if _has_valid(h['research']))
        if total_w > 0:
            pi_score = sum(h['research'].get('i_score', 0) * h['weight']
                          for h in enriched if _has_valid(h['research'])) / total_w
        else:
            pi_score = 50.0

        sub_scores = {
            'fundamental_strength': round(fundamental_strength, 1),
            'technical_strength':   round(technical_strength, 1),
            'diversification':      round(diversification_score, 1),
            'risk_score':           round(risk_display, 1),
            'quality_score':        round(quality_score, 1),
            'valuation_score':      round(valuation_score, 1),
            'momentum_score':       round(momentum_score, 1),
            'stability':            round(stability_score, 1),
        }
        pi_score = round(pi_score, 1)

        # ── Snapshot ──────────────────────────────────────────────────────────
        winners = sum(1 for h in enriched if h['pnl_pct'] > 0)
        losers  = sum(1 for h in enriched if h['pnl_pct'] < 0)
        avg_beta = self._estimate_beta(raw_risk)

        snapshot = {
            'holdings_count':     len(enriched),
            'sectors_count':      len(sector_alloc),
            'winners':            winners,
            'losers':             losers,
            'overall_return_pct': round(pnl_pct, 2),
            'beta_label':        ('High' if avg_beta > 1.3 else
                                  'Medium' if avg_beta > 0.9 else 'Low'),
            'risk_level':        ('Aggressive' if raw_risk >= 60 else
                                  'Moderate' if raw_risk >= 35 else 'Conservative'),
            'cash_allocation_pct': 0,
        }

        # ── Risk radar ────────────────────────────────────────────────────────
        risk_radar = {
            'market_risk':          round(min(100, raw_risk * 1.2), 1),
            'sector_concentration': round(min(100, max_single * 1.1), 1),
            'liquidity_risk':       round(max(10, 50 - fundamental_strength * 0.3), 1),
            'fundamental_risk':     round(max(10, 100 - fundamental_strength), 1),
            'valuation_risk':       round(max(10, 100 - valuation_score), 1),
        }

        # ── Per-holding cards ─────────────────────────────────────────────────
        holding_cards: List[Dict] = []
        for h in enriched:
            r   = h['research']
            sym = h['symbol']
            # Determine data quality for this card:
            #   has_iscore=True  → fresh, non-zero I-Score (contributes to PI score)
            #   has_iscore=False → missing / stale / unscored (shows warning badge)
            valid     = _has_valid(r)
            is_stale  = sym in stale_symbols
            rec       = (r.get('recommendation') or 'HOLD').replace('_', ' ').upper()
            holding_cards.append({
                'symbol':           sym,
                'company_name':     h['company_name'],
                'sector':           h['sector'],
                'weight':           h['weight'],
                'pnl_pct':          round(h['pnl_pct'], 2),
                'has_iscore':       valid,
                'is_stale':         is_stale,
                # ai_rating is None when the score is missing/stale/unscored so
                # the template can display a "No I-Score data" badge instead.
                'ai_rating':        round(float(r['i_score']), 1) if valid else None,
                'scores': {
                    'fundamentals': round(r.get('qualitative_score', 0), 1),
                    'technicals':   round(r.get('quantitative_score', 0), 1),
                    'momentum':     round(r.get('search_score', 0), 1),
                    'valuation':    round(r.get('market_context_score', 0), 1),
                    'risk':         round(r.get('risk_score', 0), 1),
                },
                'recommendation':    rec,
                'recommendation_raw': r.get('recommendation', 'HOLD'),
                'confidence':        round(r.get('confidence', 50), 1),
                'ai_opinion':        [],   # filled in after Claude call
            })

        # ── Stress test ───────────────────────────────────────────────────────
        drawdown = round(-10.0 * avg_beta, 1)
        base_r   = int(50 - raw_risk * 0.3)
        stress_test = {
            'scenario':    'Nifty Falls 10%',
            'drawdown_pct': drawdown,
            'recovery': [
                {'period': '3 Months',  'probability': max(20, min(65, base_r))},
                {'period': '6 Months',  'probability': max(40, min(80, base_r + 20))},
                {'period': '12 Months', 'probability': max(60, min(95, base_r + 40))},
            ],
        }

        # ── Behavioural ───────────────────────────────────────────────────────
        behavioral = self._build_behavioral(memory, sector_alloc, enriched)

        # ── Forecast ─────────────────────────────────────────────────────────
        base_return = round(pi_score * 0.18 - 4, 1)  # 50→5%, 70→8.6%, 80→10.4%
        forecast = {
            'bear':       round(base_return - 10, 1),
            'base':       base_return,
            'bull':       round(base_return + 13, 1),
            'confidence': round(min(90, 40 + pi_score * 0.5), 1),
        }

        # ── Action centre ─────────────────────────────────────────────────────
        action_centre = self._build_actions(
            sector_alloc, diversification_score, sub_scores, enriched)

        # ── Score status ──────────────────────────────────────────────────────
        score_status = self._score_status(pi_score, diversification_score)

        # ── AI narrative (one Claude call) ────────────────────────────────────
        ai = self._generate_ai_narrative(
            pi_score, sub_scores, snapshot, sector_alloc,
            missing_sectors, holding_cards, memory
        )
        for card in holding_cards:
            card['ai_opinion'] = ai.get('holdings_opinion', {}).get(card['symbol'], [])

        return {
            'has_holdings':               True,
            'total_holdings':             len(enriched),
            'total_investment':           round(total_inv, 2),
            'total_current_value':        round(total_val, 2),
            'total_pnl':                  round(total_pnl, 2),
            'pnl_pct':                    round(pnl_pct, 2),
            'portfolio_intelligence_score': pi_score,
            'sub_scores':                 sub_scores,
            'score_status':               score_status,
            'executive_summary':          ai.get('executive_summary', ''),
            'snapshot':                   snapshot,
            'sector_allocation':          sector_alloc,
            'ideal_allocation':           IDEAL_ALLOCATION,
            'diversification_score':      round(diversification_score, 1),
            'risk_radar':                 risk_radar,
            'holdings':                   holding_cards,
            'current_sectors':            current_sectors,
            'missing_sectors':            missing_sectors,
            'stress_test':                stress_test,
            'behavioral':                 behavioral,
            'watchlist_suggestions':      ai.get('watchlist_suggestions', []),
            'action_centre':              action_centre,
            'forecast':                   forecast,
            'data_quality':               data_quality,
            'overall_verdict': {
                'score':          pi_score,
                'strengths':      ai.get('strengths', []),
                'weaknesses':     ai.get('weaknesses', []),
                'recommendation': ai.get('overall_recommendation', ''),
            },
            'generated_at': datetime.utcnow().strftime('%d %b %Y, %H:%M UTC'),
        }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _compute_diversification(self, sector_alloc: Dict[str, float]) -> float:
        if not sector_alloc:
            return 0.0
        ideal_sectors = {s for s in IDEAL_ALLOCATION if s != 'Cash'}
        covered = len(ideal_sectors & set(sector_alloc.keys()))
        coverage_score = covered / len(ideal_sectors) * 50   # 0-50
        max_alloc = max(sector_alloc.values())
        concentration_penalty = max(0, max_alloc - 30) * 0.6
        count_bonus = min(10, len(sector_alloc) * 2)
        return round(max(0.0, min(100.0,
            coverage_score + count_bonus - concentration_penalty)), 1)

    def _estimate_beta(self, raw_risk: float) -> float:
        """Approximate portfolio beta from risk score (0→0.6, 50→1.1, 100→1.6)."""
        return round(0.6 + (raw_risk / 100) * 1.0, 2)

    def _build_behavioral(self, memory: Dict, sector_alloc: Dict,
                           enriched: List[Dict]) -> Dict:
        n = len(enriched)
        style_map = {
            'long_term':  'Long-Term Investor',
            'positional': 'Positional Trader',
            'swing':      'Swing Trader',
            'intraday':   'Intraday Trader',
        }
        style = memory.get('trading_style', '')
        label = style_map.get(style, 'Balanced Investor')
        if n <= 3:
            label = 'Concentrated Investor'

        traits = []
        traits.append('Concentrated positions' if n <= 4 else 'Diversified holding count')
        if style in ('long_term', '') :
            traits.append('Long-term holding tendency')
        risk = memory.get('risk_level', '')
        if risk:
            traits.append(f'{risk.title()} risk tolerance')
        if len(sector_alloc) <= 2:
            traits.append('Sector-focused investing')
        if len(sector_alloc) >= 5:
            traits.append('Broad sector exposure')

        if len(sector_alloc) <= 2:
            improvement = ('Increase diversification across uncorrelated sectors '
                           'to reduce drawdowns during sector-specific downturns.')
        elif len(enriched) <= 3:
            improvement = ('Add 2-3 more holdings to lower single-stock concentration risk.')
        else:
            improvement = ('Consider trimming overweight positions and '
                           'rebalancing into underweight target sectors.')

        return {'investor_style': label, 'traits': traits[:4],
                'improvement': improvement}

    def _build_actions(self, sector_alloc: Dict, div_score: float,
                       sub_scores: Dict, enriched: List[Dict]) -> List[Dict]:
        actions = []
        if div_score < 50:
            actions.append({'priority': 'High',
                            'action': 'Increase sector diversification', 'impact': 5})
        if len(enriched) <= 3:
            actions.append({'priority': 'High',
                            'action': 'Add 2-3 holdings to reduce concentration risk', 'impact': 5})
        if sub_scores.get('momentum_score', 50) < 50:
            actions.append({'priority': 'Medium',
                            'action': 'Review holdings with weak momentum', 'impact': 4})
        actions.append({'priority': 'Medium',
                        'action': 'Review positions monthly against I-Score updates', 'impact': 4})
        if sub_scores.get('risk_score', 50) < 50:
            actions.append({'priority': 'Medium',
                            'action': 'Add defensive sector exposure (Pharma / FMCG)', 'impact': 4})
        actions.append({'priority': 'Low',
                        'action': 'Maintain 5% cash buffer for new opportunities', 'impact': 3})
        return actions[:5]

    def _score_status(self, pi: float, div: float) -> str:
        if pi >= 75 and div >= 60:
            return 'Well Diversified & Strong'
        if pi >= 65 and div < 50:
            return 'Healthy but Needs Diversification'
        if pi >= 55:
            return 'Moderate — Review Weak Positions'
        return 'Needs Attention — Consider Rebalancing'

    def _generate_ai_narrative(self, pi_score: float, sub_scores: Dict,
                                snapshot: Dict, sector_alloc: Dict,
                                missing_sectors: List[str],
                                holding_cards: List[Dict],
                                memory: Dict) -> Dict:
        # Guard: skip if no LLM is reachable (no API key configured)
        if not os.environ.get('ANTHROPIC_API_KEY') and not os.environ.get('LLM_PROVIDER'):
            return self._fallback_narrative(holding_cards, missing_sectors,
                                            sub_scores, pi_score, snapshot)

        holdings_lines = '\n'.join(
            f"  {h['symbol']} (sector:{h['sector']}, I-Score:{h['ai_rating']}, "
            f"rec:{h['recommendation']}, conf:{h['confidence']}%, wt:{h['weight']}%)"
            for h in holding_cards
        )
        sectors_str = ', '.join(f"{k}:{v}%" for k, v in list(sector_alloc.items())[:6])
        style  = memory.get('trading_style') or 'unknown'
        risk   = memory.get('risk_level') or 'moderate'

        prompt = (
            "You are an institutional Indian equity portfolio analyst. "
            "Analyse this portfolio and return ONLY valid JSON — no markdown, no prose outside the JSON.\n\n"
            f"PORTFOLIO METRICS:\n"
            f"  Intelligence Score: {pi_score}/100\n"
            f"  Fundamental: {sub_scores['fundamental_strength']}, "
            f"Technical: {sub_scores['technical_strength']}, "
            f"Diversification: {sub_scores['diversification']}/100\n"
            f"  Holdings: {snapshot['holdings_count']}, "
            f"Sectors: {snapshot['sectors_count']}, "
            f"Return: {snapshot['overall_return_pct']}%\n"
            f"  Investor style: {style}, Risk: {risk}\n\n"
            f"HOLDINGS:\n{holdings_lines}\n\n"
            f"SECTOR ALLOCATION: {sectors_str}\n"
            f"MISSING SECTORS: {', '.join(missing_sectors) or 'None'}\n\n"
            "Return exactly this JSON schema (all fields required, no extras):\n"
            '{\n'
            '  "executive_summary": "3 sentences on health, key risk, and one actionable insight. Name the actual holdings.",\n'
            '  "holdings_opinion": {\n'
            '    "SYMBOL": [\n'
            '      {"icon": "✅", "text": "strength in under 12 words"},\n'
            '      {"icon": "⚠", "text": "risk or weakness in under 12 words"}\n'
            '    ]\n'
            '  },\n'
            '  "watchlist_suggestions": ["3-4 sector/theme ideas to fill gaps, 5-8 words each"],\n'
            '  "strengths": ["3 portfolio strengths, 8-12 words each"],\n'
            '  "weaknesses": ["3 portfolio weaknesses, 8-12 words each"],\n'
            '  "overall_recommendation": "2-sentence final recommendation. Be specific and actionable."\n'
            '}'
        )

        import concurrent.futures as _cf_n

        def _do_llm():
            from services.llm_client import get_llm_client, Model
            llm = get_llm_client()
            return llm.structured_output(
                [{'role': 'user', 'content': prompt}],
                max_tokens=1400,
                temperature=0.2,
                model=Model.FAST,
            )

        _ex_n = _cf_n.ThreadPoolExecutor(max_workers=1)
        _fut_n = _ex_n.submit(_do_llm)
        try:
            result = _fut_n.result(timeout=18)
            if result:
                return result
            raise ValueError('empty structured_output')
        except _cf_n.TimeoutError:
            logger.warning("portfolio_intelligence: AI narrative timed out after 18 s — using fallback")
            return self._fallback_narrative(holding_cards, missing_sectors,
                                            sub_scores, pi_score, snapshot)
        except Exception as exc:
            logger.warning(f"portfolio_intelligence: LLM call failed: {exc}")
            return self._fallback_narrative(holding_cards, missing_sectors,
                                            sub_scores, pi_score, snapshot)
        finally:
            _ex_n.shutdown(wait=False)

    def _fallback_narrative(self, holding_cards: List[Dict],
                             missing_sectors: List[str],
                             sub_scores: Dict, pi_score: float,
                             snapshot: Dict) -> Dict:
        n = snapshot.get('holdings_count', len(holding_cards))
        opinions: Dict[str, List] = {}
        for h in holding_cards:
            ops = []
            if h['scores']['fundamentals'] >= 60:
                ops.append({'icon': '✅', 'text': 'Solid fundamental profile'})
            elif h['scores']['fundamentals'] > 0:
                ops.append({'icon': '⚠', 'text': 'Fundamentals need monitoring'})
            if h['scores']['technicals'] < 55 and h['scores']['technicals'] > 0:
                ops.append({'icon': '⚠', 'text': 'Technical momentum is weak'})
            elif h['scores']['technicals'] >= 65:
                ops.append({'icon': '✅', 'text': 'Strong technical trend'})
            if not ops:
                ops.append({'icon': '✅', 'text': 'Run i-Score in chat for detailed analysis'})
            opinions[h['symbol']] = ops[:2]

        return {
            'executive_summary': (
                f"Your portfolio of {n} holding{'s' if n != 1 else ''} scores "
                f"{pi_score}/100 on the Portfolio Intelligence Index. "
                f"{'Diversification needs improvement — consider adding uncorrelated sectors.' if sub_scores['diversification'] < 50 else 'Diversification is adequate for the number of holdings.'} "
                f"{'Fundamental quality is strong across holdings.' if sub_scores['fundamental_strength'] >= 65 else 'Review I-Scores for individual holdings in chat.'}"
            ),
            'holdings_opinion': opinions,
            'watchlist_suggestions': [
                f"Large-cap {s} stocks for diversification"
                for s in missing_sectors[:3]
            ] or ['Run I-Score in chat for personalised suggestions'],
            'strengths': [
                f"Portfolio tracked with AI-powered I-Score methodology",
                f"{'Strong fundamental quality across holdings' if sub_scores['fundamental_strength'] >= 65 else 'Holdings selected with long-term perspective'}",
                f"{'Good sector breadth' if sub_scores['diversification'] >= 50 else 'Discipline in maintaining focused positions'}",
            ],
            'weaknesses': [
                f"Only {n} holding{'s' if n != 1 else ''} — single-stock concentration risk elevated",
                f"{'Diversification below recommended threshold' if sub_scores['diversification'] < 50 else 'Some ideal sectors underrepresented'}",
                "I-Score cache cold for some holdings — chat analysis recommended",
            ],
            'overall_recommendation': (
                f"Continue holding current positions while monitoring via the I-Score tool in chat. "
                f"{'Add positions in ' + ', '.join(missing_sectors[:2]) + ' to improve diversification.' if missing_sectors else 'Portfolio is reasonably positioned — focus on regular rebalancing.'}"
            ),
        }

    def _empty_report(self) -> Dict[str, Any]:
        return {
            'has_holdings': False,
            'generated_at': datetime.utcnow().strftime('%d %b %Y, %H:%M UTC'),
        }
