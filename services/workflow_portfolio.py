import json
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone

from services.anthropic_service import AnthropicService
from services.workflow_engine import WorkflowNode, WorkflowPipeline, WorkflowState
from services.data_connectors import ConnectorRegistry

logger = logging.getLogger(__name__)


class PortfolioAnalysisWorkflow:
    PIPELINE_NAME = "portfolio_analysis"
    PIPELINE_DESCRIPTION = "Multi-agent portfolio analysis using Anthropic Claude"

    def __init__(self, anthropic_service: Optional[AnthropicService] = None):
        self._anthropic = anthropic_service or AnthropicService()
        self._registry = ConnectorRegistry()

    def build_pipeline(self) -> WorkflowPipeline:
        pipeline = WorkflowPipeline(
            name=self.PIPELINE_NAME,
            description=self.PIPELINE_DESCRIPTION,
        )
        pipeline.add_node(WorkflowNode(
            name="fetch_portfolio",
            execute_fn=self._fetch_portfolio,
            description="Fetch portfolio data from broker connectors",
            retry_count=1,
        ))
        pipeline.add_node(WorkflowNode(
            name="risk_analysis",
            execute_fn=self._risk_analysis,
            description="Analyze portfolio risk, volatility, and diversification",
            retry_count=1,
        ))
        pipeline.add_node(WorkflowNode(
            name="sector_analysis",
            execute_fn=self._sector_analysis,
            description="Evaluate sector allocation and concentration",
            retry_count=1,
        ))
        pipeline.add_node(WorkflowNode(
            name="asset_allocation",
            execute_fn=self._asset_allocation,
            description="Recommend optimal asset allocation",
            retry_count=1,
        ))
        pipeline.add_node(WorkflowNode(
            name="opportunity_finding",
            execute_fn=self._opportunity_finding,
            description="Identify investment opportunities matching user profile",
            retry_count=1,
        ))
        pipeline.add_node(WorkflowNode(
            name="synthesis",
            execute_fn=self._synthesis,
            description="Synthesize all analyses into a comprehensive report",
            retry_count=1,
        ))
        return pipeline

    def run(self, user_id: int, tenant_id: str = "live") -> Dict[str, Any]:
        pipeline = self.build_pipeline()
        initial_data = {
            "user_id": user_id,
            "tenant_id": tenant_id,
        }
        state = pipeline.execute(initial_data=initial_data, stop_on_failure=True)
        summary = pipeline.get_execution_summary(state)
        summary["results"] = {
            "portfolio_data": state.get("portfolio_data"),
            "user_preferences": state.get("user_preferences"),
            "risk_analysis": state.get("risk_analysis"),
            "sector_analysis": state.get("sector_analysis"),
            "asset_allocation": state.get("asset_allocation"),
            "opportunities": state.get("opportunities"),
            "report": state.get("report"),
        }
        return summary

    def _fetch_portfolio(self, state: WorkflowState) -> Dict[str, Any]:
        user_id = state.get("user_id")
        tenant_id = state.get("tenant_id", "live")

        connector = self._registry.get_best_connector(user_id, tenant_id)
        portfolio = connector.get_portfolio(user_id)
        portfolio_dict = portfolio.to_dict()

        try:
            from services.comprehensive_portfolio_service import ComprehensivePortfolioService
            svc = ComprehensivePortfolioService(user_id)
            comprehensive = svc.get_complete_portfolio_summary()
            portfolio_dict["comprehensive"] = comprehensive
        except Exception as exc:
            logger.warning(f"Comprehensive portfolio fetch failed: {exc}")

        user_preferences = self._load_user_preferences(user_id)

        return {
            "portfolio_data": portfolio_dict,
            "user_preferences": user_preferences,
        }

    def _risk_analysis(self, state: WorkflowState) -> Dict[str, Any]:
        portfolio_data = state.get("portfolio_data", {})
        user_preferences = state.get("user_preferences", {})

        system = (
            "You are a Risk Analysis Specialist at Capulse, specializing in Indian equity markets.\n"
            "Analyze the portfolio and provide a precise, quantitative risk assessment.\n"
            "Consider the user's stated risk tolerance and preferences.\n"
            "Return ONLY valid JSON."
        )
        schema = {
            "risk_score": "number 1-10",
            "volatility_assessment": "string",
            "diversification_quality": "string (Poor/Fair/Good/Excellent)",
            "concentration_risks": ["string"],
            "risk_adjusted_return": "string",
            "risk_tolerance_alignment": "string",
            "mitigation_recommendations": ["string"],
        }
        trimmed_portfolio = dict(portfolio_data)
        if trimmed_portfolio.get('holdings') and len(trimmed_portfolio['holdings']) > 30:
            trimmed_portfolio['holdings'] = trimmed_portfolio['holdings'][:30]
            trimmed_portfolio['holdings_truncated'] = True
        content = (
            f"Analyze this portfolio for risk:\n\n"
            f"Portfolio:\n{json.dumps(trimmed_portfolio, indent=2, default=str)}\n\n"
            f"User Preferences:\n{json.dumps(user_preferences, indent=2, default=str)}"
        )

        result = self._anthropic.structured_output(
            messages=[{"role": "user", "content": content}],
            output_schema=schema,
            system=system,
            temperature=0.1,
        )
        parsed = result.get("parsed") or {"raw_analysis": result.get("text", "")}
        return {"risk_analysis": parsed}

    def _sector_analysis(self, state: WorkflowState) -> Dict[str, Any]:
        portfolio_data = state.get("portfolio_data", {})

        system = (
            "You are a Sector Analysis Specialist at Capulse.\n"
            "Evaluate the portfolio's sector allocation with specific percentages.\n"
            "Return ONLY valid JSON."
        )
        schema = {
            "sector_breakdown": {"sector_name": "percentage"},
            "overexposed_sectors": ["string"],
            "underexposed_sectors": ["string"],
            "cyclical_defensive_balance": "string",
            "sector_risks": ["string"],
            "rebalancing_recommendations": ["string"],
        }
        trimmed_portfolio = dict(portfolio_data)
        if trimmed_portfolio.get('holdings') and len(trimmed_portfolio['holdings']) > 30:
            trimmed_portfolio['holdings'] = trimmed_portfolio['holdings'][:30]
            trimmed_portfolio['holdings_truncated'] = True
        content = (
            f"Analyze sector allocation for this portfolio:\n\n"
            f"{json.dumps(trimmed_portfolio, indent=2, default=str)}"
        )

        result = self._anthropic.structured_output(
            messages=[{"role": "user", "content": content}],
            output_schema=schema,
            system=system,
            temperature=0.15,
        )
        parsed = result.get("parsed") or {"raw_analysis": result.get("text", "")}
        return {"sector_analysis": parsed}

    def _asset_allocation(self, state: WorkflowState) -> Dict[str, Any]:
        portfolio_data = state.get("portfolio_data", {})
        risk_analysis = state.get("risk_analysis", {})
        sector_analysis = state.get("sector_analysis", {})
        user_preferences = state.get("user_preferences", {})

        system = (
            "You are an Asset Allocation Specialist at Capulse.\n"
            "Recommend optimal asset allocation aligned with the user's risk tolerance, "
            "investment horizon, and preferred asset classes.\n"
            "Return ONLY valid JSON."
        )
        schema = {
            "current_allocation": {"asset_class": "percentage"},
            "recommended_allocation": {"asset_class": "percentage"},
            "rebalancing_actions": [{"asset_class": "string", "action": "string", "amount_pct": "number"}],
            "implementation_timeline": "string",
            "expected_risk_return_impact": "string",
        }
        trimmed_portfolio = dict(portfolio_data)
        if trimmed_portfolio.get('holdings') and len(trimmed_portfolio['holdings']) > 30:
            trimmed_portfolio['holdings'] = trimmed_portfolio['holdings'][:30]
            trimmed_portfolio['holdings_truncated'] = True
        content = (
            f"Recommend asset allocation based on:\n\n"
            f"Portfolio:\n{json.dumps(trimmed_portfolio, indent=2, default=str)}\n\n"
            f"Risk Analysis:\n{json.dumps(risk_analysis, indent=2, default=str)}\n\n"
            f"Sector Analysis:\n{json.dumps(sector_analysis, indent=2, default=str)}\n\n"
            f"User Preferences:\n{json.dumps(user_preferences, indent=2, default=str)}"
        )

        result = self._anthropic.structured_output(
            messages=[{"role": "user", "content": content}],
            output_schema=schema,
            system=system,
            temperature=0.2,
        )
        parsed = result.get("parsed") or {"raw_recommendations": result.get("text", "")}
        return {"asset_allocation": parsed}

    def _opportunity_finding(self, state: WorkflowState) -> Dict[str, Any]:
        portfolio_data = state.get("portfolio_data", {})
        asset_allocation = state.get("asset_allocation", {})
        user_preferences = state.get("user_preferences", {})

        system = (
            "You are an Investment Opportunities Specialist at Capulse.\n"
            "Identify 5-10 specific investment opportunities for Indian markets "
            "matching the user's profile and allocation gaps.\n"
            "Return ONLY valid JSON."
        )
        schema = {
            "opportunities": [
                {
                    "symbol": "string",
                    "name": "string",
                    "sector": "string",
                    "rationale": "string",
                    "suggested_allocation_pct": "number",
                    "risk_level": "string",
                    "alignment_with_goals": "string",
                }
            ]
        }
        trimmed_portfolio = dict(portfolio_data)
        if trimmed_portfolio.get('holdings') and len(trimmed_portfolio['holdings']) > 30:
            trimmed_portfolio['holdings'] = trimmed_portfolio['holdings'][:30]
            trimmed_portfolio['holdings_truncated'] = True
        content = (
            f"Find investment opportunities based on:\n\n"
            f"Portfolio:\n{json.dumps(trimmed_portfolio, indent=2, default=str)}\n\n"
            f"Allocation Gaps:\n{json.dumps(asset_allocation, indent=2, default=str)}\n\n"
            f"User Preferences:\n{json.dumps(user_preferences, indent=2, default=str)}"
        )

        result = self._anthropic.structured_output(
            messages=[{"role": "user", "content": content}],
            output_schema=schema,
            system=system,
            temperature=0.4,
        )
        parsed = result.get("parsed") or {"raw_opportunities": result.get("text", "")}
        opportunities = parsed.get("opportunities", [parsed]) if isinstance(parsed, dict) else parsed
        return {"opportunities": opportunities}

    def _synthesis(self, state: WorkflowState) -> Dict[str, Any]:
        user_preferences = state.get("user_preferences", {})
        risk_analysis = state.get("risk_analysis", {})
        sector_analysis = state.get("sector_analysis", {})
        asset_allocation = state.get("asset_allocation", {})
        opportunities = state.get("opportunities", [])

        system = (
            "You are the Chief Portfolio Strategist at Capulse.\n"
            "Synthesize all analysis results into a comprehensive, personalized portfolio report.\n"
            "Use markdown formatting with clear sections.\n"
            "Emphasize how recommendations align with the user's goals and preferences."
        )
        content = (
            f"Create a comprehensive portfolio optimization report.\n\n"
            f"User Preferences:\n{json.dumps(user_preferences, indent=2, default=str)}\n\n"
            f"Risk Analysis:\n{json.dumps(risk_analysis, indent=2, default=str)}\n\n"
            f"Sector Analysis:\n{json.dumps(sector_analysis, indent=2, default=str)}\n\n"
            f"Asset Allocation:\n{json.dumps(asset_allocation, indent=2, default=str)}\n\n"
            f"Opportunities:\n{json.dumps(opportunities, indent=2, default=str)}\n\n"
            "Structure the report with:\n"
            "1. Executive Summary\n"
            "2. Investor Profile\n"
            "3. Risk Assessment\n"
            "4. Sector Analysis\n"
            "5. Asset Allocation Strategy\n"
            "6. Investment Opportunities\n"
            "7. Prioritized Action Plan\n"
            "8. Expected Outcomes"
        )

        result = self._anthropic.chat(
            messages=[{"role": "user", "content": content}],
            system=system,
            temperature=0.3,
        )
        report = result.get("text", "")

        self._save_report(state, report)

        return {"report": report}

    def _load_user_preferences(self, user_id: int) -> Dict[str, Any]:
        try:
            from models import PortfolioPreferences
            prefs = PortfolioPreferences.query.filter_by(user_id=user_id).first()
            if prefs and prefs.completed:
                return prefs.to_dict()
        except Exception as exc:
            logger.warning(f"Could not load user preferences: {exc}")
        return {}

    def _save_report(self, state: WorkflowState, report: str) -> None:
        try:
            from models import PortfolioOptimizationReport
            from app import db

            user_id = state.get("user_id")
            if not user_id:
                return

            record = PortfolioOptimizationReport(
                user_id=user_id,
                tenant_id=state.get("tenant_id", "live"),
                report_data={"report": report},
                risk_analysis=state.get("risk_analysis"),
                sector_analysis=state.get("sector_analysis"),
                allocation_recommendations=state.get("asset_allocation"),
                opportunities=state.get("opportunities"),
            )
            db.session.add(record)
            db.session.commit()
            logger.info(f"Portfolio analysis report saved for user {user_id}")
        except Exception as exc:
            logger.warning(f"Failed to save portfolio report: {exc}")
