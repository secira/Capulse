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
        """Send OTP via SMS - Returns (success: bool, error_message: str or None).

        Return convention:
          (True,  None)              — SMS delivered successfully
          (True,  "SMS_UNAVAILABLE") — SMS could not be sent; caller should
                                       surface the OTP on-screen as a fallback
          (False, "<user message>")  — hard failure; caller should show error
        """
        message_body = f"Your Capulse OTP is: {otp}. Valid for 10 minutes. Do not share this code."

        try:
            if self.client and TWILIO_PHONE_NUMBER:
                # Ensure mobile number is in E.164 format
                if not mobile_number.startswith('+'):
                    mobile_number = f"+91{mobile_number}"

                # Check if trying to send to same number as sender
                if mobile_number == TWILIO_PHONE_NUMBER:
                    return False, "Cannot send OTP to the Twilio sender number. Please use a different mobile number."

                message = self.client.messages.create(
                    body=message_body,
                    from_=TWILIO_PHONE_NUMBER,
                    to=mobile_number
                )
                print(f"✅ OTP sent via SMS to {mobile_number}. SID: {message.sid}")
                return True, None  # genuine delivery — do NOT surface OTP on screen

            else:
                # No Twilio credentials — dev/test fallback
                print(f"📱 NO TWILIO — OTP for {mobile_number}: {otp}")
                return True, "SMS_UNAVAILABLE"

        except Exception as e:
            error_str = str(e)
            print(f"❌ Failed to send OTP to {mobile_number}: {error_str}")

            # 20003 — bad SID / auth token
            if "20003" in error_str or "authenticate" in error_str.lower():
                print(f"🔑 TWILIO AUTH FAILURE (20003) — check TWILIO_ACCOUNT_SID / TWILIO_AUTH_TOKEN in Secrets.")
                return True, "SMS_UNAVAILABLE"

            # 21608 — trial account: destination number not verified
            if "21608" in error_str:
                print(f"⚠️ TWILIO TRIAL RESTRICTION (21608) — {mobile_number} is not a verified caller on this trial account.")
                return True, "SMS_UNAVAILABLE"

            # 21219 / 21408 — trial account geographic / sender restrictions
            if "21219" in error_str or "21408" in error_str:
                print(f"⚠️ TWILIO TRIAL GEO RESTRICTION — upgrade to a paid account to send to {mobile_number}.")
                return True, "SMS_UNAVAILABLE"

            # 21659 — sender number not valid for this destination country
            if "21659" in error_str or "country mismatch" in error_str.lower() or "not a Twilio phone number" in error_str:
                print(f"📱 SENDER MISMATCH (21659) — TWILIO_PHONE_NUMBER may not be enabled for India. OTP for {mobile_number}: {otp}")
                return True, "SMS_UNAVAILABLE"

            # 21266 — can't send to own sender number
            if "21266" in error_str or "cannot be the same" in error_str.lower():
                return False, "Cannot send OTP to the Twilio sender number. Please use a different mobile number."

            # 21211 — invalid destination number format
            if "21211" in error_str:
                return False, "Invalid mobile number format. Please enter a valid 10-digit Indian mobile number."

            # Any other error: fall back gracefully so users aren't locked out
            print(f"📱 TWILIO ERROR FALLBACK — OTP for {mobile_number}: {otp}")
            return True, "SMS_UNAVAILABLE"
    
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