import razorpay
import hmac
import hashlib
import json
import qrcode
import os
import subprocess
from io import BytesIO
from urllib.parse import urljoin
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.conf import settings
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.core.files import File
from django.template.loader import render_to_string

from .models import RTORecord, Order
from .forms import RTORecordForm, SchoolRecordForm, OrderForm


def landing_view(request):
    return render(request, 'landing.html')


def home_view(request):
    """Home page view - redirects to dashboard if authenticated, otherwise to landing"""
    if request.user.is_authenticated:
        return redirect('core:dashboard')
    return redirect('core:landing')


@login_required
def dashboard_view(request):
    user_records_qs = RTORecord.objects.filter(owner=request.user).order_by("-created_at")
    user_orders_qs = Order.objects.filter(user=request.user).order_by("-created_at")
    
    user_records = user_records_qs[:10]
    user_orders = user_orders_qs[:5]
    
    stats = {
        "total_records": user_records_qs.count(),
        "approved_records": user_records_qs.filter(status='approved').count(),
        "pending_records": user_records_qs.filter(status='pending').count(),
        "total_orders": user_orders_qs.count(),
    }
    
    return render(request, 'core/dashboard.html', {
        'user_records': user_records,
        'user_orders': user_orders,
        'stats': stats,
    })


@csrf_exempt
@login_required
def ajax_create_record(request):
    if request.method != "POST":
        return JsonResponse({"error": "Invalid method"}, status=405)
    
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    name = data.get("name")
    contact_no = data.get("contact_no")
    address = data.get("address")
    record_type = data.get("record_type")
    cloudinary_urls = data.get("uploaded_documents", [])

    if not all([name, contact_no, address, record_type]) or not cloudinary_urls:
        return JsonResponse({"error": "Missing required fields"}, status=400)

    # Create record with Cloudinary URLs
    record = RTORecord.objects.create(
        owner=request.user,
        record_type=record_type,
        name=name,
        contact_no=contact_no,
        address=address,
    )
    
    # Store Cloudinary URLs in the record based on type
    urls = list(cloudinary_urls)
    if record_type == "rto":
        if len(urls) > 0: record.rc_photo = urls[0]
        if len(urls) > 1: record.insurance_doc = urls[1]
        if len(urls) > 2: record.pu_check_doc = urls[2]
        if len(urls) > 3: record.driving_license_doc = urls[3]
    elif record_type == "school":
        if len(urls) > 0: record.marks_card = urls[0]
        if len(urls) > 1: record.photo = urls[1]
        if len(urls) > 2: record.convocation = urls[2]
        if len(urls) > 3: record.migration = urls[3]
    
    record.save()

    # Create Razorpay order for ₹2
    client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
    amount_paise = 200  # ₹2 in paise
    
    razorpay_order = client.order.create({
        'amount': amount_paise,
        'currency': 'INR',
        'payment_capture': 1,
    })

    order = Order.objects.create(
        user=request.user,
        rto_record=record,
        order_id=razorpay_order['id'],
        order_type='qr_download',
        amount=2.00,
        payment_status=Order.Status.PENDING,
        payment_provider='razorpay',
    )

    payment_url = reverse('core:payment', kwargs={'record_id': record.id, 'order_type': 'qr_download'})
    return JsonResponse({'payment_url': payment_url})


@login_required
def create_record_view(request, record_type):
    if record_type == 'school':
        form_class = SchoolRecordForm
    else:
        form_class = RTORecordForm

    if request.method == 'POST':
        form = form_class(request.POST, request.FILES)
        if form.is_valid():
            record = form.save(commit=False)
            record.owner = request.user
            record.record_type = record_type
            record.save()
            messages.success(request, "Record created successfully. Proceed to payment.")
            return redirect('core:payment', record_id=record.id, order_type='qr_download')
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = form_class()

    return render(request, 'core/create_record.html', {'form': form, 'record_type': record_type})


