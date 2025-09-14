import os
import uuid
from django.db import models
from django.contrib.auth import get_user_model
from django.core.validators import FileExtensionValidator
from django.utils import timezone
from django.urls import reverse
import qrcode
from io import BytesIO
from django.core.files import File
from PIL import Image
import json
from django.db.models.signals import post_save
from django.dispatch import receiver


User = get_user_model()


def upload_to_user_folder(instance, filename):
    """Generate upload path for user files."""
    ext = filename.split('.')[-1]
    filename = f"{uuid.uuid4()}.{ext}"
    return f"user_uploads/{instance.owner.id}/{filename}"

class RTORecord(models.Model):
    """Main RTO record model storing all document information."""
    
    # NEW FIELDS for Cloudinary + Netlify integration
    gallery_html_url = models.URLField(blank=True, help_text="Netlify hosted gallery URL")
    cloudinary_urls = models.JSONField(default=list, help_text="Cloudinary document URLs")
    
    class RecordType(models.TextChoices):
        RC = 'rc', 'RC Record'
        SCHOOL = 'school', 'School Record'
        OTHER = 'other', 'Other Record'
    
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        UNDER_REVIEW = 'under_review', 'Under Review'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'
    
    # Basic Information
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='rto_records')
    name = models.CharField(max_length=200)
    contact_no = models.CharField(max_length=20)
    address = models.TextField()
    record_type = models.CharField(max_length=10, choices=RecordType.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    
    # File Uploads (these can store both local files and Cloudinary URLs)
    rc_photo = models.ImageField(
        upload_to=upload_to_user_folder,
        validators=[FileExtensionValidator(allowed_extensions=['jpg', 'jpeg', 'png'])],
        blank=True, null=True,
        help_text="RC registration certificate photo"
    )
    insurance_doc = models.FileField(
        upload_to=upload_to_user_folder,
        validators=[FileExtensionValidator(allowed_extensions=['pdf', 'jpg', 'jpeg', 'png'])],
        blank=True, null=True,
        help_text="Insurance document"
    )
    pu_check_doc = models.FileField(
        upload_to=upload_to_user_folder,
        validators=[FileExtensionValidator(allowed_extensions=['pdf', 'jpg', 'jpeg', 'png'])],
        blank=True, null=True,
        help_text="PU check document"
    )
    driving_license_doc = models.FileField(  # FIXED: was "FileFiel"
        upload_to=upload_to_user_folder,
        validators=[FileExtensionValidator(allowed_extensions=['pdf', 'jpg', 'jpeg', 'png'])],
        blank=True, null=True,
        help_text="Driving license document"
    )
    
    # Generated Files
    qr_code_image = models.ImageField(
        upload_to='qr_codes/', blank=True, null=True,
        help_text="Generated QR code image"
    )
    pdf_card_filepath = models.CharField(
        max_length=500, blank=True,
        help_text="Path to generated PDF card"
    )
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    reviewed_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='reviewed_records'
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True, help_text="Internal notes for review")

    class Meta:
        db_table = 'rto_record'
        verbose_name = 'RTO Record'
        verbose_name_plural = 'RTO Records'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.name} - {self.get_record_type_display()} ({self.get_status_display()})"
    
    def generate_qr_code(self):
        """Generate QR code for the record with all document links."""
        # Create QR data with record information
        qr_data = {
            'record_id': str(self.id),
            'name': self.name,
            'contact_no': self.contact_no,
            'record_type': self.record_type,
            'documents': {
                'rc_photo': self.rc_photo.url if self.rc_photo else None,
                'insurance_doc': self.insurance_doc.url if self.insurance_doc else None,
                'pu_check_doc': self.pu_check_doc.url if self.pu_check_doc else None,
                'driving_license_doc': self.driving_license_doc.url if self.driving_license_doc else None,
            },
            'verification_url': f'/verify-record/{self.id}/',
            'created_at': self.created_at.isoformat() if self.created_at else None
        }
        
        # Generate QR code
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(json.dumps(qr_data))
        qr.make(fit=True)
        
        # Create QR image
        img = qr.make_image(fill_color="black", back_color="white")
        
        # Save to model
        blob = BytesIO()
        img.save(blob, 'PNG')
        blob.seek(0)
        
        self.qr_code_image.save(
            f'qr_{self.id}.png',
            File(blob),
            save=False
        )
        self.save()
        
        return self.qr_code_image.url

    def get_document_count(self):
        """Get count of uploaded documents."""
        count = 0
        if self.rc_photo: count += 1
        if self.insurance_doc: count += 1
        if self.pu_check_doc: count += 1
        if self.driving_license_doc: count += 1
        return count

    def has_documents(self):
        """Check if record has any documents uploaded."""
        return self.get_document_count() > 0
    
    def save(self, *args, **kwargs):
        if self.pk:
            try:
                old_record = RTORecord.objects.get(pk=self.pk)
            except RTORecord.DoesNotExist:
                old_record = None
            if old_record and old_record.status != self.status and self.status in ['approved', 'rejected']:
                self.reviewed_at = timezone.now()
        super().save(*args, **kwargs)

