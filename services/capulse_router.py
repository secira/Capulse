"""
Capulse Router — Claude-based intent classifier and engine dispatcher.
Routes natural-language chat messages to the appropriate analysis engine.
"""
import os
import json
import logging
import time
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

INTENTS = {
    'ISCORE':      'i-Score lookup for a specific stock or company',
    'FNO_SIGNAL':  'F&O signals, NIFTY/BANKNIFTY/FINNIFTY levels, options probability, futures analysis',
    'MUTUAL_FUND': 'Mutual fund analysis, NAV, fund comparison, scheme lookup',
    'PORTFOLIO':   'Portfolio analysis, holdings review, sector concentration, rebalancing, my stocks',
    'BEHAVIOUR':   'Behavioural analysis, trading psychology, discipline score, emotional patterns, biases, revenge trading, overtrading',
    'GENERAL':     'General market questions, concepts, education, anything else',
}

INTENT_CLASSIFIER_PROMPT = """You are an intent classifier for Capulse, an AI stock research chat for Indian markets.

Classify the user's message into exactly ONE of these intents:
- ISCORE: User wants an i-Score, rating, or fundamental analysis for a specific stock/company (e.g. "i-Score for Reliance", "rate TCS", "score HDFC Bank", "how is Infosys")
- FNO_SIGNAL: User wants F&O trade signals, NIFTY/BANKNIFTY/FINNIFTY/SENSEX analysis, options probability, premium decay, OI analysis (e.g. "NIFTY signals", "BANKNIFTY setup", "probability of 24000", "F&O outlook today")
- MUTUAL_FUND: User wants mutual fund info, NAV, fund comparison, SIP analysis (e.g. "Parag Parikh fund NAV", "best mid cap fund", "HDFC flexi cap returns")
- PORTFOLIO: User wants analysis of THEIR OWN portfolio/holdings, sector concentration, rebalancing, portfolio health (e.g. "my portfolio", "my holdings", "how is my portfolio", "sector concentration", "rebalance")
- BEHAVIOUR: User wants analysis of THEIR OWN trading behaviour, psychology, bias detection, discipline score, pattern analysis (e.g. "my behaviour score", "am I overtrading", "trading psychology", "my biases", "revenge trading", "trading patterns", "my trading discipline", "analyse my trades")
- GENERAL: Everything else — market education, concepts, news, how things work, strategy questions

Also extract:
- symbol: NSE ticker if a specific stock was mentioned (e.g. "RELIANCE", "TCS", "HDFCBANK") — null otherwise
- fund_query: Fund name/keyword if a mutual fund was mentioned — null otherwise
- index: "NIFTY", "BANKNIFTY", "FINNIFTY", or "SENSEX" if mentioned (default "NIFTY" for FNO_SIGNAL)
- level: Numeric price level mentioned for F&O (e.g. 24000) — null otherwise

Respond with ONLY valid JSON:
{"intent": "INTENT_NAME", "symbol": "SYMBOL_OR_NULL", "fund_query": "FUND_NAME_OR_NULL", "index": "INDEX_OR_NULL", "level": null_or_number, "confidence": 0.0_to_1.0}"""


def classify_intent(message: str, conversation_history: list = None) -> Dict[str, Any]:
    """
    Classify user message intent using Claude haiku.
    Falls back to GENERAL if classification fails.
    """
    try:
        api_key = os.environ.get('ANTHROPIC_API_KEY', '')
        if not api_key:
            return {'intent': 'GENERAL', 'symbol': None, 'fund_query': None, 'index': None, 'level': None, 'confidence': 0.5}

        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        # Build context from recent history
        context = ''
        if conversation_history:
            recent = conversation_history[-4:]
            context = '\n'.join([f"{m['role'].upper()}: {m['content'][:200]}" for m in recent])
            context = f"\n\nRecent conversation:\n{context}"

        response = client.messages.create(
            model='claude-haiku-4-5',
            max_tokens=200,
            messages=[{
                'role': 'user',
                'content': f"{INTENT_CLASSIFIER_PROMPT}{context}\n\nUser message: {message}"
            }]
        )
        text = response.content[0].text.strip()
        # Strip markdown code fences if Claude wraps the JSON
        if text.startswith('```'):
            text = text.split('```', 2)[1]          # drop opening fence line
            if text.startswith('json'):
                text = text[4:]                      # strip the 'json' language tag
            text = text.rsplit('```', 1)[0].strip()  # drop closing fence
        result = json.loads(text)
        if result.get('confidence', 1.0) < 0.6:
            result['intent'] = 'GENERAL'
        return result

    except Exception as e:
        logger.warning(f"Intent classification failed: {e}")
        return {'intent': 'GENERAL', 'symbol': None, 'fund_query': None, 'index': None, 'level': None, 'confidence': 0.5}


