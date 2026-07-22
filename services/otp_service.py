# OTP Service for mobile verification
from datetime import datetime, timedelta
import random
import string
from app import db
from models import User
from services.sms_service import sms_service

class OTPService:
    OTP_EXPIRY_MINUTES = 10
    MAX_OTP_ATTEMPTS = 3
    OTP_RESEND_COOLDOWN_MINUTES = 1
    
    @staticmethod
    def generate_otp(length=6):
        """Generate a random numeric OTP"""
        return ''.join(random.choices(string.digits, k=length))
    
    @staticmethod
    def send_otp_to_mobile(mobile_number: str, purpose: str = "verification"):
        """Send OTP to mobile number"""
        # Format and validate mobile number
        if not sms_service.validate_mobile_number(mobile_number):
            return False, "Invalid mobile number format"
        
        formatted_mobile = sms_service.format_mobile_number(mobile_number)
        
        # Check if user exists and get/create user record
        user = User.query.filter_by(mobile_number=formatted_mobile).first()
        if not user:
            # Create new user record for mobile-only signup
            temp_username = formatted_mobile.replace('+', '').replace('-', '')
            user = User()
            user.mobile_number = formatted_mobile
            user.mobile_verified = False
            user.username = temp_username
            user.email = None
            user.password_hash = None
            db.session.add(user)
        
        # Check cooldown period
        if user.last_otp_request:
            time_since_last = datetime.utcnow() - user.last_otp_request
            if time_since_last < timedelta(minutes=OTPService.OTP_RESEND_COOLDOWN_MINUTES):
                remaining_seconds = (timedelta(minutes=OTPService.OTP_RESEND_COOLDOWN_MINUTES) - time_since_last).seconds
                return False, f"Please wait {remaining_seconds} seconds before requesting another OTP"
        
        # Generate and store OTP
        otp = OTPService.generate_otp()
        user.current_otp = otp
        user.otp_expires_at = datetime.utcnow() + timedelta(minutes=OTPService.OTP_EXPIRY_MINUTES)
        user.otp_attempts = 0
        user.last_otp_request = datetime.utcnow()
        
        # Send OTP via SMS
        success, error_msg = sms_service.send_otp(formatted_mobile, otp)

        if success:
            db.session.commit()
            # Only surface the OTP on-screen when SMS was genuinely unavailable
            # (no Twilio credentials, trial restriction, auth failure, etc.).
            # If error_msg is None, Twilio delivered the SMS — do NOT show the OTP.
            if error_msg == "SMS_UNAVAILABLE":
                return True, f"DEV_OTP:{otp}"
            return True, "OTP sent successfully"
        else:
            db.session.rollback()
            return False, error_msg or "Failed to send OTP. Please try again."
    
    @staticmethod
    def verify_otp(mobile_number: str, otp: str):
        formatted_mobile = sms_service.format_mobile_number(mobile_number)
        user = User.query.filter_by(mobile_number=formatted_mobile).first()
        
        if not user:
            return False, "Mobile number not found", None
        
        if not user.current_otp:
            return False, "No OTP found. Please request a new OTP.", None
        
        # Check if OTP has expired
        if datetime.utcnow() > user.otp_expires_at:
            user.current_otp = None
            user.otp_expires_at = None
            db.session.commit()
            return False, "OTP has expired. Please request a new OTP.", None
        
        # Check if too many attempts
        if user.otp_attempts >= OTPService.MAX_OTP_ATTEMPTS:
            user.current_otp = None
            user.otp_expires_at = None
            db.session.commit()
            return False, "Too many failed attempts. Please request a new OTP.", None
        
        # Verify OTP
        if user.current_otp == otp:
            # OTP is correct
            user.mobile_verified = True
            user.current_otp = None
            user.otp_expires_at = None
            user.otp_attempts = 0
            user.last_login = datetime.utcnow()
            db.session.commit()
            return True, "OTP verified successfully", user
        else:
            # OTP is incorrect
            user.otp_attempts += 1
            db.session.commit()
            remaining_attempts = OTPService.MAX_OTP_ATTEMPTS - user.otp_attempts
            return False, f"Invalid OTP. {remaining_attempts} attempts remaining.", None
    
    @staticmethod
    def cleanup_expired_otps():
        """Clean up expired OTPs from database"""
        expired_users = User.query.filter(
            User.otp_expires_at < datetime.utcnow(),
            User.current_otp.isnot(None)
        ).all()
        
        for user in expired_users:
            user.current_otp = None
            user.otp_expires_at = None
            user.otp_attempts = 0
        
        db.session.commit()
        return len(expired_users)

# Global OTP service instance
otp_service = OTPService()