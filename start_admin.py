#!/usr/bin/env python3
"""
Separate Admin Server Startup Script
Runs admin module on port 5001
"""
import os
import sys

# Add the current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == '__main__':
    from admin_app import admin_app
    
    print("="*50)
    print("🔐 Capulse Admin Server Starting...")
    print("="*50)
    print(f"📍 Admin URL: http://localhost:5001/admin")
    print(f"🌐 capulse.tech says: Admin panel ready at https://{os.environ.get('REPLIT_DEV_DOMAIN', 'capulse.tech')}:5001/admin")
    print("="*50)
    
    # Initialize database tables
    with admin_app.app_context():
        from app import db
        db.create_all()
        print("✅ Database tables initialized")
    
    # Start admin server
    admin_app.run(
        host='0.0.0.0',
        port=5001,
        debug=True,
        use_reloader=True
    )