@login_required
def edit_record_view(request, record_id):
    record = get_object_or_404(RTORecord, id=record_id, owner=request.user)
    if record.record_type == 'school':
        form_class = SchoolRecordForm
    else:
        form_class = RTORecordForm

    if request.method == 'POST':
        form = form_class(request.POST, request.FILES, instance=record)
        if form.is_valid():
            form.save()
            messages.success(request, "Record updated successfully.")
            return redirect('core:record_detail', record_id=record.id)
    else:
        form = form_class(instance=record)

    return render(request, 'edit_record.html', {'form': form, 'record': record})


@login_required
def record_detail_view(request, record_id):
    record = get_object_or_404(RTORecord, id=record_id, owner=request.user)
    orders = Order.objects.filter(rto_record=record)
    return render(request, 'record_detail.html', {'record': record, 'orders': orders})


@login_required
def payment_view(request, record_id, order_type):
    record = get_object_or_404(RTORecord, id=record_id, owner=request.user)
    pricing = {
        'qr_download': {'amount': 200, 'title': 'QR Code Download', 'description': 'Digital QR Code', 'currency': 'INR'},
        'pvc_card': {'amount': 10000, 'title': 'PVC Card', 'description': 'Physical PVC Card', 'currency': 'INR'},
        'nfc_card': {'amount': 40000, 'title': 'NFC Card', 'description': 'NFC Card', 'currency': 'INR'},
    }
    if order_type not in pricing:
        messages.error(request, "Invalid payment option selected.")
        return redirect('core:dashboard')

    payment_info = pricing[order_type]
    amount = payment_info['amount']

    client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
    razorpay_order = client.order.create(dict(
        amount=amount,
        currency=payment_info['currency'],
        payment_capture=1,
    ))

    order = Order.objects.create(
        user=request.user,
        rto_record=record,
        order_id=razorpay_order['id'],
        order_type=order_type,
        amount=amount / 100.0,
        payment_status=Order.Status.PENDING,
        payment_provider='razorpay',
    )

    context = {
        'record': record,
        'order': order,
        'razorpay_order': json.dumps(razorpay_order),
        'razorpay_key': settings.RAZORPAY_KEY_ID,
        'payment_info': payment_info,
        'order_type': order_type,
        'amount_in_rupees': amount / 100.0,
    }

    return render(request, 'core/payment.html', context)


def get_cloudinary_urls(record):
    """Extract all Cloudinary URLs from a record"""
    urls = []
    
    print(f"🔍 DEBUG: Checking record {record.id} of type '{record.record_type}'")
    
    if record.record_type == "rto":
        print("📋 Checking RTO documents:")
        if record.rc_photo: 
            urls.append(record.rc_photo)
            print(f"✅ RC Photo: {record.rc_photo}")
        else:
            print("❌ RC Photo: EMPTY")
            
        if record.insurance_doc: 
            urls.append(record.insurance_doc)
            print(f"✅ Insurance Doc: {record.insurance_doc}")
        else:
            print("❌ Insurance Doc: EMPTY")
            
        if record.pu_check_doc: 
            urls.append(record.pu_check_doc)
            print(f"✅ PU Check Doc: {record.pu_check_doc}")
        else:
            print("❌ PU Check Doc: EMPTY")
            
        if record.driving_license_doc: 
            urls.append(record.driving_license_doc)
            print(f"✅ Driving License Doc: {record.driving_license_doc}")
        else:
            print("❌ Driving License Doc: EMPTY")
            
    elif record.record_type == "school":
        print("🎓 Checking School documents:")
        if record.marks_card: 
            urls.append(record.marks_card)
            print(f"✅ Marks Card: {record.marks_card}")
        else:
            print("❌ Marks Card: EMPTY")
            
        if record.photo: 
            urls.append(record.photo)
            print(f"✅ Photo: {record.photo}")
        else:
            print("❌ Photo: EMPTY")
            
        if record.convocation: 
            urls.append(record.convocation)
            print(f"✅ Convocation: {record.convocation}")
        else:
            print("❌ Convocation: EMPTY")
            
        if record.migration: 
            urls.append(record.migration)
            print(f"✅ Migration: {record.migration}")
        else:
            print("❌ Migration: EMPTY")
    
    print(f"📊 TOTAL DOCUMENTS FOUND: {len(urls)}")
    return urls



