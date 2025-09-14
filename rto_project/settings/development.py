from .base import *

DEBUG = True

# Development-specific settings
CORS_ALLOW_ALL_ORIGINS = True

# Email backend for development
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'

# Razorpay API credentials (use your actual keys here)
RAZORPAY_KEY_ID = 'rzp_test_R7ybrMqHPiXQ98'
RAZORPAY_SECRET = 'SKTGgh7lXtMPZl8WwDaCHg9s'

