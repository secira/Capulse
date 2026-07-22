# Mobile OTP Authentication Routes
from flask import request, render_template, redirect, url_for, flash, jsonify, session
from flask_login import login_user, logout_user, login_required, current_user
from app import app, db
from models import User
from services.otp_service import otp_service
from services.sms_service import sms_service

@app.route('/mobile-register', methods=['GET', 'POST'])
def mobile_register():
    """Mobile number registration with OTP verification"""
    if current_user.is_authenticated:
        return redirect(url_for('chat.chat_home'))
    
    if request.method == 'POST':
        mobile_number = request.form.get('mobile_number', '').strip()
        
        if not mobile_number:
            flash('Please enter your mobile number.', 'error')
            return render_template('auth/mobile_register.html')
        
        # Validate mobile number format
        if not sms_service.validate_mobile_number(mobile_number):
            flash('Please enter a valid Indian mobile number.', 'error')
            return render_template('auth/mobile_register.html')
        
        # Send OTP
        success, message = otp_service.send_otp_to_mobile(mobile_number, "registration")

        if success:
            session['mobile_number'] = sms_service.format_mobile_number(mobile_number)
            session['otp_purpose'] = 'registration'
            if message and message.startswith('DEV_OTP:'):
                dev_otp = message.split(':', 1)[1]
                flash(f'OTP: {dev_otp} (SMS delivery unavailable — use this code to continue)', 'warning')
            else:
                flash('OTP sent to your mobile number. Please verify to continue.', 'info')
            return redirect(url_for('verify_mobile_otp'))
        else:
            flash(message, 'error')
            return render_template('auth/mobile_register.html')
    
    return render_template('auth/mobile_register.html')

@app.route('/mobile-login', methods=['GET', 'POST'])
def mobile_login():
    """Mobile number login with OTP verification - auto-registers new users"""
    if current_user.is_authenticated:
        return redirect(url_for('chat.chat_home'))
    
    if request.method == 'POST':
        mobile_number = request.form.get('mobile_number', '').strip()
        
        if not mobile_number:
            flash('Please enter your mobile number.', 'error')
            return render_template('auth/mobile_login.html')
        
        # Validate mobile number format
        if not sms_service.validate_mobile_number(mobile_number):
            flash('Please enter a valid Indian mobile number.', 'error')
            return render_template('auth/mobile_login.html')
        
        # Send OTP (will auto-create user if doesn't exist)
        formatted_mobile = sms_service.format_mobile_number(mobile_number)
        success, message = otp_service.send_otp_to_mobile(mobile_number, "login")
        
        if success:
            session['mobile_number'] = formatted_mobile
            # Check if user exists to determine if they need to complete profile
            user = User.query.filter_by(mobile_number=formatted_mobile).first()
            if user and user.email and user.password_hash:
                session['otp_purpose'] = 'login'
            else:
                session['otp_purpose'] = 'registration'

            if message and message.startswith('DEV_OTP:'):
                dev_otp = message.split(':', 1)[1]
                flash(f'OTP: {dev_otp} (SMS delivery unavailable — use this code to continue)', 'warning')
            else:
                flash('OTP sent to your mobile number. Please verify to continue.', 'info')
            return redirect(url_for('verify_mobile_otp'))
        else:
            flash(message, 'error')
            return render_template('auth/mobile_login.html')
    
    return render_template('auth/mobile_login.html')

@app.route('/verify-mobile-otp', methods=['GET', 'POST'])
def verify_mobile_otp():
    """Verify OTP for mobile number"""
    mobile_number = session.get('mobile_number')
    otp_purpose = session.get('otp_purpose', 'verification')
    
    if not mobile_number:
        flash('Session expired. Please try again.', 'error')
        return redirect(url_for('mobile_register'))
    
    if request.method == 'POST':
        otp = request.form.get('otp', '').strip()
        
        if not otp:
            flash('Please enter the OTP sent to your mobile.', 'error')
            return render_template('auth/verify_otp.html', mobile_number=mobile_number[-4:])
        
        # Verify OTP
        success, message, user = otp_service.verify_otp(mobile_number, otp)
        
        if success and user:
            # Clear session data
            session.pop('mobile_number', None)
            session.pop('otp_purpose', None)
            
            # Log in the user
            login_user(user)
            flash('Mobile number verified successfully!', 'success')
            
            # Redirect based on purpose
            if otp_purpose == 'registration':
                # For new registrations, redirect to complete profile
                return redirect(url_for('complete_profile'))
            else:
                # For login, go to chat
                return redirect(url_for('chat.chat_home'))
        else:
            flash(message, 'error')
            return render_template('auth/verify_otp.html', mobile_number=mobile_number[-4:])
    
    return render_template('auth/verify_otp.html', mobile_number=mobile_number[-4:])

@app.route('/resend-otp', methods=['POST'])
def resend_otp():
    """Resend OTP to mobile number"""
    mobile_number = session.get('mobile_number')
    otp_purpose = session.get('otp_purpose', 'verification')
    
    if not mobile_number:
        return jsonify({'success': False, 'message': 'Session expired. Please try again.'})
    
    # Send OTP
    success, message = otp_service.send_otp_to_mobile(mobile_number, otp_purpose)

    if success and message and message.startswith('DEV_OTP:'):
        dev_otp = message.split(':', 1)[1]
        display_message = f'OTP: {dev_otp} (SMS delivery unavailable — use this code to continue)'
    else:
        display_message = message

    return jsonify({'success': success, 'message': display_message})

@app.route('/complete-profile', methods=['GET', 'POST'])
@login_required
def complete_profile():
    """Complete user profile after mobile registration"""
    if request.method == 'POST':
        first_name = request.form.get('first_name', '').strip()
        last_name = request.form.get('last_name', '').strip()
        email = request.form.get('email', '').strip()
        password = request.form.get('password', '').strip()
        
        # Update user profile
        if first_name:
            current_user.first_name = first_name
        if last_name:
            current_user.last_name = last_name
        if email:
            # Check if email is already used
            existing_user = User.query.filter_by(email=email).first()
            if existing_user and existing_user.id != current_user.id:
                flash('Email address already registered.', 'error')
                return render_template('auth/complete_profile.html')
            current_user.email = email
        
        if password:
            if len(password) < 6:
                flash('Password must be at least 6 characters long.', 'error')
                return render_template('auth/complete_profile.html')
            current_user.set_password(password)
        
        # Generate proper username if not set
        if not current_user.username or current_user.username.startswith('+91'):
            if first_name and last_name:
                base_username = f"{first_name.lower()}{last_name.lower()}"
            elif first_name:
                base_username = first_name.lower()
            else:
                base_username = f"user{current_user.id}"
            
            # Ensure username is unique
            counter = 1
            username = base_username
            while User.query.filter_by(username=username).first():
                username = f"{base_username}{counter}"
                counter += 1
            
            current_user.username = username
        
        db.session.commit()
        flash('Profile completed successfully!', 'success')
        return redirect(url_for('chat.chat_home'))
    
    return render_template('auth/complete_profile.html')