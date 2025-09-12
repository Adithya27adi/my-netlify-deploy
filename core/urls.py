from django.urls import path
from . import views

app_name = 'core'

urlpatterns = [
    # Main pages
    path('', views.home_view, name='home'),
    path('landing/', views.landing_view, name='landing'),
    path('dashboard/', views.dashboard_view, name='dashboard'),

    # Record management with record type support
    path('records/create/<str:record_type>/', views.create_record_view, name='create_record'),
    path('records/<uuid:record_id>/', views.record_detail_view, name='record_detail'),
    path('records/<uuid:record_id>/edit/', views.edit_record_view, name='edit_record'),

    # QR Code functionality
    path('records/<uuid:record_id>/generate-qr/', views.generate_qr_view, name='generate_qr'),
    path('records/<uuid:record_id>/download-qr/', views.download_qr_view, name='download_qr'),
    path('records/<uuid:record_id>/qr-preview/', views.qr_preview_view, name='qr_preview'),

    # Payment processing for different order types
    path('records/<uuid:record_id>/payment/<str:order_type>/', views.payment_view, name='payment'),
    path('payment/create-order/', views.create_payment_order, name='create_payment_order'),
    path('payment/verify/', views.verify_payment, name='verify_payment'),

    # Order management
    path('orders/', views.orders_view, name='orders'),
    path('orders/<str:order_id>/', views.order_detail_view, name='order_detail'),
    path('orders/<str:order_id>/success/', views.order_success_view, name='order_success'),
    path('orders/<str:order_id>/cancel/', views.order_cancel_view, name='order_cancel'),

    # Document verification (for QR scanning)
    path('verify-record/<uuid:record_id>/', views.verify_record_view, name='verify_record'),

    # Profile management
    path('profile/', views.profile_view, name='profile'),
    path('profile/edit/', views.edit_profile_view, name='edit_profile'),

    # Additional utility views
    path('search/', views.search_records_view, name='search_records'),
    path('export-records/', views.export_records_view, name='export_records'),
    path('ajax/create-record/', views.ajax_create_record, name='ajax_create_record'),

    path('ajax/verify-payment/', views.verify_payment, name='verify_payment'),
    path('qr-success/<uuid:record_id>/', views.qr_success_view, name='qr_success'),
    # In your urls.py
    path('select-service/', views.select_service_view, name='select_service'),
    path('payment/', views.payment_view, name='payment'),

    
]
