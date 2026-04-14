"""
Razorpay Payment Service for Target Capital
Handles subscription payments, upgrades, and payment verification
"""

import os
try:
    import razorpay
except ImportError:
    razorpay = None
import hmac
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class RazorpayService:
    def __init__(self):
        """Initialize Razorpay client with API credentials"""
        self.key_id = os.environ.get('RAZORPAY_KEY_ID')
        self.key_secret = os.environ.get('RAZORPAY_KEY_SECRET')
        
        if not razorpay:
            logger.warning("Razorpay package not installed")
            self.client = None
        elif not self.key_id or not self.key_secret:
            logger.warning("Razorpay credentials not found in environment variables")
            self.client = None
        else:
            self.client = razorpay.Client(auth=(self.key_id, self.key_secret))
            logger.info("Razorpay service initialized successfully")
    
    def create_subscription_order(self, user_id: int, plan_type: str, amount: float) -> Dict[str, Any]:
        """Create a Razorpay order for subscription payment"""
        if not self.client:
            return {'success': False, 'error': 'Payment service not configured'}
        
        try:
            # Convert amount to paise (Razorpay uses smallest currency unit)
            amount_paise = int(amount * 100)
            
            order_data = {
                'amount': amount_paise,
                'currency': 'INR',
                'receipt': f'tcapital_sub_{user_id}_{datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")}',
                'notes': {
                    'user_id': str(user_id),
                    'plan_type': plan_type,
                    'subscription': 'true'
                }
            }
            
            order = self.client.order.create(order_data)
            
            return {
                'success': True,
                'order_id': order['id'],
                'amount': amount,
                'currency': order['currency'],
                'key_id': self.key_id
            }
            
        except Exception as e:
            logger.error(f"Error creating Razorpay order: {str(e)}")
            return {'success': False, 'error': str(e)}
    
    def verify_payment_signature(self, razorpay_order_id: str, razorpay_payment_id: str, 
                                razorpay_signature: str) -> bool:
        """Verify payment signature from Razorpay webhook"""
        if not self.key_secret:
            return False
        
        try:
            # Create signature verification string
            body = f"{razorpay_order_id}|{razorpay_payment_id}"
            
            # Generate expected signature
            expected_signature = hmac.new(
                self.key_secret.encode('utf-8'),
                body.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            
            # Compare signatures
            return hmac.compare_digest(expected_signature, razorpay_signature)
            
        except Exception as e:
            logger.error(f"Error verifying payment signature: {str(e)}")
            return False
    
    def get_payment_details(self, payment_id: str) -> Dict[str, Any]:
        """Retrieve payment details from Razorpay"""
        if not self.client:
            return {'success': False, 'error': 'Payment service not configured'}
        
        try:
            payment = self.client.payment.fetch(payment_id)
            
            return {
                'success': True,
                'payment': {
                    'id': payment['id'],
                    'order_id': payment['order_id'],
                    'amount': payment['amount'] / 100,  # Convert from paise
                    'currency': payment['currency'],
                    'status': payment['status'],
                    'method': payment['method'],
                    'created_at': datetime.fromtimestamp(payment['created_at']),
                    'email': payment.get('email'),
                    'contact': payment.get('contact')
                }
            }
            
        except Exception as e:
            logger.error(f"Error fetching payment details: {str(e)}")
            return {'success': False, 'error': str(e)}
    
    def create_refund(self, payment_id: str, amount: Optional[float] = None, 
                     reason: str = "requested_by_customer") -> Dict[str, Any]:
        """Create refund for a payment"""
        if not self.client:
            return {'success': False, 'error': 'Payment service not configured'}
        
        try:
            refund_data = {
                'speed': 'normal',
                'notes': {
                    'reason': reason,
                    'refund_date': datetime.now(timezone.utc).isoformat()
                }
            }
            
            if amount:
                refund_data['amount'] = int(amount * 100)  # Convert to paise
            
            refund = self.client.payment.refund(payment_id, refund_data)
            
            return {
                'success': True,
                'refund': {
                    'id': refund['id'],
                    'payment_id': refund['payment_id'],
                    'amount': refund['amount'] / 100,
                    'currency': refund['currency'],
                    'status': refund['status'],
                    'created_at': datetime.fromtimestamp(refund['created_at'])
                }
            }
            
        except Exception as e:
            logger.error(f"Error creating refund: {str(e)}")
            return {'success': False, 'error': str(e)}
    
    def get_subscription_plans(self) -> Dict[str, Dict[str, Any]]:
        """Get available subscription plans with pricing"""
        return {
            'FREE': {
                'name': 'Free Plan',
                'price': 0,
                'duration_days': 0,
                'features': ['Basic portfolio tracking', 'Limited market data', 'Basic AI insights'],
                'limits': {'portfolios': 1, 'watchlist': 10, 'ai_queries': 5, 'brokers': 0}
            },
            'TARGET_PLUS': {
                'name': 'Target Plus Plan',
                'price': 1499,
                'duration_days': 30,
                'features': ['Advanced charts', 'Real-time data', 'Trading signals', '3 broker connections', 'Portfolio analysis only', 'Basic AI advisor'],
                'limits': {'portfolios': 3, 'watchlist': 50, 'ai_queries': 100, 'brokers': 3, 'trade_brokers': 0}
            },
            'TARGET_PRO': {
                'name': 'Target Pro Plan',
                'price': 2499,
                'duration_days': 30,
                'features': ['All Target Plus features', '3 broker connections', 'Trade Now (select broker per trade)', 'Advanced AI insights', 'Portfolio optimization'],
                'limits': {'portfolios': 10, 'watchlist': 200, 'ai_queries': 500, 'brokers': 3, 'trade_brokers': 1}
            },
            'HNI': {
                'name': 'HNI Account',
                'price': 4999,
                'duration_days': 30,
                'features': ['All Target Pro features', '3 broker connections', 'Trade Now (select broker per trade)', 'Priority support', 'Dedicated account manager'],
                'limits': {'portfolios': -1, 'watchlist': -1, 'ai_queries': -1, 'brokers': 3, 'trade_brokers': 1}
            }
        }
    
    def calculate_plan_upgrade_cost(self, current_plan: str, new_plan: str, 
                                  days_remaining: int = 0) -> Dict[str, Any]:
        """Calculate prorated cost for plan upgrade"""
        plans = self.get_subscription_plans()
        
        if current_plan not in plans or new_plan not in plans:
            return {'success': False, 'error': 'Invalid plan type'}
        
        current_price = plans[current_plan]['price']
        new_price = plans[new_plan]['price']
        
        if new_price <= current_price:
            return {'success': False, 'error': 'Cannot downgrade or same plan'}
        
        # Calculate prorated cost
        price_difference = new_price - current_price
        
        if days_remaining > 0:
            # Prorate based on remaining days
            daily_difference = price_difference / 30  # Assuming monthly plans
            prorated_cost = daily_difference * days_remaining
        else:
            prorated_cost = price_difference
        
        return {
            'success': True,
            'upgrade_cost': round(prorated_cost, 2),
            'current_plan': current_plan,
            'new_plan': new_plan,
            'days_remaining': days_remaining
        }

# Initialize global service instance
razorpay_service = RazorpayService()