def handle_iscore(symbol: str, user_id: int) -> Dict[str, Any]:
    """Get i-Score for a stock symbol using IScoreWorkflow."""
    try:
        if not symbol:
            return {
                'card_type': 'prose',
                'content': "Which stock would you like an i-Score for? Try: **i-Score for Reliance**, **rate TCS**, or **score HDFC Bank**."
            }

        from services.workflow_iscore import IScoreWorkflow
        wf = IScoreWorkflow()
        result = wf.calculate_iscore(symbol.upper(), 'stocks', user_id)

        res = result.get('results', {})
        overall_score = res.get('overall_score') or 0
        recommendation = res.get('recommendation', 'HOLD')
        summary = res.get('recommendation_summary', '')

        # Build component breakdown (quant 50%, qual 15%, sentiment 10%, trend 25%)
        components = {}
        for label, key in [('Quantitative', 'quantitative'), ('Trend', 'trend'),
                           ('Qualitative', 'qualitative'), ('Sentiment', 'search')]:
            s = (res.get(key) or {}).get('score')
            if s is not None:
                components[label] = round(float(s))

        if overall_score > 0:
            return {
                'card_type': 'iscore',
                'content': f"Here's the current i-Score for **{symbol.upper()}**:",
                'card_data': {
                    'symbol': symbol.upper(),
                    'score': round(overall_score, 1),
                    'components': components,
                    'recommendation': recommendation,
                    'summary': summary,
                }
            }

        return {
            'card_type': 'prose',
            'content': (
                f"I couldn't compute an i-Score for **{symbol.upper()}** right now — "
                f"price data may be temporarily unavailable. "
                f"Check the ticker is correct (e.g. RELIANCE, TCS, HDFCBANK) and try again."
            )
        }

    except Exception as e:
        logger.error(f"i-Score error for {symbol}: {e}", exc_info=True)
        return {
            'card_type': 'prose',
            'content': (
                f"The i-Score engine hit an error for **{symbol.upper()}**. "
                f"Please try again in a moment."
            )
        }


def handle_fno_signal(index: str, level: Optional[float], user_id: int) -> Dict[str, Any]:
    """Get F&O analysis for NIFTY/BANKNIFTY using NiftyOptionsEngine.generate_analysis()."""
    try:
        idx = (index or 'NIFTY').upper()
        from services.nifty_options_engine import NiftyOptionsEngine
        engine = NiftyOptionsEngine(index=idx, user_id=user_id)
        analysis = engine.generate_analysis()

        spot            = analysis.get('spot_price') or 0
        atm             = analysis.get('atm_strike') or 0
        trade_direction = analysis.get('trade_direction', 'NEUTRAL')
        final_decision  = analysis.get('final_decision', 'WAIT')
        confidence      = analysis.get('confidence') or 0
        confidence_grade = analysis.get('confidence_grade', '')
        is_blocked      = analysis.get('is_blocked', False)
        block_reasons   = analysis.get('block_reasons', [])
        trades          = analysis.get('trades', [])
        data_source     = analysis.get('data_source', 'estimated')

        # Format trades into signal-card dicts
        signals = []
        for t in trades[:3]:
            signals.append({
                'strike':      t.get('strike'),
                'option_type': t.get('type', ''),          # CE / PE
                'direction':   t.get('action', 'BUY'),     # BUY / SELL
                'entry':       t.get('entry_price'),
                'stop_loss':   t.get('sl'),
                'target':      t.get('target'),
                'confidence':  t.get('confidence', 0),
                'label':       t.get('label', ''),
                'risk_reward': t.get('risk_reward', ''),
                'ltp':         t.get('ltp'),
            })

        spot_str = f"₹{spot:,.2f}" if spot else "—"

        # Time-window caution from the engine (never a block — just a warning)
        time_check     = analysis.get('time_filter', {})
        time_caution   = time_check.get('caution', False)
        time_reason    = time_check.get('reason', '')

        # Build a plain-English summary line
        if is_blocked or final_decision in ('NO TRADE', 'WAIT', 'AVOID'):
            reasons_md = '\n'.join(f"- {r}" for r in block_reasons[:4]) if block_reasons else ""
            parts = [f"**{idx}** · Spot: {spot_str} · ATM: {atm}"]
            if trade_direction and trade_direction not in ('NEUTRAL', ''):
                parts.append(f"Bias: **{trade_direction}**")
            parts.append(f"Signal: **{final_decision}**")
            if reasons_md:
                parts.append(f"\nWhy no trade right now:\n{reasons_md}")
            content = "  \n".join(parts)
        else:
            content = (
                f"**{idx}** F&O signals · Spot: {spot_str} · ATM: {atm} · "
                f"Bias: **{trade_direction}** · Confidence: **{confidence_grade}** ({confidence}%)"
            )

        # Append volatility warning when outside the core 10 AM–3:30 PM window
        if time_caution and time_reason:
            content += f"\n\n⚠️ **Volatility note:** {time_reason} Trade based on your own risk tolerance."

        return {
            'card_type': 'fno_signals',
            'content': content,
            'card_data': {
                'index':            idx,
                'spot':             spot,
                'atm':              atm,
                'trade_direction':  trade_direction,
                'final_decision':   final_decision,
                'confidence':       confidence,
                'confidence_grade': confidence_grade,
                'is_blocked':       is_blocked,
                'signals':          signals,
                'data_source':      data_source,
                'time_caution':     time_caution,
                'time_reason':      time_reason,
            }
        }

    except Exception as e:
        logger.error(f"F&O signal error for {index}: {e}", exc_info=True)
        return _fno_fallback(index)


