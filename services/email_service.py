"""
Email service for sending notifications using SendGrid
"""
import os
import logging
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Email, To, Content
from datetime import datetime

# Configuration
SENDGRID_API_KEY = os.environ.get('SENDGRID_API_KEY')
FROM_EMAIL = 'noreply@tcapital.com'  # Replace with your verified sender email
ADMIN_EMAIL = 'admin@tcapital.com'   # Replace with your admin email
SUPPORT_EMAIL = 'support@tcapital.com'  # Replace with your support email

class EmailService:
    def __init__(self):
        self.sg = None
        if SENDGRID_API_KEY:
            try:
                self.sg = SendGridAPIClient(api_key=SENDGRID_API_KEY)
            except Exception as e:
                logging.error(f"Failed to initialize SendGrid: {e}")
        else:
            logging.warning("SENDGRID_API_KEY not found. Email functionality disabled.")
    
    def send_email(self, to_email, subject, html_content, text_content=None):
        """Send an email using SendGrid"""
        if not self.sg:
            logging.warning("SendGrid not initialized. Cannot send email.")
            return False
        
        try:
            message = Mail(
                from_email=Email(FROM_EMAIL),
                to_emails=To(to_email),
                subject=subject,
                html_content=html_content,
                plain_text_content=text_content
            )
            
            response = self.sg.send(message)
            logging.info(f"Email sent successfully to {to_email}. Status: {response.status_code}")
            return True
            
        except Exception as e:
            logging.error(f"Failed to send email to {to_email}: {e}")
            return False
    
    def send_contact_notification(self, contact_message):
        """Send notification email to admin when new contact message is received"""
        subject = f"New Contact Message from {contact_message.name}"
        
        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <div style="background-color: #00091a; color: white; padding: 20px; text-align: center;">
                <h1>New Contact Message - Target Capital</h1>
            </div>
            
            <div style="padding: 20px; background-color: #f8f9fa;">
                <h2 style="color: #00091a;">Contact Information</h2>
                <table style="width: 100%; border-collapse: collapse;">
                    <tr>
                        <td style="padding: 8px; border-bottom: 1px solid #ddd; font-weight: bold;">Name:</td>
                        <td style="padding: 8px; border-bottom: 1px solid #ddd;">{contact_message.name}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px; border-bottom: 1px solid #ddd; font-weight: bold;">Email:</td>
                        <td style="padding: 8px; border-bottom: 1px solid #ddd;">{contact_message.email}</td>
                    </tr>
                    {f'<tr><td style="padding: 8px; border-bottom: 1px solid #ddd; font-weight: bold;">Phone:</td><td style="padding: 8px; border-bottom: 1px solid #ddd;">{contact_message.phone}</td></tr>' if contact_message.phone else ''}
                    {f'<tr><td style="padding: 8px; border-bottom: 1px solid #ddd; font-weight: bold;">Company:</td><td style="padding: 8px; border-bottom: 1px solid #ddd;">{contact_message.company}</td></tr>' if contact_message.company else ''}
                    <tr>
                        <td style="padding: 8px; border-bottom: 1px solid #ddd; font-weight: bold;">Inquiry Type:</td>
                        <td style="padding: 8px; border-bottom: 1px solid #ddd;">{contact_message.inquiry_type or 'General'}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px; border-bottom: 1px solid #ddd; font-weight: bold;">Subject:</td>
                        <td style="padding: 8px; border-bottom: 1px solid #ddd;">{contact_message.subject or 'No subject'}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px; border-bottom: 1px solid #ddd; font-weight: bold;">Submitted:</td>
                        <td style="padding: 8px; border-bottom: 1px solid #ddd;">{contact_message.created_at.strftime('%B %d, %Y at %I:%M %p')}</td>
                    </tr>
                </table>
                
                <h3 style="color: #00091a; margin-top: 20px;">Message:</h3>
                <div style="background-color: white; padding: 15px; border-left: 4px solid #00091a; margin-top: 10px;">
                    {contact_message.message.replace(chr(10), '<br>')}
                </div>
                
                <div style="margin-top: 20px; text-align: center;">
                    <p style="color: #666;">Please respond to this inquiry promptly.</p>
                </div>
            </div>
        </div>
        """
        
        text_content = f"""
        New Contact Message - Target Capital
        
        Contact Information:
        Name: {contact_message.name}
        Email: {contact_message.email}
        Phone: {contact_message.phone or 'Not provided'}
        Company: {contact_message.company or 'Not provided'}
        Inquiry Type: {contact_message.inquiry_type or 'General'}
        Subject: {contact_message.subject or 'No subject'}
        Submitted: {contact_message.created_at.strftime('%B %d, %Y at %I:%M %p')}
        
        Message:
        {contact_message.message}
        
        Please respond to this inquiry promptly.
        """
        
        return self.send_email(ADMIN_EMAIL, subject, html_content, text_content)
    
    def send_contact_confirmation(self, contact_message):
        """Send confirmation email to user who submitted contact form"""
        subject = "Thank you for contacting Target Capital"
        
        html_content = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
            <div style="background-color: #00091a; color: white; padding: 20px; text-align: center;">
                <h1>Thank You for Contacting Us!</h1>
            </div>
            
            <div style="padding: 20px; background-color: #f8f9fa;">
                <p>Dear {contact_message.name},</p>
                
                <p>Thank you for reaching out to Target Capital. We have received your message and our team will review it shortly.</p>
                
                <div style="background-color: white; padding: 15px; border-left: 4px solid #00091a; margin: 20px 0;">
                    <h3 style="margin-top: 0; color: #00091a;">Your Message Summary:</h3>
                    <p><strong>Subject:</strong> {contact_message.subject or 'General Inquiry'}</p>
                    <p><strong>Inquiry Type:</strong> {contact_message.inquiry_type or 'General'}</p>
                    <p><strong>Submitted:</strong> {contact_message.created_at.strftime('%B %d, %Y at %I:%M %p')}</p>
                </div>
                
                <p><strong>What happens next?</strong></p>
                <ul>
                    <li>Our team will review your inquiry within 24 hours</li>
                    <li>You'll receive a personalized response from our experts</li>
                    <li>For urgent matters, you can call us directly</li>
                </ul>
                
                <div style="text-align: center; margin: 30px 0; padding: 20px; background-color: white; border-radius: 5px;">
                    <h3 style="color: #00091a;">Need Immediate Assistance?</h3>
                    <p>Visit our <a href="https://www.targetcapital.ai/help" style="color: #007bff;">Help Center</a> for instant answers</p>
                    <p>Or explore our <a href="https://www.targetcapital.ai/pricing" style="color: #007bff;">Trading Plans</a></p>
                </div>
                
                <p>Best regards,<br>
                <strong>The Target Capital Team</strong></p>
                
                <div style="margin-top: 30px; padding: 15px; background-color: #e9ecef; border-radius: 5px; text-align: center;">
                    <p style="margin: 0; color: #666; font-size: 14px;">
                        This is an automated message. Please do not reply to this email.
                    </p>
                </div>
            </div>
        </div>
        """
        
        text_content = f"""
        Thank You for Contacting Target Capital!
        
        Dear {contact_message.name},
        
        Thank you for reaching out to Target Capital. We have received your message and our team will review it shortly.
        
        Your Message Summary:
        Subject: {contact_message.subject or 'General Inquiry'}
        Inquiry Type: {contact_message.inquiry_type or 'General'}
        Submitted: {contact_message.created_at.strftime('%B %d, %Y at %I:%M %p')}
        
        What happens next?
        - Our team will review your inquiry within 24 hours
        - You'll receive a personalized response from our experts
        - For urgent matters, you can call us directly
        
        Need Immediate Assistance?
        Visit our Help Center: https://www.targetcapital.ai/help
        Or explore our Trading Plans: https://www.targetcapital.ai/pricing
        
        Best regards,
        The Target Capital Team
        
        This is an automated message. Please do not reply to this email.
        """
        
        return self.send_email(contact_message.email, subject, html_content, text_content)

# Global email service instance
email_service = EmailService()