def generate_static_html(record):
    """Generate static HTML file for the record"""
    # Get Cloudinary URLs
    cloudinary_urls = get_cloudinary_urls(record)
    
    # Create context for template
    context = {
        'record': record,
        'cloudinary_urls': cloudinary_urls,
    }
    
    # Generate HTML content
    try:
        html_content = render_to_string('document_gallery.html', context)
    except:
        html_content = generate_inline_html(record, cloudinary_urls)
    
    # Create folder structure for Netlify (in static_site folder)
    folder_path = f'static_site/record_{record.id}'
    os.makedirs(folder_path, exist_ok=True)
    
    # Write HTML file
    with open(os.path.join(folder_path, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    # Ensure _redirects file exists
    redirects_path = 'static_site/_redirects'
    if not os.path.exists(redirects_path):
        with open(redirects_path, 'w') as f:
            f.write('/* /index.html 200\n')
    
    print(f"✅ Generated HTML for record: {record.id}")

def auto_deploy_to_github(record):
    """Automatically commit and push to GitHub"""
    try:
        # Add new files
        subprocess.run(['git', 'add', 'static_site/'], check=True)
        
        # Commit changes
        commit_message = f"Add document gallery for record {record.id}"
        subprocess.run(['git', 'commit', '-m', commit_message], check=True)
        
        # Push to GitHub
        subprocess.run(['git', 'push', 'origin', 'main'], check=True)
        
        print(f"✅ Successfully deployed record {record.id} to GitHub")
        
    except subprocess.CalledProcessError as e:
        print(f"❌ Error deploying to GitHub: {e}")



def generate_inline_html(record, cloudinary_urls):
    """Generate HTML content inline if template is not available"""
    docs_html = ""
    for i, url in enumerate(cloudinary_urls):
        download_url = url.replace('/upload/', '/upload/fl_attachment/')
        docs_html += f"""
        <div class="doc-card">
            <img src="{url}" alt="Document {i+1}" class="doc-image" loading="lazy">
            <div class="doc-info">
                <h3>Document {i+1}</h3>
                <div class="btn-group">
                    <a href="{url}" class="btn btn-view" target="_blank">👁️ View</a>
                    <a href="{download_url}" class="btn btn-download" target="_blank">⬇️ Download</a>
                </div>
            </div>
        </div>
        """
    
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Documents for {record.name}</title>
        <style>
            body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; padding: 20px; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; }}
            .container {{ max-width: 1200px; margin: 0 auto; }}
            .header {{ text-align: center; margin-bottom: 40px; background: rgba(255,255,255,0.95); padding: 30px; border-radius: 15px; box-shadow: 0 10px 30px rgba(0,0,0,0.2); }}
            .header h1 {{ color: #333; margin-bottom: 10px; font-size: 2.5em; }}
            .header p {{ color: #666; font-size: 1.1em; }}
            .gallery {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 25px; margin-bottom: 40px; }}
            .doc-card {{ background: white; border-radius: 15px; overflow: hidden; box-shadow: 0 8px 25px rgba(0,0,0,0.15); transition: transform 0.3s ease; }}
            .doc-card:hover {{ transform: translateY(-5px); }}
            .doc-image {{ width: 100%; height: 250px; object-fit: cover; border-bottom: 1px solid #eee; }}
            .doc-info {{ padding: 20px; text-align: center; }}
            .doc-info h3 {{ margin: 0 0 15px 0; color: #333; font-size: 1.2em; }}
            .btn-group {{ display: flex; gap: 10px; justify-content: center; }}
            .btn {{ padding: 10px 20px; text-decoration: none; border-radius: 8px; font-weight: 500; transition: all 0.3s ease; }}
            .btn-view {{ background: #667eea; color: white; }}
            .btn-view:hover {{ background: #5a67d8; }}
            .btn-download {{ background: #48bb78; color: white; }}
            .btn-download:hover {{ background: #38a169; }}
            .footer {{ text-align: center; margin-top: 40px; color: rgba(255,255,255,0.8); }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>📄 Documents for {record.name}</h1>
                <p><strong>📞 Contact:</strong> {record.contact_no}</p>
                <p><strong>🏷️ Type:</strong> {record.get_record_type_display()}</p>
                <p><strong>📅 Created:</strong> {record.created_at.strftime('%B %d, %Y')}</p>
            </div>
            
            <div class="gallery">
                {docs_html}
            </div>
            
            <div class="footer">
                <p>Generated by RTO Management System | Secure Document Storage</p>
            </div>
        </div>
    </body>
    </html>
    """
    return html_content


def auto_deploy_to_github(record):
    """Automatically commit and push to GitHub"""
    try:
        # Change to project directory
        os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        
        # Add new files
        subprocess.run(['git', 'add', 'netlify_uploads/'], check=True)
        
        # Check if there are changes to commit
        result = subprocess.run(['git', 'diff', '--staged', '--quiet'], capture_output=True)
        if result.returncode == 0:
            print(f"No changes to commit for record {record.id}")
            return
        
        # Commit changes
        commit_message = f"Add document gallery for record {record.id}"
        subprocess.run(['git', 'commit', '-m', commit_message], check=True)
        
        # Push to GitHub
        subprocess.run(['git', 'push', 'origin', 'main'], check=True)
        
        print(f"Successfully deployed record {record.id} to GitHub")
        
    except subprocess.CalledProcessError as e:
        print(f"Error deploying to GitHub: {e}")
    except Exception as e:
        print(f"Unexpected error during GitHub deployment: {e}")


def generate_qr_code_for_record(record, url):
    """Generate QR code pointing to the gallery URL"""
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
    
    # Save to model
    blob = BytesIO()
    img.save(blob, 'PNG')
    blob.seek(0)
    
    record.qr_code_image.save(f'qr_{record.id}.png', File(blob), save=False)
    record.save()


@csrf_exempt
@login_required
def verify_payment(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Invalid request'}, status=400)

    data = json.loads(request.body)
    razorpay_order_id = data.get('razorpay_order_id')
    razorpay_payment_id = data.get('razorpay_payment_id')
    razorpay_signature = data.get('razorpay_signature')

    try:
        order = Order.objects.get(order_id=razorpay_order_id, user=request.user)
    except Order.DoesNotExist:
        return JsonResponse({'error': 'Order not found'}, status=404)

    # Verify payment signature
    generated_signature = hmac.new(
        key=settings.RAZORPAY_KEY_SECRET.encode(),
        msg=f"{razorpay_order_id}|{razorpay_payment_id}".encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()

    if generated_signature != razorpay_signature:
        order.payment_status = Order.Status.FAILED
        order.save()
        return JsonResponse({'error': 'Signature verification failed'}, status=400)

    # Payment successful
    order.payment_status = Order.Status.COMPLETED
    order.payment_provider_payment_id = razorpay_payment_id
    order.save()
    
    record = order.rto_record
    
    # Generate static HTML file
    generate_static_html(record)
    
    # Auto-commit and push to GitHub
    auto_deploy_to_github(record)
    
    # Generate QR code with Netlify URL
    # In your verify_payment view, change this line:
    # In your verify_payment view, update this line:
    netlify_url = f"https://YOUR-NEW-SITE-NAME.netlify.app/record_{record.id}/"
    generate_qr_code_for_record(record, netlify_url)
    
    record.gallery_html_url = netlify_url
    record.save()
    
    redirect_url = reverse('core:qr_success', kwargs={'record_id': record.id})
    return JsonResponse({'success': True, 'redirect_url': redirect_url})


@login_required
def qr_success_view(request, record_id):
    record = get_object_or_404(RTORecord, id=record_id, owner=request.user)
    return render(request, 'core/qr_success.html', {'record': record})


@login_required
def generate_qr_view(request, record_id):
    record = get_object_or_404(RTORecord, id=record_id, owner=request.user)
    cloudinary_urls = get_cloudinary_urls(record)
    
    if not cloudinary_urls:
        messages.error(request, 'Please upload at least one document before generating QR code.')
        return redirect('core:record_detail', record_id=record.id)
    
    try:
        # Generate static HTML
        generate_static_html(record)
        
        # Generate QR code
        netlify_url = f"https://rto-document-gallery.netlify.app/record_{record.id}/"
        generate_qr_code_for_record(record, netlify_url)
        record.gallery_html_url = netlify_url
        record.save()
        
        # Deploy to GitHub
        auto_deploy_to_github(record)
        
        messages.success(request, 'QR code generated successfully!')
        return redirect('core:record_detail', record_id=record.id)
    except Exception as e:
        messages.error(request, f'Failed to generate QR code: {str(e)}')
        return redirect('core:record_detail', record_id=record.id)


@login_required
def qr_preview_view(request, record_id):
    record = get_object_or_404(RTORecord, id=record_id, owner=request.user)
    return render(request, 'qr_preview.html', {'record': record})


@require_POST
@login_required
def create_payment_order(request):
    return JsonResponse({'success': True, 'message': 'Payment order created'})


@login_required
def download_qr_view(request, record_id):
    record = get_object_or_404(RTORecord, id=record_id, owner=request.user)
    if not record.qr_code_image:
        # Generate QR code if it doesn't exist
        netlify_url = f"https://rto-document-gallery.netlify.app/record_{record.id}/"
        generate_qr_code_for_record(record, netlify_url)
    return render(request, 'core/download_qr.html', {'record': record})


@login_required
def orders_view(request):
    orders = Order.objects.filter(user=request.user).order_by('-created_at')
    return render(request, 'orders.html', {'orders': orders})


@login_required
def order_detail_view(request, order_id):
    order = get_object_or_404(Order, order_id=order_id, user=request.user)
    return render(request, 'order_detail.html', {'order': order})


@login_required
def order_success_view(request, order_id):
    order = get_object_or_404(Order, order_id=order_id, user=request.user)
    return render(request, 'order_success.html', {'order': order})


@login_required
def order_cancel_view(request, order_id):
    order = get_object_or_404(Order, order_id=order_id, user=request.user)
    messages.info(request, "Order cancellation requested.")
    return redirect('core:orders')


@login_required
def verify_record_view(request, record_id):
    record = get_object_or_404(RTORecord, id=record_id)
    return render(request, 'verify_record.html', {'record': record})


@login_required
def profile_view(request):
    return render(request, 'profile.html')


@login_required
def edit_profile_view(request):
    return render(request, 'edit_profile.html')


@login_required
def search_records_view(request):
    return redirect('core:dashboard')


@login_required
def export_records_view(request):
    return redirect('core:dashboard')


# Legacy function kept for compatibility (not used in new flow)
def save_html_file(record, content):
    """Legacy function - kept for compatibility"""
    record_folder = f'netlify_uploads/record_{record.id}'
    os.makedirs(record_folder, exist_ok=True)
    
    filepath = os.path.join(record_folder, 'index.html')
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)
    
    return f'record_{record.id}'


# Legacy function kept for compatibility (not used in new flow) 
def generate_gallery_html(record):
    """Legacy function - kept for compatibility"""
    cloudinary_urls = get_cloudinary_urls(record)
    return generate_inline_html(record, cloudinary_urls)