def _fno_fallback(index: str) -> Dict[str, Any]:
    idx = (index or 'NIFTY').upper()
    return {
        'card_type': 'prose',
        'content': (
            f"The F&O engine for **{idx}** analyses option chain OI, IV, VWAP, RSI, "
            f"Supertrend, and EMA momentum to generate ranked trade signals.\n\n"
            f"Signals are available 24/7 — use them for planning anytime. "
            f"During market hours (9:15 AM – 3:30 PM IST) live option chain data is used; "
            f"outside hours, signals are based on last known prices. Always set your own risk parameters."
        )
    }


def handle_mutual_fund(fund_query: str, message: str) -> Dict[str, Any]:
    """Fetch mutual fund data using MFApi."""
    try:
        query = fund_query or message
        if not query:
            return {'card_type': 'prose', 'content': "Which mutual fund would you like to analyse? Try: **Parag Parikh Flexi Cap**, **HDFC Mid Cap Opportunities**, or **SBI Small Cap Fund**."}

        from services.mfapi_service import MFApiService
        svc = MFApiService()
        results = svc.search_fund(query)

        if not results:
            return {
                'card_type': 'prose',
                'content': f"I couldn't find a mutual fund matching **\"{query}\"**. Try a more specific name, e.g. \"HDFC Mid Cap Opportunities\" or \"Axis Bluechip Fund\"."
            }

        # Take the best match
        top = results[0]
        scheme_code = top.get('schemeCode')
        if not scheme_code:
            return {'card_type': 'prose', 'content': f"Found fund **{top.get('schemeName', query)}** but could not retrieve its details. Please try again."}

        details = svc.get_fund_details(scheme_code)
        if not details.get('success'):
            return {'card_type': 'prose', 'content': f"Couldn't load data for **{top.get('schemeName', query)}** right now. MFApi may be temporarily unavailable."}

        return {
            'card_type': 'mutual_fund',
            'content': f"Here's the fund snapshot for **{details.get('scheme_name', query)}**:",
            'card_data': {
                'scheme_name': details.get('scheme_name', ''),
                'fund_house': details.get('fund_house', ''),
                'scheme_category': details.get('scheme_category', ''),
                'scheme_type': details.get('scheme_type', ''),
                'current_nav': details.get('current_nav', 0),
                'nav_date': details.get('nav_date', ''),
                'returns_1y': details.get('returns_1y'),
                'returns_3y': details.get('returns_3y'),
                'returns_5y': details.get('returns_5y'),
            }
        }

    except Exception as e:
        logger.error(f"Mutual fund error: {e}")
        return {
            'card_type': 'prose',
            'content': "I can look up NAV, returns, and category details for any Indian mutual fund. Try asking: **\"Parag Parikh Flexi Cap NAV\"** or **\"compare HDFC and ICICI mid cap funds\"**."
        }


