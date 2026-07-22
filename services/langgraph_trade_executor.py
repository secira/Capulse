"""
LangGraph Trade Execution Pipeline for Capulse
Validates subscription, broker, funds, signal, and risk before trade execution
"""

import os
import logging
from typing import Dict, List, TypedDict, Annotated, Literal
from datetime import datetime
import operator
import json

from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END

from models import User, PricingPlan
from models_broker import BrokerAccount
from services.broker_service import BrokerService
from app import db

logger = logging.getLogger(__name__)


class TradeExecutionState(TypedDict):
    """State for the trade execution pipeline"""
    messages: Annotated[List, operator.add]
    user_id: int
    signal_id: int
    signal_data: Dict
    subscription_tier: str
    broker_account: Dict
    available_funds: float
    order_value: float
    position_size: int
    stop_loss_price: float
    target_price: float
    risk_amount: float
    reward_amount: float
    risk_reward_ratio: float
    execution_plan: Dict
    validation_errors: List[str]
    stage_status: Dict
    can_execute: bool


class LangGraphTradeExecutor:
    """
    6-Stage Trade Execution Pipeline with LangGraph
    
    Stages:
    1. Subscription Validator - Ensure user is TARGET_PRO or HNI
    2. Broker Selector - Identify primary broker and check connection
    3. Funds Validator - Verify available margin for order
    4. Signal Validator - Validate risk-reward ratio (minimum 1:2)
    5. Risk Calculator - Calculate optimal position sizing and stop-loss
    6. Execution Planner - Generate execution plan for user confirmation
    """
    
    def __init__(self):
        self._llm = None
        self._graph = None
    
    @property
    def llm(self):
        """Lazy-load the LLM to avoid startup failures when API key is not set"""
        if self._llm is None:
            api_key = os.environ.get("OPENAI_API_KEY", "")
            self._llm = ChatOpenAI(
                model="gpt-4o",
                temperature=0.1,
                api_key=api_key
            )
        return self._llm
    
    @property
    def graph(self):
        """Lazy-load the graph"""
        if self._graph is None:
            self._graph = self._build_pipeline()
        return self._graph
    
    def _build_pipeline(self):
        """Build the 6-stage conditional execution pipeline"""
        workflow = StateGraph(TradeExecutionState)
        
        # Add all pipeline stages
        workflow.add_node("subscription_validator", self.validate_subscription)
        workflow.add_node("broker_selector", self.select_broker)
        workflow.add_node("funds_validator", self.validate_funds)
        workflow.add_node("signal_validator", self.validate_signal)
        workflow.add_node("risk_calculator", self.calculate_risk)
        workflow.add_node("execution_planner", self.plan_execution)
        
        # Define sequential flow with conditional edges
        workflow.set_entry_point("subscription_validator")
        
        # Each stage can either continue or end on failure
        workflow.add_conditional_edges(
            "subscription_validator",
            self._should_continue,
            {
                "continue": "broker_selector",
                "stop": END
            }
        )
        
        workflow.add_conditional_edges(
            "broker_selector",
            self._should_continue,
            {
                "continue": "funds_validator",
                "stop": END
            }
        )
        
        workflow.add_conditional_edges(
            "funds_validator",
            self._should_continue,
            {
                "continue": "signal_validator",
                "stop": END
            }
        )
        
        workflow.add_conditional_edges(
            "signal_validator",
            self._should_continue,
            {
                "continue": "risk_calculator",
                "stop": END
            }
        )
        
        workflow.add_conditional_edges(
            "risk_calculator",
            self._should_continue,
            {
                "continue": "execution_planner",
                "stop": END
            }
        )
        
        workflow.add_edge("execution_planner", END)
        
        return workflow.compile()
    
    def _should_continue(self, state: TradeExecutionState) -> Literal["continue", "stop"]:
        """Determine if pipeline should continue or stop"""
        if state.get("validation_errors"):
            return "stop"
        return "continue"
    
    def validate_subscription(self, state: TradeExecutionState) -> Dict:
        """Stage 1: Validate user has TARGET_PRO or HNI subscription"""
        logger.info("Stage 1: Validating subscription tier")
        
        user_id = state["user_id"]
        
        try:
            user = User.query.get(user_id)
            if not user:
                return {
                    "validation_errors": ["User not found"],
                    "stage_status": {"subscription_validator": "error"},
                    "messages": [AIMessage(content="User not found")]
                }
            
            # Check subscription tier
            allowed_tiers = [PricingPlan.TARGET_PRO.value, PricingPlan.HNI.value]
            
            if user.pricing_plan not in allowed_tiers:
                return {
                    "validation_errors": [
                        f"Trade execution requires TARGET PRO or HNI subscription. Your current plan: {user.pricing_plan}"
                    ],
                    "stage_status": {"subscription_validator": "failed"},
                    "subscription_tier": user.pricing_plan,
                    "messages": [AIMessage(content="Subscription validation failed")]
                }
            
            return {
                "subscription_tier": user.pricing_plan,
                "stage_status": {"subscription_validator": "completed"},
                "messages": [AIMessage(content=f"Subscription validated: {user.pricing_plan}")]
            }
            
        except Exception as e:
            logger.error(f"Subscription validation error: {e}")
            return {
                "validation_errors": [f"Subscription validation error: {str(e)}"],
                "stage_status": {"subscription_validator": "error"},
                "messages": [AIMessage(content="Error validating subscription")]
            }
    
    def select_broker(self, state: TradeExecutionState) -> Dict:
        """Stage 2: Select primary broker and verify connection"""
        logger.info("Stage 2: Selecting primary broker")
        
        user_id = state["user_id"]
        
        try:
            # Find primary broker account
            primary_broker = BrokerAccount.query.filter_by(
                user_id=user_id,
                is_primary=True,
                is_active=True
            ).first()
            
            if not primary_broker:
                return {
                    "validation_errors": ["No primary broker account configured. Please set a primary broker in Settings."],
                    "stage_status": {**state.get("stage_status", {}), "broker_selector": "failed"},
                    "messages": [AIMessage(content="No primary broker found")]
                }
            
            # Verify broker connection
            if primary_broker.connection_status != 'connected':
                return {
                    "validation_errors": [
                        f"Primary broker {primary_broker.broker_name} is not connected. Please reconnect in Settings."
                    ],
                    "stage_status": {**state.get("stage_status", {}), "broker_selector": "failed"},
                    "messages": [AIMessage(content="Broker not connected")]
                }
            
            broker_data = {
                "id": primary_broker.id,
                "broker_name": primary_broker.broker_name,
                "broker_type": primary_broker.broker_type,
                "margin_available": primary_broker.margin_available or 0.0
            }
            
            return {
                "broker_account": broker_data,
                "available_funds": broker_data["margin_available"],
                "stage_status": {**state.get("stage_status", {}), "broker_selector": "completed"},
                "messages": [AIMessage(content=f"Primary broker selected: {primary_broker.broker_name}")]
            }
            
        except Exception as e:
            logger.error(f"Broker selection error: {e}")
            return {
                "validation_errors": [f"Broker selection error: {str(e)}"],
                "stage_status": {**state.get("stage_status", {}), "broker_selector": "error"},
                "messages": [AIMessage(content="Error selecting broker")]
            }
    
    def validate_funds(self, state: TradeExecutionState) -> Dict:
        """Stage 3: Validate available funds for order"""
        logger.info("Stage 3: Validating available funds")
        
        signal_data = state.get("signal_data", {})
        available_funds = state.get("available_funds", 0.0)
        
        try:
            # Calculate required order value
            entry_price = signal_data.get("entry_price", 0)
            suggested_quantity = signal_data.get("quantity", 100)  # Default 100 shares
            
            order_value = entry_price * suggested_quantity
            
            # Add buffer for brokerage and charges (1% buffer)
            required_margin = order_value * 1.01
            
            if required_margin > available_funds:
                return {
                    "validation_errors": [
                        f"Insufficient funds. Required: ₹{required_margin:,.2f}, Available: ₹{available_funds:,.2f}"
                    ],
                    "stage_status": {**state.get("stage_status", {}), "funds_validator": "failed"},
                    "order_value": order_value,
                    "messages": [AIMessage(content="Insufficient funds")]
                }
            
            return {
                "order_value": order_value,
                "position_size": suggested_quantity,
                "stage_status": {**state.get("stage_status", {}), "funds_validator": "completed"},
                "messages": [AIMessage(content=f"Funds validated. Order value: ₹{order_value:,.2f}")]
            }
            
        except Exception as e:
            logger.error(f"Funds validation error: {e}")
            return {
                "validation_errors": [f"Funds validation error: {str(e)}"],
                "stage_status": {**state.get("stage_status", {}), "funds_validator": "error"},
                "messages": [AIMessage(content="Error validating funds")]
            }
    
    def validate_signal(self, state: TradeExecutionState) -> Dict:
        """Stage 4: Validate signal risk-reward ratio"""
        logger.info("Stage 4: Validating signal quality")
        
        signal_data = state.get("signal_data", {})
        
        try:
            entry_price = signal_data.get("entry_price", 0)
            target_price = signal_data.get("target_price", 0)
            stop_loss = signal_data.get("stop_loss", 0)
            action = signal_data.get("action", "BUY")
            
            if not all([entry_price, target_price, stop_loss]):
                return {
                    "validation_errors": ["Signal missing required price levels (entry/target/stop-loss)"],
                    "stage_status": {**state.get("stage_status", {}), "signal_validator": "failed"},
                    "messages": [AIMessage(content="Invalid signal data")]
                }
            
            # Calculate risk and reward
            if action == "BUY":
                risk = entry_price - stop_loss
                reward = target_price - entry_price
            else:  # SELL
                risk = stop_loss - entry_price
                reward = entry_price - target_price
            
            if risk <= 0 or reward <= 0:
                return {
                    "validation_errors": ["Invalid signal: risk or reward is negative or zero"],
                    "stage_status": {**state.get("stage_status", {}), "signal_validator": "failed"},
                    "messages": [AIMessage(content="Invalid risk/reward calculation")]
                }
            
            risk_reward_ratio = reward / risk
            
            # Enforce minimum 1:2 risk-reward ratio
            if risk_reward_ratio < 2.0:
                return {
                    "validation_errors": [
                        f"Signal quality too low. Risk-Reward Ratio: 1:{risk_reward_ratio:.2f} (minimum required: 1:2)"
                    ],
                    "stage_status": {**state.get("stage_status", {}), "signal_validator": "failed"},
                    "risk_reward_ratio": risk_reward_ratio,
                    "messages": [AIMessage(content="Signal quality below threshold")]
                }
            
            # Calculate risk amount
            position_size = state.get("position_size", 100)
            risk_amount = risk * position_size
            reward_amount = reward * position_size
            
            return {
                "risk_reward_ratio": risk_reward_ratio,
                "risk_amount": risk_amount,
                "reward_amount": reward_amount,
                "stop_loss_price": stop_loss,
                "target_price": target_price,
                "stage_status": {**state.get("stage_status", {}), "signal_validator": "completed"},
                "messages": [AIMessage(content=f"Signal validated. R:R = 1:{risk_reward_ratio:.2f}")]
            }
            
        except Exception as e:
            logger.error(f"Signal validation error: {e}")
            return {
                "validation_errors": [f"Signal validation error: {str(e)}"],
                "stage_status": {**state.get("stage_status", {}), "signal_validator": "error"},
                "messages": [AIMessage(content="Error validating signal")]
            }
    
    def calculate_risk(self, state: TradeExecutionState) -> Dict:
        """Stage 5: Calculate optimal position sizing based on risk tolerance"""
        logger.info("Stage 5: Calculating risk-based position sizing")
        
        try:
            available_funds = state.get("available_funds", 0)
            risk_amount = state.get("risk_amount", 0)
            order_value = state.get("order_value", 0)
            
            # Calculate risk as percentage of available capital
            risk_percentage = (risk_amount / available_funds) * 100 if available_funds > 0 else 0
            
            # Enforce maximum 5% risk per trade
            max_risk_percentage = 5.0
            
            if risk_percentage > max_risk_percentage:
                # Reduce position size to meet risk limits
                adjusted_position_size = int((max_risk_percentage / risk_percentage) * state.get("position_size", 100))
                
                signal_data = state.get("signal_data", {})
                entry_price = signal_data.get("entry_price", 0)
                stop_loss = signal_data.get("stop_loss", 0)
                
                # Recalculate with adjusted position
                if signal_data.get("action") == "BUY":
                    adjusted_risk = (entry_price - stop_loss) * adjusted_position_size
                else:
                    adjusted_risk = (stop_loss - entry_price) * adjusted_position_size
                
                adjusted_order_value = entry_price * adjusted_position_size
                adjusted_risk_percentage = (adjusted_risk / available_funds) * 100
                
                return {
                    "position_size": adjusted_position_size,
                    "order_value": adjusted_order_value,
                    "risk_amount": adjusted_risk,
                    "stage_status": {**state.get("stage_status", {}), "risk_calculator": "completed"},
                    "messages": [
                        AIMessage(content=f"Position size adjusted to {adjusted_position_size} shares to limit risk to {adjusted_risk_percentage:.2f}%")
                    ]
                }
            
            return {
                "stage_status": {**state.get("stage_status", {}), "risk_calculator": "completed"},
                "messages": [AIMessage(content=f"Risk calculated: {risk_percentage:.2f}% of capital")]
            }
            
        except Exception as e:
            logger.error(f"Risk calculation error: {e}")
            return {
                "validation_errors": [f"Risk calculation error: {str(e)}"],
                "stage_status": {**state.get("stage_status", {}), "risk_calculator": "error"},
                "messages": [AIMessage(content="Error calculating risk")]
            }
    
    def plan_execution(self, state: TradeExecutionState) -> Dict:
        """Stage 6: Generate execution plan for user confirmation"""
        logger.info("Stage 6: Planning execution")
        
        try:
            signal_data = state.get("signal_data", {})
            broker_account = state.get("broker_account", {})
            
            execution_plan = {
                "symbol": signal_data.get("symbol", ""),
                "action": signal_data.get("action", "BUY"),
                "quantity": state.get("position_size", 0),
                "entry_price": signal_data.get("entry_price", 0),
                "stop_loss": state.get("stop_loss_price", 0),
                "target": state.get("target_price", 0),
                "order_value": state.get("order_value", 0),
                "risk_amount": state.get("risk_amount", 0),
                "reward_amount": state.get("reward_amount", 0),
                "risk_reward_ratio": state.get("risk_reward_ratio", 0),
                "broker_name": broker_account.get("broker_name", ""),
                "broker_id": broker_account.get("id", 0),
                "timeframe": signal_data.get("timeframe", "Intraday"),
                "order_type": "LIMIT",
                "product_type": "MIS" if signal_data.get("timeframe") == "Intraday" else "CNC",
                "ready_for_execution": True
            }
            
            return {
                "execution_plan": execution_plan,
                "can_execute": True,
                "stage_status": {**state.get("stage_status", {}), "execution_planner": "completed"},
                "messages": [AIMessage(content="Execution plan ready for user confirmation")]
            }
            
        except Exception as e:
            logger.error(f"Execution planning error: {e}")
            return {
                "validation_errors": [f"Execution planning error: {str(e)}"],
                "stage_status": {**state.get("stage_status", {}), "execution_planner": "error"},
                "messages": [AIMessage(content="Error planning execution")]
            }
    
    def validate_trade(self, user_id: int, signal_data: Dict, signal_id: int = None) -> Dict:
        """
        Main entry point for trade validation
        
        Args:
            user_id: User ID requesting trade
            signal_data: Signal data with entry/target/stop-loss
            signal_id: Optional signal ID from database
            
        Returns:
            Dict with validation results and execution plan
        """
        logger.info(f"Starting trade validation for user {user_id}")
        
        initial_state = {
            "messages": [],
            "user_id": user_id,
            "signal_id": signal_id,
            "signal_data": signal_data,
            "subscription_tier": "",
            "broker_account": {},
            "available_funds": 0.0,
            "order_value": 0.0,
            "position_size": signal_data.get("quantity", 100),
            "stop_loss_price": 0.0,
            "target_price": 0.0,
            "risk_amount": 0.0,
            "reward_amount": 0.0,
            "risk_reward_ratio": 0.0,
            "execution_plan": {},
            "validation_errors": [],
            "stage_status": {},
            "can_execute": False
        }
        
        try:
            # Run the pipeline
            final_state = self.graph.invoke(initial_state)
            
            return {
                "success": final_state.get("can_execute", False),
                "execution_plan": final_state.get("execution_plan", {}),
                "validation_errors": final_state.get("validation_errors", []),
                "stage_status": final_state.get("stage_status", {}),
                "pipeline_metadata": {
                    "stages_completed": len([s for s in final_state.get("stage_status", {}).values() if s == "completed"]),
                    "total_stages": 6,
                    "subscription_tier": final_state.get("subscription_tier", ""),
                    "broker_used": final_state.get("broker_account", {}).get("broker_name", "")
                }
            }
            
        except Exception as e:
            logger.error(f"Trade validation pipeline error: {e}")
            return {
                "success": False,
                "execution_plan": {},
                "validation_errors": [f"Pipeline error: {str(e)}"],
                "stage_status": {},
                "pipeline_metadata": {}
            }
