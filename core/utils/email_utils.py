import os
import tempfile
from io import BytesIO
from django.core.mail import EmailMessage
from django.template.loader import render_to_string
from django.conf import settings
from PIL import Image
import qrcode


def send_order_notification_to_admin(record, order_type, qr_code_url):
    """Send email notification to admin for PVC/NFC orders"""
    
    subject = f"New {order_type.upper()} Order - {record.name}"
    
    # Email content
    context = {
        'record': record,
        'order_type': order_type.upper(),  # Uppercase for display consistency
        'qr_code_url': qr_code_url,
        'service_name': 'PVC Card' if order_type == 'pvc' else 'NFC Card'
    }
    
    # Generate email body using existing templates
    html_message = render_to_string('emails/admin_order_notification.html', context)
    plain_message = render_to_string('emails/admin_order_notification.txt', context)
    
    # Create email
    email = EmailMessage(
        subject=subject,
        body=html_message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[settings.ADMIN_EMAIL],
    )
    email.content_subtype = 'html'
    
    # Generate and attach QR code image
    qr_image_path = generate_qr_code_image(qr_code_url, record.name)
    if qr_image_path:
        email.attach_file(qr_image_path)
    
    # Send email
    try:
        email.send()
        print(f"✅ Order notification sent to admin for record {record.id}")
        
        # Clean up temp QR code file
        if qr_image_path and os.path.exists(qr_image_path):
            os.remove(qr_image_path)
            
        return True
    except Exception as e:
        print(f"❌ Failed to send email: {e}")
        return False


def generate_qr_code_image(url, name):
    """Generate QR code image file for email attachment"""
    try:
        # Generate QR code
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(url)
        qr.make(fit=True)
        
        # Create QR image
        img = qr.make_image(fill_color="black", back_color="white")
        
        # Save to temporary file
        temp_file = tempfile.NamedTemporaryFile(
            delete=False, 
            suffix=f'_qr_{name}.png',
            prefix='qr_code_'
        )
        img.save(temp_file.name, 'PNG')
        temp_file.close()
        
        return temp_file.name
    except Exception as e:
        print(f"❌ Failed to generate QR code image: {e}")
        return None
