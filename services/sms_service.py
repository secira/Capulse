# SMS OTP Service using Twilio integration
# From blueprint:twilio_send_message integration

import os
import random
import string
from datetime import datetime, timedelta

# Simplified Twilio client import to avoid gevent recursion issues
from twilio.rest import Client

# Use standard client to avoid recursion problems
http_client = None

# Twilio credentials from environment
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN") 
TWILIO_PHONE_NUMBER = os.environ.get("TWILIO_PHONE_NUMBER")

class SMSService:
    def __init__(self):
        if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
            try:
                # Initialize standard Twilio client
                self.client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
                print("✅ Twilio client initialized successfully")
            except Exception as e:
                print(f"❌ Failed to initialize Twilio client: {e}")
                self.client = None
        else:
            self.client = None
            print("⚠️ Twilio credentials not configured - OTP will be logged instead of sent")
    
    def generate_otp(self, length=6):
        """Generate a random OTP"""
        return ''.join(random.choices(string.digits, k=length))
    
    def send_otp(self, mobile_number: str, otp: str) -> tuple:
        """Send OTP via SMS - Returns (success: bool, error_message: str or None)"""
        message_body = f"Your Target Capital OTP is: {otp}. Valid for 10 minutes. Do not share this code."
        
        try:
            if self.client and TWILIO_PHONE_NUMBER:
                # Ensure mobile number is in E.164 format
                if not mobile_number.startswith('+'):
                    # Assume Indian number if no country code
                    mobile_number = f"+91{mobile_number}"
                
                # Check if trying to send to same number
                if mobile_number == TWILIO_PHONE_NUMBER:
                    return False, f"Cannot send OTP to Twilio sender number. Please use a different mobile number to receive OTP."
                
                message = self.client.messages.create(
                    body=message_body,
                    from_=TWILIO_PHONE_NUMBER,
                    to=mobile_number
                )
                print(f"✅ OTP sent via SMS. Message SID: {message.sid}")
                return True, None
            else:
                # Development mode - log OTP instead of sending
                print(f"📱 DEV MODE - OTP for {mobile_number}: {otp}")
                return True, None
                
        except Exception as e:
            error_str = str(e)
            print(f"❌ Failed to send OTP: {error_str}")

            # Authentication failure (20003 = invalid SID/AUTH token)
            # Treat as SMS_UNAVAILABLE in ALL environments so the user can
            # still complete the flow via the on-screen OTP, while admin sees
            # the precise reason in the server logs.
            if "20003" in error_str or "Authenticate" in error_str:
                print(f"🔑 TWILIO AUTH FAILURE (20003) — refresh TWILIO_AUTH_TOKEN in Secrets. "
                      f"OTP for {mobile_number}: {otp}")
                return True, "SMS_UNAVAILABLE"

            # Sender misconfigured (21659 = not a Twilio number / country mismatch)
            # Fall back in ALL environments — OTP is saved to DB and surfaced on screen
            if "21659" in error_str or "country mismatch" in error_str.lower() or "not a Twilio phone number" in error_str:
                print(f"📱 SENDER FALLBACK (21659) - OTP for {mobile_number}: {otp}")
                return True, "SMS_UNAVAILABLE"

            # Same-number error — can't send to your own Twilio sender
            if "21266" in error_str or "cannot be the same" in error_str.lower():
                return False, "Cannot send OTP to the Twilio sender number. Please use a different mobile number."

            # In non-production, fall back to dev mode for any other error
            environment = os.environ.get("ENVIRONMENT", "development")
            if environment != "production":
                print(f"📱 DEV FALLBACK - OTP for {mobile_number}: {otp}")
                return True, "SMS_UNAVAILABLE"

            # Production: surface specific error messages
            if "21211" in error_str:
                return False, "Invalid mobile number format. Please enter a valid phone number."
            elif "21608" in error_str:
                return False, "This phone number is not verified. Please contact support."
            else:
                return False, "Failed to send OTP. Please try again later."
    
    def format_mobile_number(self, mobile_number: str) -> str:
        """Format mobile number to standard format"""
        # Remove all non-digit characters
        mobile_number = ''.join(filter(str.isdigit, mobile_number))
        
        # Add country code for Indian numbers
        if len(mobile_number) == 10:
            mobile_number = f"+91{mobile_number}"
        elif len(mobile_number) == 12 and mobile_number.startswith('91'):
            mobile_number = f"+{mobile_number}"
        elif not mobile_number.startswith('+'):
            mobile_number = f"+{mobile_number}"
            
        return mobile_number
    
    def validate_mobile_number(self, mobile_number: str) -> bool:
        """Validate Indian mobile number format"""
        # Remove all non-digit characters
        digits_only = ''.join(filter(str.isdigit, mobile_number))
        
        # Check for valid Indian mobile number
        if len(digits_only) == 10:
            # Indian mobile numbers start with 6, 7, 8, or 9
            return digits_only[0] in ['6', '7', '8', '9']
        elif len(digits_only) == 12:
            # With country code +91
            return digits_only.startswith('91') and digits_only[2] in ['6', '7', '8', '9']
        
        return False

# Global SMS service instance
sms_service = SMSService()