def handle_portfolio(user_id: int) -> Dict[str, Any]:
    """Full portfolio analysis — sector breakdown, risk, holdings, AI narrative."""
    try:
        from models import Portfolio
        holdings_check = Portfolio.query.filter_by(user_id=user_id).first()
        if not holdings_check:
            return {
                'card_type': 'prose',
                'content': (
                    "You haven't added any holdings yet.\n\n"
                    "Upload your holdings CSV using the **+** button in the chat, "
                    "or add stocks manually via **My Holdings**. "
                    "Once added, I'll give you sector concentration, risk metrics, "
                    "diversification gaps, and AI-powered rebalancing suggestions."
                )
            }

        from services.portfolio_analyzer_service import PortfolioAnalyzerService
        service = PortfolioAnalyzerService(user_id)
        result  = service.analyze_portfolio()

        if not result or not result.get('success'):
            raise ValueError(result.get('error', 'Empty result'))

        analysis        = result['analysis']
        summary         = analysis.get('portfolio_summary', {})
        sector_alloc    = analysis.get('sector_allocation', {})
        top_holdings    = analysis.get('top_holdings', [])
        risk_metrics    = analysis.get('risk_metrics', {})
        ai_assessment   = analysis.get('ai_assessment', {})

        # Sort sectors by allocation descending
        sectors_sorted = sorted(sector_alloc.items(), key=lambda x: x[1], reverse=True)

        # AI narrative from portfolio assessment
        narrative = None
        try:
            from services.anthropic_service import AnthropicService
            total_val   = summary.get('total_value', 0)
            total_pnl   = summary.get('total_pnl', 0)
            pnl_pct     = summary.get('total_pnl_percentage', 0)
            n_holdings  = summary.get('holdings_count', 0)
            health_sc   = ai_assessment.get('health_score', 75)
            risk_level  = ai_assessment.get('risk_level', 'Medium')
            conc_idx    = risk_metrics.get('concentration_index', 0)
            top3_sectors= ', '.join(f"{s} ({p}%)" for s, p in sectors_sorted[:3])
            suggestions = ai_assessment.get('suggestions', [])
            flags = []
            if ai_assessment.get('concentration_risk'):   flags.append('high single-stock concentration')
            if ai_assessment.get('under_diversified'):    flags.append('under-diversified')
            if ai_assessment.get('high_volatility'):      flags.append('high volatility')
            if ai_assessment.get('sector_concentration'): flags.append('sector over-concentration')

            prompt = (
                f"Analyse this Indian retail investor's portfolio:\n"
                f"- Total value: ₹{total_val:,.0f} | P&L: ₹{total_pnl:,.0f} ({pnl_pct:.1f}%)\n"
                f"- Holdings: {n_holdings} stocks | Health score: {health_sc}/100 | Risk: {risk_level}\n"
                f"- Top sectors: {top3_sectors}\n"
                f"- Concentration index: {conc_idx:.2f} (0=perfectly diversified, 1=single stock)\n"
                + (f"- Risk flags: {', '.join(flags)}\n" if flags else "- No major risk flags\n")
                + (f"- AI suggestions: {'; '.join(suggestions[:3])}\n" if suggestions else "")
                + "\nWrite a 3-paragraph portfolio health report:\n"
                "Para 1: Overall portfolio health — what's working, what isn't.\n"
                "Para 2: The biggest risk in this portfolio — sector, concentration, or volatility risk. Be specific.\n"
                "Para 3: Two concrete actions to improve the portfolio for Indian market conditions.\n"
                "Use 'your portfolio'. Direct, specific, max 220 words. No headers, no bullets."
            )
            svc  = AnthropicService()
            resp = svc.chat(
                messages=[{'role': 'user', 'content': prompt}],
                system="Portfolio analyst for Indian retail investors. Direct, data-driven. Not financial advice.",
                max_tokens=500,
                temperature=0.3,
            )
            narrative = resp.get('content', '').strip()
        except Exception as e:
            logger.error(f"Portfolio AI narrative error: {e}")

        return {
            'card_type': 'portfolio',
            'content':   f"Here's your portfolio analysis across {summary.get('holdings_count', 0)} holdings:",
            'card_data': {
                'summary':       summary,
                'sectors':       sectors_sorted[:8],          # [(name, pct), ...]
                'top_holdings':  top_holdings[:8],
                'risk_metrics':  risk_metrics,
                'ai_assessment': ai_assessment,
                'narrative':     narrative,
                'flags': {
                    'concentration_risk':    ai_assessment.get('concentration_risk', False),
                    'under_diversified':     ai_assessment.get('under_diversified', False),
                    'high_volatility':       ai_assessment.get('high_volatility', False),
                    'sector_concentration':  ai_assessment.get('sector_concentration', False),
                },
                'suggestions':   ai_assessment.get('suggestions', []),
                'rebalance':     ai_assessment.get('rebalance_actions', []),
            }
        }

    except Exception as e:
        logger.error(f"Portfolio handler error: {e}", exc_info=True)
        return {
            'card_type': 'prose',
            'content': "There was an issue loading your portfolio analysis. Make sure holdings are added and try again."
        }


