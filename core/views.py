import razorpay
import hmac
import hashlib
import json
import qrcode
import os
import subprocess
from io import BytesIO
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
from core.utils.email_utils import send_order_notification_to_admin


from .models import RTORecord, Order
from .forms import RTORecordForm, SchoolRecordForm, OrderForm

# Initialize Razorpay client
client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))

@csrf_exempt
@require_POST
def ajax_create_record(request):
    try:
        data = json.loads(request.body)
        
        # Get service type and amount from frontend
        service_type = data.get('service_type', 'qr')
        amount = data.get('amount', 2)
        
        # Validate amount based on service type
        service_prices = {
            'qr': 2,
            'pvc': 100, 
            'nfc': 400
        }
        
        expected_amount = service_prices.get(service_type, 2)
        if amount != expected_amount:
            return JsonResponse({
                'error': f'Invalid amount for service {service_type}. Expected ₹{expected_amount}'
            }, status=400)

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

        # Create Razorpay order with DYNAMIC amount
        amount_paise = amount * 100  # Convert rupees to paise dynamically
        
        razorpay_order = client.order.create({
            'amount': amount_paise,
            'currency': 'INR',
            'payment_capture': 1,
        })

        # Map service type to order type
        order_type_mapping = {
            'qr': 'qr_download',
            'pvc': 'pvc_card', 
            'nfc': 'nfc_card'
        }
        
        order_type = order_type_mapping.get(service_type, 'qr_download')

        order = Order.objects.create(
            user=request.user,
            rto_record=record,
            order_id=razorpay_order['id'],
            order_type=order_type,
            amount=amount,  # Store amount in rupees
            payment_status=Order.Status.PENDING,
            payment_provider='razorpay',
        )

        payment_url = reverse('core:payment', kwargs={'record_id': record.id, 'order_type': order_type})
        return JsonResponse({'payment_url': payment_url})
        
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)

def landing_view(request):
    return render(request, 'landing.html')

def home_view(request):
    """Home page view - redirects to dashboard if authenticated, otherwise to landing"""
    if request.user.is_authenticated:
        return redirect('core:dashboard')
    return redirect('core:landing')

@login_required
def dashboard_view(request):
    sort = request.GET.get('sort', 'recent')
    
    user_records_qs = RTORecord.objects.filter(owner=request.user)
    user_orders_qs = Order.objects.filter(user=request.user).order_by('-created_at')

    # Apply sorting based on query parameter
    if sort == "oldest":
        user_records = user_records_qs.order_by('created_at')
    elif sort == "name":
        user_records = user_records_qs.order_by('name')
    elif sort == "date":
        user_records = user_records_qs.order_by('created_at')
    else:  # recent
        user_records = user_records_qs.order_by('-created_at')

    # Limit records to 10 for dashboard
    user_records = user_records[:10]
    user_orders = user_orders_qs[:10]

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
        'sort': sort,  # pass current sort to template for UI state
    })