# core/models.py
class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='core_profile')
    phone = models.CharField(max_length=18, blank=True)
    address = models.TextField(blank=True)
    profile_picture = models.ImageField(upload_to='profiles/', blank=True, null=True)

    def __str__(self):
        return f"Profile for {self.user.email}"

@receiver(post_save, sender=User)
def create_or_update_user_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.create(user=instance)
    else:
        instance.core_profile.save()
        
class Order(models.Model):
    """Order model for handling payments and delivery."""
    
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        PROCESSING = 'processing', 'Processing'
        COMPLETED = 'completed', 'Completed'
        FAILED = 'failed', 'Failed'
        REFUNDED = 'refunded', 'Refunded'
    
    class OrderType(models.TextChoices):
        QR_DOWNLOAD = 'qr_download', 'QR Download'
        PVC_CARD = 'pvc_card', 'PVC Card'
        NFC_CARD = 'nfc_card', 'NFC Card'
    
    # Order Information
    order_id = models.CharField(max_length=50, unique=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='orders')
    rto_record = models.ForeignKey(RTORecord, on_delete=models.CASCADE, related_name='orders')
    order_type = models.CharField(max_length=20, choices=OrderType.choices)
    
    # Pricing
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    shipping_cost = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    
    # Delivery
    delivery_address = models.TextField(blank=True)
    delivery_phone = models.CharField(max_length=20, blank=True)
    delivery_pincode = models.CharField(max_length=10, blank=True)
    
    # Payment
    payment_status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    payment_provider = models.CharField(max_length=20, choices=[('razorpay', 'Razorpay'), ('stripe', 'Stripe')])
    payment_provider_payment_id = models.CharField(max_length=100, blank=True)
    payment_response = models.JSONField(default=dict, blank=True)
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        db_table = 'order'
        verbose_name = 'Order'
        verbose_name_plural = 'Orders'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Order {self.order_id} - {self.user.email} - ₹{self.total_amount}"
    
    def save(self, *args, **kwargs):
        if not self.order_id:
            self.order_id = self.generate_order_id()
        
        # Calculate total amount
        self.total_amount = self.amount + self.shipping_cost
        
        # Set completed_at when status changes to completed
        if self.payment_status == self.Status.COMPLETED and not self.completed_at:
            self.completed_at = timezone.now()
        
        super().save(*args, **kwargs)
    
    def generate_order_id(self):
        """Generate unique order ID."""
        return f"RTO{timezone.now().strftime('%Y%m%d')}{uuid.uuid4().hex[:8].upper()}"

class PrintOrder(models.Model):
    """Print order model for tracking physical card production and delivery."""
    
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        IN_PRODUCTION = 'in_production', 'In Production'
        PRINTED = 'printed', 'Printed'
        SHIPPED = 'shipped', 'Shipped'
        DELIVERED = 'delivered', 'Delivered'
        CANCELLED = 'cancelled', 'Cancelled'
    
    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name='print_order')
    rto_record = models.ForeignKey(RTORecord, on_delete=models.CASCADE)
    
    # Production Details
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    production_notes = models.TextField(blank=True)
    
    # Shipping Details
    tracking_number = models.CharField(max_length=100, blank=True)
    shipping_partner = models.CharField(max_length=50, blank=True)
    estimated_delivery_date = models.DateField(null=True, blank=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    printed_at = models.DateTimeField(null=True, blank=True)
    shipped_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        db_table = 'print_order'
        verbose_name = 'Print Order'
        verbose_name_plural = 'Print Orders'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Print Order {self.order.order_id} - {self.get_status_display()}"
