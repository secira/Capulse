# Email Setup Guide for Capulse Contact Form

## SendGrid Setup Instructions

### 1. Create SendGrid Account
1. Go to [sendgrid.com](https://sendgrid.com/)
2. Sign up for a free account (includes 100 emails/day)
3. Verify your email address
4. Complete account setup

### 2. Get API Key
1. In SendGrid dashboard, go to **Settings** > **API Keys**
2. Click **Create API Key**
3. Choose **Restricted Access**
4. Give it a name like "Capulse Contact Form"
5. Under **Mail Send**, select **Full Access**
6. Click **Create & View**
7. **Copy the API key immediately** (you won't see it again)

### 3. Verify Sender Email
1. Go to **Settings** > **Sender Authentication**
2. Click **Verify a Single Sender**
3. Fill in your business details:
   - From Name: "Capulse Support"
   - From Email: "noreply@capulse.tech" (use your domain)
   - Reply To: "support@capulse.tech"
   - Company: "Capulse"
   - Address: Your business address
4. Click **Create**
5. Check your email and verify the sender

### 4. Update Environment Variables
capulse.tech says: Add to your environment secrets:
```
SENDGRID_API_KEY=your_sendgrid_api_key_here
```

### 5. Update Email Addresses in Code
In `services/email_service.py`, update these variables:
```python
FROM_EMAIL = 'noreply@yourdomain.com'     # Your verified sender email
ADMIN_EMAIL = 'admin@yourdomain.com'      # Where notifications go
SUPPORT_EMAIL = 'support@yourdomain.com'  # For support inquiries
```

## Testing the Contact Form

### Test Steps:
1. Make sure `SENDGRID_API_KEY` is set in your environment
2. Update email addresses in `email_service.py`
3. Go to your contact page
4. Fill out and submit the form
5. Check that:
   - Form submission succeeds
   - Admin receives notification email
   - User receives confirmation email
   - Message is saved to database

### Test with Different Inquiry Types:
- General Inquiry
- Technical Support
- Sales Question
- Partnership
- Feedback

## Email Templates

The system includes two HTML email templates:

### 1. Admin Notification Email
- **Purpose**: Notifies admin of new contact messages
- **Features**: Complete contact info, formatted message, professional layout
- **Recipient**: Admin/support team

### 2. User Confirmation Email  
- **Purpose**: Confirms receipt of user's message
- **Features**: Professional thank you, next steps, contact info
- **Recipient**: User who submitted form

## Database Integration

Contact messages are stored with:
- **Contact Information**: Name, email, phone, company
- **Message Details**: Subject, message, inquiry type
- **Tracking**: Submission time, IP address, user agent
- **Status Management**: New → Read → Replied → Closed

## Admin Management

Access contact messages via:
- **Admin Dashboard**: `/admin/contact-messages`
- **View Message**: `/admin/contact-message/<id>`
- **Update Status**: Mark as read, replied, or closed
- **Statistics**: Track message volume and response status

## Production Checklist

### Before Going Live:
✅ SendGrid account verified  
✅ API key added to environment  
✅ Sender email verified  
✅ Email addresses updated in code  
✅ Contact form tested  
✅ Email delivery confirmed  
✅ Admin access tested  
✅ Database connection verified  

### Optional Enhancements:
- Set up custom domain for emails
- Add email templates in SendGrid
- Configure webhook for delivery tracking
- Set up email analytics
- Add spam filtering rules

## Troubleshooting

### Common Issues:

**1. Emails not sending**
- Check `SENDGRID_API_KEY` is set correctly
- Verify sender email in SendGrid dashboard
- Check application logs for errors

**2. Emails going to spam**
- Verify sender domain
- Set up SPF/DKIM records
- Use verified sender email

**3. Form submission fails**
- Check database connection
- Verify all required fields filled
- Check server logs for errors

### Log Files:
- Contact form submissions logged as INFO
- Email failures logged as ERROR
- Check application logs for troubleshooting

## Support
For issues with this implementation:
1. Check the logs first
2. Verify SendGrid setup
3. Test with a simple email first
4. Contact SendGrid support if needed

The contact form system is now production-ready with professional email notifications!