def handle_behaviour(user_id: int) -> Dict[str, Any]:
    """Full behavioural + psychology analysis — same rich card as CSV upload path."""
    try:
        from services.behaviour_engine import BehaviourEngine
        engine = BehaviourEngine(user_id, 'live')

        # Quick data-presence check
        from models import ManualTradeImport, TradeHistory
        has_data = (
            ManualTradeImport.query.filter_by(user_id=user_id).first() is not None or
            TradeHistory.query.filter_by(user_id=user_id).first() is not None
        )
        if not has_data:
            return {
                'card_type': 'prose',
                'content': (
                    "I need your trade history to run a behavioural analysis.\n\n"
                    "**Upload a CSV** (Dhan P&L, Dhan Trade History, Zerodha Trade Book, or Zerodha P&L) "
                    "directly in this chat window using the **+** button. "
                    "I'll instantly run a full psychology report — revenge trading, overtrading, "
                    "loss aversion, tilt detection, and a personalised AI narrative."
                )
            }

        # Full engine run
        full         = engine.get_full_analysis()
        score_bd     = engine.get_score_breakdown()
        root_cause   = engine.get_performance_root_cause()
        psych_narr   = engine.get_psychology_narratives()
        correlations = engine.get_cross_module_correlations()

        stats       = full.get('stats', {})
        categories  = full.get('categories', {})
        personality = full.get('personality')

        # Flatten modules
        all_modules = {}
        for cat_data in categories.values():
            all_modules.update(cat_data.get('modules', {}))

        active_issues = sorted(
            [
                {
                    'key':      k,
                    'label':    v.get('label', k.replace('_', ' ').title()),
                    'severity': v.get('severity', 'none'),
                    'score':    v.get('score', 50),
                    'insight':  v.get('insight') or v.get('message', ''),
                }
                for k, v in all_modules.items()
                if v.get('detected') or v.get('severity') in ('high', 'medium')
            ],
            key=lambda x: {'high': 0, 'medium': 1, 'low': 2, 'none': 3}.get(x['severity'], 3)
        )

        _cat_labels = {
            'trading': 'Trading Behaviour', 'risk': 'Risk Management',
            'portfolio': 'Portfolio Health', 'performance': 'Performance Patterns',
            'psychology': 'Psychology',
        }
        cat_summary = [
            {
                'key':   cat_key,
                'label': _cat_labels.get(cat_key, cat_key.title()),
                'score': cat_data.get('score', 50),
                'modules': [
                    {
                        'key':      mk,
                        'label':    mv.get('label', mk.replace('_', ' ').title()),
                        'score':    mv.get('score', 50),
                        'severity': mv.get('severity', 'none'),
                        'detected': mv.get('detected', False),
                        'insight':  mv.get('insight') or mv.get('message', ''),
                    }
                    for mk, mv in cat_data.get('modules', {}).items()
                ],
            }
            for cat_key, cat_data in categories.items()
        ]

        # AI narrative
        narrative   = None
        action_items = []
        try:
            from services.anthropic_service import AnthropicService
            trade_count = stats.get('total_trades', 0)
            win_rate    = stats.get('win_rate', 0)
            wins        = stats.get('wins', 0)
            losses      = stats.get('losses', 0)
            total_pnl   = stats.get('total_pnl', 0)
            rr          = stats.get('risk_reward', 0)
            overall_sc  = full.get('score', 50)

            issue_lines = '\n'.join(
                f"  ⚠ {i['label']} ({i['severity']}): {i['insight']}"
                for i in active_issues[:8]
            ) or '  ✓ No major issues detected'

            rc_text = ''
            if root_cause:
                rc = root_cause.get('root_cause', {})
                rc_text = f"\nROOT CAUSE: {rc.get('label','')} — {rc.get('detail','')}\nFIX: {root_cause.get('fix_priority','')}\nUPSIDE: {root_cause.get('potential_upside','')}"

            psych_text = '\n'.join(
                f"  {k}: {v.get('narrative', '')} | Self-check: {v.get('self_awareness', '')}"
                for k, v in psych_narr.items()
            )

            prompt = (
                f"Trading psychology analysis for Indian retail trader.\n"
                f"SCORE: {overall_sc}/100 ({full.get('score_label','')}) · Archetype: {personality['type'] if personality else 'Unknown'}\n"
                f"STATS: {trade_count} trades · {win_rate}% win rate ({wins}W/{losses}L) · P&L ₹{total_pnl:,.0f} · R:R {rr}:1\n"
                f"BREAKDOWN: Discipline {score_bd.get('discipline','?')}/100 · Risk {score_bd.get('risk','?')}/100 · Timing {score_bd.get('timing','?')}/100 · Psychology {score_bd.get('psychology','?')}/100\n"
                f"ISSUES:\n{issue_lines}"
                f"\n{rc_text}"
                + (f"\nDEEP PSYCHOLOGY:\n{psych_text}" if psych_text else '')
                + "\n\nWrite a 4-paragraph trading psychology report:\n"
                "Para 1: Archetype and dominant emotional pattern.\n"
                "Para 2: Specific biases hurting P&L — use actual numbers.\n"
                "Para 3: Root cause — the core behavioural loop.\n"
                "Para 4: 3-5 specific ranked actions for Indian markets.\n"
                "Use 'you'. Direct, data-driven. No headers, no bullets. Max 350 words."
            )
            svc  = AnthropicService()
            resp = svc.chat(
                messages=[{'role': 'user', 'content': prompt}],
                system="Trading psychology expert for Indian retail traders. Blunt, specific, data-driven.",
                max_tokens=700,
                temperature=0.35,
            )
            narrative = resp.get('content', '').strip()

            _action_map = {
                'revenge_trading':    'Enforce a 30-minute no-trade cooldown after every loss.',
                'overtrading':        'Hard cap: 3 trades per day. Set this in your broker app.',
                'loss_aversion':      'Write your stop-loss before entering. Close it when hit — no renegotiating.',
                'profit_booking':     'Trail stops to entry on a 1R move — let the market exit you.',
                'tilt':               'Two consecutive losses = stop for the day. Return tomorrow.',
                'overconfidence':     'After 3 consecutive wins, cap position size to 1% of capital.',
                'fomo':               'After 3 consecutive wins, cap position size to 1% of capital.',
                'panic_selling':      'Hide P&L during market hours. Evaluate positions by thesis validity.',
                'time_of_day':        'Only trade during your proven profitable hours (see timing above).',
                'drawdown_sensitivity': 'Stop trading when day drawdown hits 2% of capital.',
                'position_sizing':    'Risk exactly 1-2% of capital per trade using a fixed calculator.',
                'leverage_risk':      'Immediately halve your F&O lot count to avoid unrecoverable drawdown.',
                'behavioral_drift':   'Write your trading rules. Read them every morning before market open.',
            }
            seen = set()
            for iss in active_issues:
                k = iss['key']
                if k in _action_map and k not in seen:
                    action_items.append(_action_map[k])
                    seen.add(k)
        except Exception as e:
            logger.error(f"Behaviour AI narrative error: {e}")

        return {
            'card_type': 'psychology',
            'content':   f"Here's your full behavioural analysis across {stats.get('total_trades', 0)} trades:",
            'card_data': {
                'score':          full.get('score', 50),
                'score_label':    full.get('score_label', ''),
                'score_color':    full.get('score_color', '#6b7280'),
                'personality':    personality,
                'trade_count':    stats.get('total_trades', 0),
                'win_rate':       stats.get('win_rate', 0),
                'wins':           stats.get('wins', 0),
                'losses':         stats.get('losses', 0),
                'total_pnl':      stats.get('total_pnl', 0),
                'rr':             stats.get('risk_reward', 0),
                'avg_win':        stats.get('avg_win', 0),
                'avg_loss':       stats.get('avg_loss', 0),
                'score_breakdown': score_bd,
                'categories':     cat_summary,
                'active_issues':  active_issues,
                'root_cause':     root_cause,
                'psych_narratives': {
                    k: {'narrative': v.get('narrative', ''), 'self_awareness': v.get('self_awareness', '')}
                    for k, v in psych_narr.items()
                },
                'by_hour':        full.get('by_hour', []),
                'by_day':         full.get('by_day', []),
                'by_symbol':      full.get('by_symbol', []),
                'narrative':      narrative,
                'action_items':   action_items,
                'correlations':   correlations[:3],
            }
        }

    except Exception as e:
        logger.error(f"Behaviour handler error: {e}")
        return {
            'card_type': 'prose',
            'content': (
                "The behavioural analysis engine hit an error. "
                "Make sure you have some trade history — upload a Dhan or Zerodha CSV via the + button."
            )
        }


