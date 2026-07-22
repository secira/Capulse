"""
WhatsApp and Telegram Configuration for Capulse Trading Platform
Configure group/channel settings for sharing trading signals
"""

import os
import requests
import json
from datetime import datetime

# WhatsApp Business API Configuration
WHATSAPP_API_TOKEN = os.environ.get('WHATSAPP_API_TOKEN', '')
WHATSAPP_PHONE_NUMBER_ID = os.environ.get('WHATSAPP_PHONE_NUMBER_ID', '')
WHATSAPP_GROUP_CHAT_ID = os.environ.get('WHATSAPP_GROUP_CHAT_ID', '')

# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHANNEL_ID = os.environ.get('TELEGRAM_CHANNEL_ID', '')  # Format: @channelname or -100123456789

class WhatsAppService:
    """Service for sending trading signals to WhatsApp groups"""
    
    def __init__(self):
        self.api_token = WHATSAPP_API_TOKEN
        self.phone_number_id = WHATSAPP_PHONE_NUMBER_ID
        self.group_chat_id = WHATSAPP_GROUP_CHAT_ID
        self.base_url = f"https://graph.facebook.com/v18.0/{self.phone_number_id}/messages"
    
    def is_configured(self):
        """Check if WhatsApp is properly configured"""
        return all([self.api_token, self.phone_number_id, self.group_chat_id])
    
    def format_signal_message(self, signal):
        """Format trading signal for WhatsApp"""
        message = f"🔔 *Capulse Trading Signal*\n\n"
        message += f"📈 *{signal.symbol}*"
        if signal.company_name:
            message += f" ({signal.company_name})"
        message += f"\n\n"
        
        # Action with emoji
        action_emoji = "🟢" if signal.action == "BUY" else "🔴" if signal.action == "SELL" else "🟡"
        message += f"{action_emoji} *Action:* {signal.action}\n"
        
        if signal.entry_price:
            message += f"💰 *Entry Price:* ₹{signal.entry_price:.2f}\n"
        if signal.target_price:
            message += f"🎯 *Target:* ₹{signal.target_price:.2f}\n"
        if signal.stop_loss:
            message += f"🛑 *Stop Loss:* ₹{signal.stop_loss:.2f}\n"
        if signal.quantity:
            message += f"📊 *Quantity:* {signal.quantity}\n"
        if signal.risk_level:
            message += f"⚠️ *Risk:* {signal.risk_level}\n"
        if signal.time_frame:
            message += f"⏰ *Time Frame:* {signal.time_frame}\n"
        
        if signal.notes:
            message += f"\n📝 *Analysis:*\n{signal.notes[:200]}"
            if len(signal.notes) > 200:
                message += "..."
        
        message += f"\n\n⏰ Signal Time: {signal.created_at.strftime('%d/%m/%Y %I:%M %p')}"
        message += f"\n\n_Join Capulse for more signals: https://www.capulse.tech_"
        
        return message
    
    def send_signal(self, signal):
        """Send trading signal to WhatsApp group"""
        if not self.is_configured():
            return {"success": False, "error": "WhatsApp not configured"}
        
        try:
            message_text = self.format_signal_message(signal)
            
            headers = {
                'Authorization': f'Bearer {self.api_token}',
                'Content-Type': 'application/json'
            }
            
            payload = {
                "messaging_product": "whatsapp",
                "to": self.group_chat_id,
                "type": "text",
                "text": {
                    "body": message_text
                }
            }
            
            response = requests.post(self.base_url, headers=headers, json=payload)
            
            if response.status_code == 200:
                return {"success": True, "message": "Signal sent to WhatsApp successfully"}
            else:
                return {"success": False, "error": f"WhatsApp API error: {response.text}"}
                
        except Exception as e:
            return {"success": False, "error": f"WhatsApp send error: {str(e)}"}