@login_required
def create_record_view(request, record_type):
    # Use the same form for all record types
    form_class = RTORecordForm
    
    if request.method == 'POST':
        form = form_class(request.POST, request.FILES)
        if form.is_valid():
            record = form.save(commit=False)
            record.owner = request.user
            record.record_type = record_type  # 'rc', 'school', or 'other'
            record.save()
            messages.success(request, f"{record.get_record_type_display()} created successfully. Proceed to payment.")
            return redirect('core:payment', record_id=record.id, order_type='qr_download')
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = form_class()

    # Pass record_type to the template for header/label rendering
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
    
    # Updated pricing with dynamic amounts
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
    """Generate static HTML file for the record in deploy_site folder"""
    cloudinary_urls = get_cloudinary_urls(record)
    
    print(f"🔍 DEBUG: Creating HTML for record {record.id}")
    print(f"📋 Found {len(cloudinary_urls)} documents")
    
    context = {
        'record': record,
        'cloudinary_urls': cloudinary_urls,
    }
    
    # Generate HTML content using your existing template
    try:
        html_content = render_to_string('document_gallery.html', context)
    except Exception as e:
        print(f"❌ Template error: {e}")
        html_content = generate_inline_html(record, cloudinary_urls)
    
    # Create folder structure for Netlify (using deploy_site now)
    folder_path = f'deploy_site/record_{record.id}'
    os.makedirs(folder_path, exist_ok=True)
    
    # Write HTML file
    with open(os.path.join(folder_path, 'index.html'), 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print(f"✅ Generated HTML file: {folder_path}/index.html")

def generate_inline_html(record, cloudinary_urls):
    docs_html = ""
    for i, url in enumerate(cloudinary_urls):
        filename = url.split("/")[-1].split("?")[0] or f"Document_{i+1}"
        download_url = f"{url}?fl_attachment"  # For Cloudinary; but see below for the <a download> trick
        docs_html += f"""
        <div class="doc-card">
            <img src="{url}" alt="Document {i+1}" class="doc-image" loading="lazy">
            <div class="doc-info">
                <h3>Document {i+1}</h3>
                <div class="btn-group">
                    <a href="{url}" class="btn btn-view" target="_blank">
                        <span class="icon-eye"></span> View
                    </a>
                    <a href="{download_url}" download="{filename}" class="btn btn-download">
                        <span class="icon-download"></span> Download
                    </a>
                </div>
            </div>
        </div>
        """
    
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Documents for {record.name}</title>
    <style>
        :root {{
            --accent: #667eea;
            --accent-hover: #5a67d8;
            --success: #48bb78;
            --success-hover: #38a169;
            --bg-card: rgba(255,255,255,0.14);
            --bg: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            --txt-main: #252f44;
            --txt-light: #fff;
            --shadow-card: 0 8px 32px rgba(102,126,234,.13);
        }}
        * {{ box-sizing: border-box; }}
        body {{
            margin: 0; padding: 0;
            font-family: 'Inter', 'Segoe UI', Arial, sans-serif;
            background: var(--bg); min-height: 100vh;
        }}
        .container {{
            max-width: 1200px; margin: 0 auto; padding: 14px;
        }}
        .header {{
            background: var(--bg-card);
            border-radius: 22px;
            box-shadow: var(--shadow-card);
            color: var(--txt-main);
            margin-bottom: 24px;
            text-align: center;
            padding: 32px 20px 24px;
        }}
        .header h1 {{
            font-size: 2.5em; font-weight: 900; margin: 0 0 12px;
            color: var(--accent);
            letter-spacing: -1px;
            display: flex; gap: .45em; align-items: center; justify-content: center;
        }}
        .header .icon-doc {{
            font-size: 1.2em; color: var(--accent);
            margin-bottom: 3px;
        }}
        .header-info {{
            display: flex; gap: 2em; justify-content: center; flex-wrap: wrap; font-size:1.11em; margin-top: 12px; color: #222;
        }}
        .header-info .icon {{
            margin-right: .25em; color: var(--accent);
        }}
        .gallery {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
            gap: 32px;
            margin-bottom: 40px;
        }}
        .doc-card {{
            background: var(--bg-card);
            border-radius: 19px;
            box-shadow: var(--shadow-card);
            overflow: hidden;
            display: flex; flex-direction: column; align-items: center;
            transition: box-shadow .23s cubic-bezier(.4,.6,.18,1);
        }}
        .doc-card:hover {{
            box-shadow: 0 12px 36px rgba(102,126,234,.22);
        }}
        .doc-image {{
            max-width: 100%; width: 100%; height: 220px;
            object-fit: cover; background: #eee;
        }}
        .doc-info {{
            padding: 18px 16px 18px; text-align: center; width:100%;
        }}
        .doc-info h3 {{
            font-size: 1.16em; margin: 6px 0 20px;
            color: var(--accent-hover); font-weight: 600;
        }}
        .btn-group {{
            display: flex; gap: 12px; justify-content: center; margin-top: 4px;
        }}
        .btn {{
            display: inline-block; min-width: 94px; padding: 10px 19px;
            border-radius: 11px; font-weight: 700;
            text-decoration: none; font-size: 1em;
            transition: background .22s, box-shadow .22s;
            border: none; cursor: pointer;
            box-shadow: 0 2px 14px rgba(102,126,234,0.04);
            display: flex; align-items: center; gap: .6em; justify-content: center;
        }}
        .btn-view {{
            background: var(--accent); color: var(--txt-light);
        }}
        .btn-view:hover {{ background: var(--accent-hover); }}
        .btn-download {{
            background: var(--success); color: var(--txt-light);
        }}
        .btn-download:hover {{ background: var(--success-hover); }}
        /* Small icon helpers */
        .icon-eye::before {{ content: "👁️"; position: relative; top: 1px; }}
        .icon-download::before {{ content: "⬇️"; position: relative; top: 2px; }}
        .icon-doc::before {{ content:"📄"; }}
        .icon-phone::before {{ content:"📞"; }}
        .icon-type::before {{ content:"🏷️"; }}
        .icon-date::before {{ content:"📅"; color: #c0392b; }}
        .footer {{
            text-align: center; color: #eee; font-size: 1.05em; margin-bottom: 8px;
        }}
        /* Responsive for small screens */
        @media (max-width: 600px){{
            .header h1 {{ font-size: 1.45em; }}
            .gallery {{ grid-template-columns: 1fr; gap: 18px }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1><span class="icon-doc"></span> Documents for {record.name}</h1>
            <div class="header-info">
                <span><span class="icon-phone icon"></span> <b>Contact:</b> {record.contact_no}</span>
                <span><span class="icon-date icon"></span> <b>Created:</b> {record.created_at.strftime('%B %d, %Y')}</span>
            </div>
        </div>
        <div class="gallery">
            {docs_html}
        </div>
        <div class="footer">
            <p>Generated by <strong>Secure RTO Document Management</strong> | All documents encrypted &amp; secure</p>
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
        
        # Add new files (FIXED: Changed from static_site/ to deploy_site/)
        subprocess.run(['git', 'add', 'deploy_site/'], check=True)
        
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
        
        print(f"✅ Successfully deployed record {record.id} to GitHub")
        
    except subprocess.CalledProcessError as e:
        print(f"❌ Error deploying to GitHub: {e}")
    except Exception as e:
        print(f"❌ Unexpected error during GitHub deployment: {e}")

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
    netlify_url = f"https://teal-rugelach-4d0f54.netlify.app/record_{record.id}/"
    generate_qr_code_for_record(record, netlify_url)
    
    record.gallery_html_url = netlify_url
    record.save()
    
    # Detect the exact order_type from Order object or fallback detection
    if hasattr(order, 'service_type') and order.service_type:
        order_type = order.service_type
    else:
        referrer = request.META.get('HTTP_REFERER', '')
        if '/nfc_card/' in referrer:
            order_type = 'nfc'
        elif '/pvc_card/' in referrer:
            order_type = 'pvc'
        elif hasattr(order, 'amount'):
            if order.amount == 40000:
                order_type = 'nfc'
            elif order.amount == 10000:
                order_type = 'pvc'
            else:
                order_type = 'qr'
        else:
            order_type = 'qr'

    # Send admin notification for pvc/nfc with valid address
    try:
        if order_type in ['pvc', 'nfc'] and record.address and record.address.strip():
            send_order_notification_to_admin(record, order_type, netlify_url)
            print(f"✅ Admin notification sent for {order_type} order")
    except Exception as e:
        print(f"❌ Failed to send admin notification: {e}")

    # Store order_type for success page display
    request.session['order_type'] = order_type
    request.session['order_success'] = True
    
    redirect_url = reverse('core:qr_success', kwargs={'record_id': record.id})
    return JsonResponse({'success': True, 'redirect_url': redirect_url})

@login_required
def qr_success_view(request, record_id):
    record = get_object_or_404(RTORecord, id=record_id)
    
    order_type = request.session.get('order_type', 'qr')
    order_success = request.session.get('order_success', False)
    
    if 'order_type' in request.session:
        del request.session['order_type']
    if 'order_success' in request.session:
        del request.session['order_success']
    
    context = {
        'record': record,
        'order_type': order_type,
        'order_success': order_success,
    }
    
    return render(request, 'core/qr_success.html', context)

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
        
        # Generate QR code (FIXED: Using consistent domain)
        netlify_url = f"https://teal-rugelach-4d0f54.netlify.app/record_{record.id}/"
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
        # Generate QR code if it doesn't exist (FIXED: Using consistent domain)
        netlify_url = f"https://teal-rugelach-4d0f54.netlify.app/record_{record.id}/"
        generate_qr_code_for_record(record, netlify_url)
    return render(request, 'core/download_qr.html', {'record': record})

@login_required
def select_service_view(request):
    record_type = request.GET.get('type', 'rto')
    context = {
        'record_type': record_type
    }
    return render(request, 'core/select_service.html', context)


@login_required
def orders_view(request):
    user_orders = Order.objects.filter(
        user=request.user, order_type__in=['pvc_card', 'nfc_card']
    ).order_by('-created_at')
    return render(request, 'core/orders.html', {'user_orders': user_orders})


@login_required
def edit_profile_view(request):
    user = request.user
    profile = user.core_profile  # Correct access to extended profile

    if request.method == 'POST':
        # Grab POSTed data
        first_name = request.POST.get('first_name', '').strip()
        phone = request.POST.get('phone', '').strip()
        address = request.POST.get('address', '').strip()
        profile_picture = request.FILES.get('profile_picture')

        # Update model fields
        user.first_name = first_name
        profile.phone = phone
        profile.address = address

        if profile_picture:
            profile.profile_picture = profile_picture

        try:
            user.save()
            profile.save()
            messages.success(request, 'Profile updated successfully.')
            return redirect('core:profile')
        except Exception as e:
            messages.error(request, f'Error updating profile: {e}')
            return render(request, 'core/edit_profile.html', {'user': user, 'profile': profile})

    return render(request, 'core/edit_profile.html', {'user': user, 'profile': profile})


@login_required
def order_detail_view(request, order_id):
    order = get_object_or_404(Order, order_id=order_id, user=request.user)
    return render(request, 'core/order_detail.html', {'order': order})


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
    return render(request, 'core/profile.html', {
        'user': request.user,
        'profile': request.user.core_profile
    })



@login_required
def search_records_view(request):
    return redirect('core:dashboard')

@login_required
def export_records_view(request):
    return redirect('core:dashboard')
