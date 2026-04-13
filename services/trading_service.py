"""
Trading Service
Handles trade recommendations, market analysis, and execution coordination
"""

import json
import random
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional, Tuple
from sqlalchemy import func, and_, desc
from models import (
    TradingAsset, TradingStrategy, TradeRecommendation, TradeExecution, 
    ActiveTrade, TradeHistory, MarketAnalysis, BrokerAccount, User
)
from app import db
from middleware.tenant_middleware import get_current_tenant_id, TenantQuery, create_for_tenant
import requests
import os

class TradingService:
    """Comprehensive trading service with strategy recommendations and execution"""
    
    def __init__(self):
        self.initialize_default_data()
    
    def initialize_default_data(self):
        """Initialize default trading assets and strategies"""
        try:
            # Check if data already exists
            if TradingAsset.query.first() and TradingStrategy.query.first():
                return
                
            # Initialize default assets
            default_assets = [
                # Stocks
                {'symbol': 'NIFTY', 'company_name': 'Nifty 50 Index', 'asset_class': 'stocks', 'exchange': 'NSE', 'sector': 'Index', 'lot_size': 50},
                {'symbol': 'BANKNIFTY', 'company_name': 'Bank Nifty Index', 'asset_class': 'stocks', 'exchange': 'NSE', 'sector': 'Banking', 'lot_size': 25},
                {'symbol': 'RELIANCE', 'company_name': 'Reliance Industries Limited', 'asset_class': 'stocks', 'exchange': 'NSE', 'sector': 'Oil & Gas', 'lot_size': 1},
                {'symbol': 'HDFCBANK', 'company_name': 'HDFC Bank Limited', 'asset_class': 'stocks', 'exchange': 'NSE', 'sector': 'Banking', 'lot_size': 1},
                {'symbol': 'TCS', 'company_name': 'Tata Consultancy Services', 'asset_class': 'stocks', 'exchange': 'NSE', 'sector': 'Information Technology', 'lot_size': 1},
                {'symbol': 'INFY', 'company_name': 'Infosys Limited', 'asset_class': 'stocks', 'exchange': 'NSE', 'sector': 'Information Technology', 'lot_size': 1},
                
                # Crypto
                {'symbol': 'BTC', 'company_name': 'Bitcoin', 'asset_class': 'crypto', 'exchange': 'BINANCE', 'sector': 'Cryptocurrency', 'lot_size': 1},
                {'symbol': 'ETH', 'company_name': 'Ethereum', 'asset_class': 'crypto', 'exchange': 'BINANCE', 'sector': 'Cryptocurrency', 'lot_size': 1},
            ]
            
            for asset_data in default_assets:
                asset = TradingAsset(**asset_data)
                db.session.add(asset)
            
            # Initialize default strategies
            default_strategies = [
                {
                    'name': 'Covered Call',
                    'description': 'Conservative strategy involving holding stocks and selling call options',
                    'strategy_type': 'income',
                    'asset_classes': json.dumps(['stocks', 'options']),
                    'market_conditions': json.dumps(['sideways', 'slightly_bullish']),
                    'risk_level': 'LOW',
                    'min_capital': 50000,
                    'max_loss_percentage': 5.0,
                    'max_profit_percentage': 3.0,
                    'success_rate': 75.0
                },
                {
                    'name': 'Mean Reversion',
                    'description': 'Strategy based on price returning to mean after extreme movements',
                    'strategy_type': 'directional',
                    'asset_classes': json.dumps(['stocks', 'futures']),
                    'market_conditions': json.dumps(['oversold', 'overbought']),
                    'risk_level': 'MEDIUM',
                    'min_capital': 25000,
                    'max_loss_percentage': 8.0,
                    'max_profit_percentage': 12.0,
                    'success_rate': 68.0
                },
                {
                    'name': 'Pivot Reversal Universal',
                    'description': 'Universal pivot-based reversal strategy for all market conditions',
                    'strategy_type': 'reversal',
                    'asset_classes': json.dumps(['stocks', 'futures', 'options']),
                    'market_conditions': json.dumps(['bullish', 'bearish', 'sideways']),
                    'risk_level': 'MEDIUM',
                    'min_capital': 30000,
                    'max_loss_percentage': 6.0,
                    'max_profit_percentage': 15.0,
                    'success_rate': 72.0
                },
                {
                    'name': 'Momentum Strategy',
                    'description': 'Trend-following strategy that capitalizes on strong price movements',
                    'strategy_type': 'trend_following',
                    'asset_classes': json.dumps(['stocks', 'futures', 'crypto']),
                    'market_conditions': json.dumps(['bullish', 'bearish']),
                    'risk_level': 'HIGH',
                    'min_capital': 20000,
                    'max_loss_percentage': 10.0,
                    'max_profit_percentage': 25.0,
                    'success_rate': 65.0
                },
                {
                    'name': 'Directional Index',
                    'description': 'ADX-based strategy for trending markets',
                    'strategy_type': 'directional',
                    'asset_classes': json.dumps(['stocks', 'futures']),
                    'market_conditions': json.dumps(['bullish', 'bearish']),
                    'risk_level': 'MEDIUM',
                    'min_capital': 35000,
                    'max_loss_percentage': 7.0,
                    'max_profit_percentage': 18.0,
                    'success_rate': 70.0
                },
                {
                    'name': 'EMA Crossover',
                    'description': 'Exponential Moving Average crossover strategy',
                    'strategy_type': 'trend_following',
                    'asset_classes': json.dumps(['stocks', 'futures', 'crypto']),
                    'market_conditions': json.dumps(['bullish', 'bearish']),
                    'risk_level': 'LOW',
                    'min_capital': 15000,
                    'max_loss_percentage': 4.0,
                    'max_profit_percentage': 8.0,
                    'success_rate': 78.0
                },
                {
                    'name': 'Super Trend',
                    'description': 'Super Trend indicator based strategy for trend identification',
                    'strategy_type': 'trend_following',
                    'asset_classes': json.dumps(['stocks', 'futures']),
                    'market_conditions': json.dumps(['bullish', 'bearish']),
                    'risk_level': 'MEDIUM',
                    'min_capital': 25000,
                    'max_loss_percentage': 6.0,
                    'max_profit_percentage': 16.0,
                    'success_rate': 73.0
                },
                {
                    'name': 'Straddle',
                    'description': 'Long straddle options strategy for high volatility markets',
                    'strategy_type': 'non_directional',
                    'asset_classes': json.dumps(['options']),
                    'market_conditions': json.dumps(['high_volatility']),
                    'risk_level': 'HIGH',
                    'min_capital': 40000,
                    'max_loss_percentage': 15.0,
                    'max_profit_percentage': 50.0,
                    'success_rate': 55.0
                },
                {
                    'name': 'Iron Butterfly',
                    'description': 'Iron butterfly options strategy for low volatility markets',
                    'strategy_type': 'non_directional',
                    'asset_classes': json.dumps(['options']),
                    'market_conditions': json.dumps(['low_volatility', 'sideways']),
                    'risk_level': 'LOW',
                    'min_capital': 45000,
                    'max_loss_percentage': 8.0,
                    'max_profit_percentage': 12.0,
                    'success_rate': 80.0
                }
            ]
            
            for strategy_data in default_strategies:
                strategy = TradingStrategy(**strategy_data)
                db.session.add(strategy)
            
            db.session.commit()
            
        except Exception as e:
            print(f"Error initializing trading data: {e}")
    
    def get_market_analysis(self, symbol: str) -> Dict:
        """Get current market analysis for a symbol"""
        try:
            # Check if we have recent analysis (within last hour)
            recent_analysis = MarketAnalysis.query.filter(
                and_(
                    MarketAnalysis.symbol == symbol,
                    MarketAnalysis.analysis_date == date.today()
                )
            ).first()
            
            if recent_analysis:
                return {
                    'trend_direction': recent_analysis.trend_direction,
                    'confidence_level': recent_analysis.confidence_level,
                    'ema_signal': recent_analysis.ema_signal,
                    'rsi_value': recent_analysis.rsi_value,
                    'macd_signal': recent_analysis.macd_signal,
                    'support_level': recent_analysis.support_level,
                    'resistance_level': recent_analysis.resistance_level,
                    'recommended_strategies': json.loads(recent_analysis.recommended_strategies or '[]')
                }
            
            # Generate new analysis (simplified mock analysis)
            analysis_data = self._generate_market_analysis(symbol)
            
            # Save to database
            analysis = MarketAnalysis(
                symbol=symbol,
                trend_direction=analysis_data['trend_direction'],
                confidence_level=analysis_data['confidence_level'],
                ema_signal=analysis_data['ema_signal'],
                rsi_value=analysis_data['rsi_value'],
                macd_signal=analysis_data['macd_signal'],
                support_level=analysis_data['support_level'],
                resistance_level=analysis_data['resistance_level'],
                recommended_strategies=json.dumps(analysis_data['recommended_strategies'])
            )
            db.session.add(analysis)
            db.session.commit()
            
            return analysis_data
            
        except Exception as e:
            return self._get_default_analysis()
    
    def _generate_market_analysis(self, symbol: str) -> Dict:
        """Generate market analysis (simplified mock version)"""
        # In production, this would call real market data APIs
        trend_options = ['BULLISH', 'BEARISH', 'SIDEWAYS']
        signal_options = ['BUY', 'SELL', 'HOLD']
        
        # Mock current price for calculations
        base_price = 2500 if symbol == 'NIFTY' else 1500
        
        return {
            'trend_direction': random.choice(trend_options),
            'confidence_level': random.randint(60, 95),
            'ema_signal': random.choice(signal_options),
            'rsi_value': random.uniform(30, 70),
            'macd_signal': random.choice(signal_options),
            'support_level': base_price * 0.98,
            'resistance_level': base_price * 1.02,
            'recommended_strategies': [1, 2, 3]  # Strategy IDs
        }
    
    def _get_default_analysis(self) -> Dict:
        """Default analysis when real analysis fails"""
        return {
            'trend_direction': 'SIDEWAYS',
            'confidence_level': 50,
            'ema_signal': 'HOLD',
            'rsi_value': 50.0,
            'macd_signal': 'HOLD',
            'support_level': 2450.0,
            'resistance_level': 2550.0,
            'recommended_strategies': [1, 2]
        }
    
    def get_strategy_recommendations(self, symbol: str, asset_class: str) -> List[Dict]:
        """Get strategy recommendations based on market analysis"""
        try:
            market_analysis = self.get_market_analysis(symbol)
            trend = market_analysis['trend_direction'].lower()
            
            # Map trend to market conditions
            if trend == 'bullish':
                conditions = ['bullish', 'trending']
            elif trend == 'bearish':
                conditions = ['bearish', 'trending']
            else:
                conditions = ['sideways', 'neutral']
            
            # Get strategies that match asset class and market conditions
            strategies = TradingStrategy.query.filter(
                and_(
                    TradingStrategy.is_active == True,
                    TradingStrategy.asset_classes.like(f'%{asset_class}%')
                )
            ).all()
            
            recommendations = []
            for strategy in strategies:
                strategy_conditions = json.loads(strategy.market_conditions)
                if any(cond in strategy_conditions for cond in conditions):
                    recommendations.append({
                        'id': strategy.id,
                        'name': strategy.name,
                        'description': strategy.description,
                        'risk_level': strategy.risk_level,
                        'success_rate': strategy.success_rate,
                        'max_loss_percentage': strategy.max_loss_percentage,
                        'max_profit_percentage': strategy.max_profit_percentage,
                        'confidence_match': market_analysis['confidence_level']
                    })
            
            # Sort by success rate and confidence
            recommendations.sort(key=lambda x: (x['success_rate'], x['confidence_match']), reverse=True)
            return recommendations[:5]  # Top 5 recommendations
            
        except Exception as e:
            return self._get_default_strategies()
    
    def _get_default_strategies(self) -> List[Dict]:
        """Default strategy recommendations"""
        return [
            {
                'id': 1,
                'name': 'EMA Crossover',
                'description': 'Safe trend-following strategy',
                'risk_level': 'LOW',
                'success_rate': 78.0,
                'max_loss_percentage': 4.0,
                'max_profit_percentage': 8.0,
                'confidence_match': 75
            }
        ]
    
    def create_trade_recommendation(self, user_id: int, trade_data: Dict) -> Dict:
        """Create a new trade recommendation"""
        try:
            # Validate asset
            asset = TradingAsset.query.filter_by(symbol=trade_data['symbol']).first()
            if not asset:
                return {'success': False, 'error': 'Invalid asset symbol'}
            
            # Validate strategy
            strategy = TradingStrategy.query.get(trade_data['strategy_id'])
            if not strategy:
                return {'success': False, 'error': 'Invalid strategy'}
            
            # Get market analysis for context
            market_analysis = self.get_market_analysis(trade_data['symbol'])
            
            # Create recommendation
            recommendation = TradeRecommendation(
                user_id=user_id,
                asset_id=asset.id,
                strategy_id=strategy.id,
                symbol=trade_data['symbol'],
                quantity=int(trade_data['quantity']),
                order_type=trade_data['order_type'],
                product_type=trade_data['product_type'],
                expiry_date=trade_data.get('expiry_date'),
                strike_price=trade_data.get('strike_price'),
                option_type=trade_data.get('option_type'),
                entry_price=trade_data.get('entry_price'),
                target_price=trade_data.get('target_price'),
                stop_loss=trade_data.get('stop_loss'),
                max_loss=trade_data.get('max_loss'),
                max_profit=trade_data.get('max_profit'),
                market_direction=market_analysis['trend_direction'],
                confidence_score=market_analysis['confidence_level'],
                analysis_context=trade_data.get('analysis_context', 'AI-generated recommendation')
            )
            
            db.session.add(recommendation)
            db.session.commit()
            
            return {
                'success': True,
                'recommendation_id': recommendation.id,
                'message': 'Trade recommendation created successfully'
            }
            
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def deploy_trade(self, user_id: int, recommendation_id: int) -> Dict:
        """Deploy trade to broker (routing to broker API)"""
        try:
            # First check user's pricing plan and trading permissions (tenant-scoped)
            from models import User, PricingPlan
            user = TenantQuery(User).filter_by(id=user_id).first()
            
            if not user:
                return {'success': False, 'error': 'User not found'}
                
            # CRITICAL SECURITY CHECK: Enforce pricing plan restrictions
            if user.pricing_plan == PricingPlan.TARGET_PLUS:
                return {
                    'success': False, 
                    'error': 'Target Plus plan allows portfolio analysis only. Upgrade to Target Pro for trade execution.'
                }
            
            if user.pricing_plan == PricingPlan.HNI:
                return {
                    'success': False, 
                    'error': 'HNI accounts are managed by our account executives. Your account manager will execute trades on your behalf. Contact your account executive for trade requests.'
                }
            
            if user.pricing_plan != PricingPlan.TARGET_PRO:
                return {
                    'success': False, 
                    'error': 'Direct trade execution is only available for Target Pro subscription.'
                }
            
            recommendation = TenantQuery(TradeRecommendation).filter_by(
                id=recommendation_id,
                user_id=user_id
            ).first()
            
            if not recommendation:
                return {'success': False, 'error': 'Recommendation not found'}
            
            if recommendation.status != 'PENDING':
                return {'success': False, 'error': 'Recommendation already processed'}
            
            # Get user's primary broker account (tenant-scoped)
            broker_account = TenantQuery(BrokerAccount).filter_by(
                user_id=user_id,
                is_primary=True
            ).first()
            
            if not broker_account:
                return {'success': False, 'error': 'No primary broker account found'}
            
            # SECURITY CHECK: Ensure trading is only allowed with primary broker
            if not broker_account.is_primary:
                return {
                    'success': False, 
                    'error': 'Trading is only allowed with your primary broker. Please set this broker as primary to trade.'
                }
            
            # Create trade execution record with tenant_id
            execution = create_for_tenant(TradeExecution,
                user_id=user_id,
                recommendation_id=recommendation_id,
                broker_account_id=broker_account.id,
                symbol=recommendation.symbol,
                quantity=recommendation.quantity,
                order_type=recommendation.order_type,
                product_type=recommendation.product_type,
                order_price=recommendation.entry_price
            )
            
            # In production, here we would call the actual broker API
            # For demo, simulate execution
            execution_result = self._simulate_broker_execution(execution, broker_account)
            
            if execution_result['success']:
                execution.execution_status = 'FILLED'
                execution.broker_order_id = execution_result['order_id']
                execution.executed_price = execution_result['executed_price']
                execution.executed_quantity = recommendation.quantity
                execution.executed_at = datetime.utcnow()
                
                # Update recommendation status
                recommendation.status = 'EXECUTED'
                recommendation.deployed_at = datetime.utcnow()
                
                # Create active trade record with tenant_id
                active_trade = create_for_tenant(ActiveTrade,
                    user_id=user_id,
                    execution_id=execution.id,
                    strategy_name=recommendation.strategy.name,
                    symbol=recommendation.symbol,
                    quantity=recommendation.quantity,
                    entry_price=execution_result['executed_price'],
                    stop_loss=recommendation.stop_loss,
                    target_price=recommendation.target_price,
                    position_type='LONG'  # Simplified for demo
                )
                
                db.session.add(execution)
                db.session.add(active_trade)
                db.session.commit()
                
                return {
                    'success': True,
                    'execution_id': execution.id,
                    'broker_order_id': execution_result['order_id'],
                    'executed_price': execution_result['executed_price'],
                    'message': 'Trade executed successfully'
                }
            else:
                execution.execution_status = 'REJECTED'
                execution.error_message = execution_result['error']
                recommendation.status = 'REJECTED'
                
                db.session.add(execution)
                db.session.commit()
                
                return {
                    'success': False,
                    'error': execution_result['error']
                }
                
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def _simulate_broker_execution(self, execution: TradeExecution, broker_account) -> Dict:
        """Simulate broker execution (replace with actual broker API calls)"""
        try:
            # Simulate order processing
            import time
            time.sleep(0.5)  # Simulate network delay
            
            # Mock execution (90% success rate)
            if random.random() < 0.9:
                # Simulate slight price slippage
                base_price = execution.order_price or 2500
                slippage = random.uniform(-0.5, 0.5) / 100  # ±0.5% slippage
                executed_price = base_price * (1 + slippage)
                
                return {
                    'success': True,
                    'order_id': f"ORD_{random.randint(100000, 999999)}",
                    'executed_price': round(executed_price, 2)
                }
            else:
                return {
                    'success': False,
                    'error': 'Insufficient margin or market conditions'
                }
                
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def get_user_trades(self, user_id: int, status: str = 'all') -> Dict:
        """Get user's trade data"""
        try:
            result = {
                'active_trades': [],
                'recent_history': [],
                'recommendations': []
            }
            
            # Get active trades (tenant-scoped)
            active_trades = TenantQuery(ActiveTrade).filter_by(
                user_id=user_id,
                is_active=True
            ).order_by(desc(ActiveTrade.entry_time)).all()
            
            for trade in active_trades:
                # Calculate unrealized P&L (simplified)
                current_price = self._get_current_price(trade.symbol)
                unrealized_pnl = (current_price - trade.entry_price) * trade.quantity
                
                result['active_trades'].append({
                    'id': trade.id,
                    'symbol': trade.symbol,
                    'strategy': trade.strategy_name,
                    'quantity': trade.quantity,
                    'entry_price': trade.entry_price,
                    'current_price': current_price,
                    'unrealized_pnl': unrealized_pnl,
                    'entry_time': trade.entry_time,
                    'stop_loss': trade.stop_loss,
                    'target_price': trade.target_price
                })
            
            # Get recent trade history (tenant-scoped)
            history = TenantQuery(TradeHistory).filter_by(user_id=user_id).order_by(
                desc(TradeHistory.exit_time)
            ).all()[:10]
            
            for record in history:
                result['recent_history'].append({
                    'id': record.id,
                    'symbol': record.symbol,
                    'strategy': record.strategy_name,
                    'quantity': record.quantity,
                    'entry_price': record.entry_price,
                    'exit_price': record.exit_price,
                    'realized_pnl': record.realized_pnl,
                    'pnl_percentage': record.pnl_percentage,
                    'trade_result': record.trade_result,
                    'exit_reason': record.exit_reason,
                    'holding_period': record.holding_period_hours,
                    'exit_time': record.exit_time
                })
            
            # Get pending recommendations (tenant-scoped)
            recommendations = TenantQuery(TradeRecommendation).filter_by(
                user_id=user_id,
                status='PENDING'
            ).order_by(desc(TradeRecommendation.recommendation_date)).all()
            
            for rec in recommendations:
                result['recommendations'].append({
                    'id': rec.id,
                    'symbol': rec.symbol,
                    'strategy': rec.strategy.name,
                    'quantity': rec.quantity,
                    'order_type': rec.order_type,
                    'entry_price': rec.entry_price,
                    'target_price': rec.target_price,
                    'stop_loss': rec.stop_loss,
                    'confidence_score': rec.confidence_score,
                    'recommendation_date': rec.recommendation_date
                })
            
            return {'success': True, 'data': result}
            
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def _get_current_price(self, symbol: str) -> float:
        """Get live price for a single symbol using the real price service."""
        try:
            from services.stock_price_service import stock_price_service
            result = stock_price_service.get_real_price(symbol)
            price = result.get('price', 0)
            if price and float(price) > 0:
                return float(price)
        except Exception:
            pass
        return 0.0

    def get_all_assets(self) -> List[Dict]:
        """Get all available trading assets.
        Returns DB-stored prices; the Trade Now UI fetches live prices per-asset via AJAX.
        """
        try:
            assets = TradingAsset.query.filter_by(is_active=True).all()
            return [
                {
                    'id': asset.id,
                    'symbol': asset.symbol,
                    'company_name': asset.company_name,
                    'asset_class': asset.asset_class,
                    'exchange': asset.exchange,
                    'sector': asset.sector,
                    'current_price': float(asset.current_price) if asset.current_price else 0,
                    'lot_size': asset.lot_size
                }
                for asset in assets
            ]
        except Exception as e:
            return []
    
    def get_all_strategies(self) -> List[Dict]:
        """Get all available trading strategies"""
        try:
            strategies = TradingStrategy.query.filter_by(is_active=True).all()
            return [
                {
                    'id': strategy.id,
                    'name': strategy.name,
                    'description': strategy.description,
                    'strategy_type': strategy.strategy_type,
                    'asset_classes': json.loads(strategy.asset_classes),
                    'risk_level': strategy.risk_level,
                    'success_rate': strategy.success_rate,
                    'max_loss_percentage': strategy.max_loss_percentage,
                    'max_profit_percentage': strategy.max_profit_percentage
                }
                for strategy in strategies
            ]
        except Exception as e:
            return []