class TelegramService:
    """Service for sending trading signals to Telegram channels"""
    
    def __init__(self):
        self.bot_token = TELEGRAM_BOT_TOKEN
        self.channel_id = TELEGRAM_CHANNEL_ID
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
    
    def is_configured(self):
        """Check if Telegram is properly configured"""
        return all([self.bot_token, self.channel_id])
    
    def format_signal_message(self, signal):
        """Format trading signal for Telegram with HTML formatting"""
        message = f"🔔 <b>Capulse Trading Signal</b>\n\n"
        message += f"📈 <b>{signal.symbol}</b>"
        if signal.company_name:
            message += f" ({signal.company_name})"
        message += f"\n\n"
        
        # Action with emoji
        action_emoji = "🟢" if signal.action == "BUY" else "🔴" if signal.action == "SELL" else "🟡"
        message += f"{action_emoji} <b>Action:</b> {signal.action}\n"
        
        if signal.entry_price:
            message += f"💰 <b>Entry Price:</b> ₹{signal.entry_price:.2f}\n"
        if signal.target_price:
            message += f"🎯 <b>Target:</b> ₹{signal.target_price:.2f}\n"
        if signal.stop_loss:
            message += f"🛑 <b>Stop Loss:</b> ₹{signal.stop_loss:.2f}\n"
        if signal.quantity:
            message += f"📊 <b>Quantity:</b> {signal.quantity}\n"
        if signal.risk_level:
            message += f"⚠️ <b>Risk:</b> {signal.risk_level}\n"
        if signal.time_frame:
            message += f"⏰ <b>Time Frame:</b> {signal.time_frame}\n"
        
        # Potential return calculation
        if signal.entry_price and signal.target_price:
            potential_return = ((signal.target_price - signal.entry_price) / signal.entry_price) * 100
            message += f"📊 <b>Potential Return:</b> {potential_return:.1f}%\n"
        
        if signal.notes:
            message += f"\n📝 <b>Analysis:</b>\n<i>{signal.notes[:300]}"
            if len(signal.notes) > 300:
                message += "..."
            message += "</i>"
        
        message += f"\n\n⏰ <i>Signal Time: {signal.created_at.strftime('%d/%m/%Y %I:%M %p')}</i>"
        message += f"\n\n<a href='https://www.capulse.tech'>Join Capulse for more signals</a>"
        
        return message
    
    def send_signal(self, signal):
        """Send trading signal to Telegram channel"""
        if not self.is_configured():
            return {"success": False, "error": "Telegram not configured"}
        
        try:
            message_text = self.format_signal_message(signal)
            
            payload = {
                "chat_id": self.channel_id,
                "text": message_text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }
            
            response = requests.post(f"{self.base_url}/sendMessage", json=payload)
            
            if response.status_code == 200:
                return {"success": True, "message": "Signal sent to Telegram successfully"}
            else:
                return {"success": False, "error": f"Telegram API error: {response.text}"}
                
        except Exception as e:
            return {"success": False, "error": f"Telegram send error: {str(e)}"}

# Initialize services
whatsapp_service = WhatsAppService()
telegram_service = TelegramService()

def send_signal_to_platforms(signal, platforms):
    """
    Send trading signal to specified platforms
    Args:
        signal: TradingSignal object
        platforms: list of platforms ['whatsapp', 'telegram']
    Returns:
        dict with results for each platform
    """
    results = {}
    
    if 'whatsapp' in platforms:
        results['whatsapp'] = whatsapp_service.send_signal(signal)
    
    if 'telegram' in platforms:
        results['telegram'] = telegram_service.send_signal(signal)
    
    return results

def get_platform_status():
    """Get configuration status of all platforms"""
    return {
        'whatsapp': {
            'configured': whatsapp_service.is_configured(),
            'settings': {
                'api_token': bool(WHATSAPP_API_TOKEN),
                'phone_number_id': bool(WHATSAPP_PHONE_NUMBER_ID),
                'group_chat_id': bool(WHATSAPP_GROUP_CHAT_ID)
            }
        },
        'telegram': {
            'configured': telegram_service.is_configured(),
            'settings': {
                'bot_token': bool(TELEGRAM_BOT_TOKEN),
                'channel_id': bool(TELEGRAM_CHANNEL_ID)
            }
        }
    }

if __name__ == "__main__":
    # Test configuration
    status = get_platform_status()
    print("Platform Configuration Status:")
    print(json.dumps(status, indent=2))
    
    if not status['whatsapp']['configured']:
        print("\nWhatsApp Configuration Required:")
        print("1. Set WHATSAPP_API_TOKEN environment variable")
        print("2. Set WHATSAPP_PHONE_NUMBER_ID environment variable") 
        print("3. Set WHATSAPP_GROUP_CHAT_ID environment variable")
    
    if not status['telegram']['configured']:
        print("\nTelegram Configuration Required:")
        print("1. Set TELEGRAM_BOT_TOKEN environment variable")
        print("2. Set TELEGRAM_CHANNEL_ID environment variable")