def handle_general(message: str, conversation_history: list = None) -> Dict[str, Any]:
    """Handle general questions using Claude for prose answers."""
    try:
        api_key = os.environ.get('ANTHROPIC_API_KEY', '')
        if not api_key:
            return {
                'card_type': 'prose',
                'content': (
                    "I'm a research assistant for Indian markets — I can help with i-Scores, "
                    "F&O signals, mutual funds, portfolio analysis, and trading concepts.\n\n"
                    "To enable AI-powered answers, the ANTHROPIC_API_KEY needs to be configured."
                )
            }

        import anthropic
        client = anthropic.Anthropic(api_key=api_key)

        system = """You are Capulse, an AI research assistant for Indian retail traders and investors.
You provide clear, factual information about Indian stocks (NSE/BSE), F&O markets, mutual funds, and trading concepts.
You do NOT give buy/sell recommendations or tips. You explain, analyse, and educate.
Keep answers concise and practical. Use markdown: **bold** for key terms, - bullet lists for multiple points, short paragraphs.
Always note when something is research/education, not advice."""

        messages = []
        if conversation_history:
            for m in conversation_history[-6:]:
                role = m['role'] if m['role'] in ('user', 'assistant') else 'user'
                messages.append({'role': role, 'content': m['content']})
        messages.append({'role': 'user', 'content': message})

        response = client.messages.create(
            model='claude-haiku-4-5',
            max_tokens=900,
            system=system,
            messages=messages
        )
        return {
            'card_type': 'prose',
            'content': response.content[0].text
        }

    except Exception as e:
        logger.error(f"General handler error: {e}")
        return {
            'card_type': 'prose',
            'content': "I can help with i-Scores, F&O signals, mutual funds, portfolio analysis, and market education. What would you like to know?"
        }


