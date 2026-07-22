#!/usr/bin/env python3
"""
Test script to verify WhatsApp and Telegram configuration
Run this to check if your messaging setup is working correctly
"""

import os
from config_whatsapp_telegram import get_platform_status, whatsapp_service, telegram_service

def main():
    print("🔧 Capulse Messaging Configuration Test")
    print("=" * 50)
    
    # Check environment variables
    print("\n📋 Environment Variables:")
    whatsapp_vars = {
        'WHATSAPP_API_TOKEN': bool(os.environ.get('WHATSAPP_API_TOKEN')),
        'WHATSAPP_PHONE_NUMBER_ID': bool(os.environ.get('WHATSAPP_PHONE_NUMBER_ID')),
        'WHATSAPP_GROUP_CHAT_ID': bool(os.environ.get('WHATSAPP_GROUP_CHAT_ID'))
    }
    
    telegram_vars = {
        'TELEGRAM_BOT_TOKEN': bool(os.environ.get('TELEGRAM_BOT_TOKEN')),
        'TELEGRAM_CHANNEL_ID': bool(os.environ.get('TELEGRAM_CHANNEL_ID'))
    }
    
    print("WhatsApp:")
    for var, status in whatsapp_vars.items():
        status_icon = "✅" if status else "❌"
        print(f"  {status_icon} {var}: {'Set' if status else 'Not set'}")
    
    print("\nTelegram:")
    for var, status in telegram_vars.items():
        status_icon = "✅" if status else "❌"
        print(f"  {status_icon} {var}: {'Set' if status else 'Not set'}")
    
    # Check service configuration
    print("\n🔍 Service Configuration:")
    status = get_platform_status()
    
    whatsapp_configured = status['whatsapp']['configured']
    telegram_configured = status['telegram']['configured']
    
    print(f"📱 WhatsApp: {'✅ Configured' if whatsapp_configured else '❌ Not configured'}")
    print(f"📱 Telegram: {'✅ Configured' if telegram_configured else '❌ Not configured'}")
    
    # Configuration instructions
    if not whatsapp_configured:
        print("\n📋 WhatsApp Setup Required:")
        print("1. Create WhatsApp Business API account at https://developers.facebook.com/")
        print("2. Get your Access Token and Phone Number ID")
        print("3. Create a WhatsApp group for trading signals")
        print("4. capulse.tech says: Add these to your environment secrets:")
        print("   - WHATSAPP_API_TOKEN")
        print("   - WHATSAPP_PHONE_NUMBER_ID") 
        print("   - WHATSAPP_GROUP_CHAT_ID")
    
    if not telegram_configured:
        print("\n📋 Telegram Setup Required:")
        print("1. Create a bot by messaging @BotFather on Telegram")
        print("2. Create a channel for trading signals")
        print("3. Add your bot as admin to the channel")
        print("4. capulse.tech says: Add these to your environment secrets:")
        print("   - TELEGRAM_BOT_TOKEN")
        print("   - TELEGRAM_CHANNEL_ID")
    
    if whatsapp_configured or telegram_configured:
        print("\n🎉 Ready to Send Trading Signals!")
        if whatsapp_configured:
            print("✅ WhatsApp messages will be sent automatically")
        if telegram_configured:
            print("✅ Telegram messages will be sent automatically")
    
    print("\n📖 For detailed setup instructions, see: setup_messaging_guide.md")
    
    # Test message format (without actually sending)
    print("\n📝 Sample Message Preview:")
    
    class MockSignal:
        def __init__(self):
            self.symbol = "RELIANCE"
            self.company_name = "Reliance Industries Ltd"
            self.action = "BUY"
            self.entry_price = 2840.50
            self.target_price = 3100.00
            self.stop_loss = 2750.00
            self.quantity = 100
            self.risk_level = "MEDIUM"
            self.time_frame = "SWING"
            self.notes = "Strong breakout above resistance level with high volume. RSI showing bullish momentum."
            from datetime import datetime
            self.created_at = datetime.now()
    
    mock_signal = MockSignal()
    
    if whatsapp_configured:
        print("\n📱 WhatsApp Message:")
        print("-" * 30)
        print(whatsapp_service.format_signal_message(mock_signal))
    
    if telegram_configured:
        print("\n📱 Telegram Message:")
        print("-" * 30)
        print(telegram_service.format_signal_message(mock_signal))
    
    print("\n" + "=" * 50)
    print("Test completed! Check the admin dashboard to create and share signals.")

if __name__ == "__main__":
    main()