def route_message(message: str, user_id: int, conversation_history: list = None) -> Dict[str, Any]:
    """
    Main entry point: classify intent and dispatch to appropriate engine.
    Returns a dict with card_type, content, and optionally card_data.
    """
    start = time.time()

    classification = classify_intent(message, conversation_history)
    intent = classification.get('intent', 'GENERAL')
    symbol = classification.get('symbol')
    fund_query = classification.get('fund_query')
    index = classification.get('index', 'NIFTY')
    level = classification.get('level')

    logger.info(f"Capulse intent: {intent} symbol={symbol} fund={fund_query} index={index} user={user_id}")

    try:
        if intent == 'ISCORE':
            result = handle_iscore(symbol, user_id)
        elif intent == 'FNO_SIGNAL':
            result = handle_fno_signal(index, level, user_id)
        elif intent == 'MUTUAL_FUND':
            result = handle_mutual_fund(fund_query, message)
        elif intent == 'PORTFOLIO':
            result = handle_portfolio(user_id)
        elif intent == 'BEHAVIOUR':
            result = handle_behaviour(user_id)
        else:
            result = handle_general(message, conversation_history)
    except Exception as e:
        logger.error(f"Router dispatch error: {e}")
        result = handle_general(message, conversation_history)

    result['intent'] = intent
    result['processing_time'] = round(time.time() - start, 2)
    return result
