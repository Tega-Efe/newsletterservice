from rest_framework.response import Response
from .models import Emails, Subscriber, BroadcastLog
from .serializers import EmailSerializer, SubscriberSerializer, BroadcastLogSerializer
from django.core.mail import send_mail, EmailMultiAlternatives, get_connection
from django.conf import settings
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from datetime import datetime
import uuid
import logging
import json

logger = logging.getLogger(__name__)

# Optional SendGrid integration
try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail
    SENDGRID_AVAILABLE = True
except Exception:
    SENDGRID_AVAILABLE = False


def _get_image_url(key: str) -> str:
    """Return an image URL for `key` from settings.NEWSLETTER_IMAGES or
    fall back to common settings names like KEY_ICON_URL or KEY_URL.
    """
    imgs = getattr(settings, 'NEWSLETTER_IMAGES', {}) or {}
    if key in imgs and imgs[key]:
        return imgs[key]
    # Try common fallback attribute names
    for suffix in ('_ICON_URL', '_URL'):
        attr = f"{key.upper()}{suffix}"
        val = getattr(settings, attr, '')
        if val:
            return val
    return ''


def getEmailList(request, device_id):
    if device_id:
        emails = Emails.objects.filter(device_id=device_id).order_by('-edited_at')
    else:
        emails = Emails.objects.all().order_by('-edited_at')
    serializer = EmailSerializer(emails, many=True)
    return Response(serializer.data)

def getEmailDetail(request, pk, device_id):
    try:
        if device_id:
            email = Emails.objects.get(id=pk, device_id=device_id)
        else:
            email = Emails.objects.get(id=pk)
    except Emails.DoesNotExist:
        return Response(status=404)
    serializer = EmailSerializer(email, many=False)
    return Response(serializer.data)


def createEmail(request, device_id):
    # Flyer images: expect URLs from request
    flyer_images = request.data.get('flyer_images', [])
    """
    Send newsletter emails for a ticketing platform.
    Supports two types:
    1. Newsletter Announcement - General announcements, updates, news
    2. Newsletter Event - Event-specific notifications with event details
    """
    data = request.data

    # Validate required fields
    if 'email' not in data:
        return Response({'error': 'Email field is missing in the request data.'}, status=400)
    
    if 'subject' not in data:
        return Response({'error': 'Subject field is missing in the request data.'}, status=400)

    # Get newsletter type (announcement or event)
    newsletter_type = data.get('newsletter_type', 'announcement')  # Default to announcement
    
    # Create email record
    email = Emails.objects.create(
        subject=data['subject'],
        message=data.get('message', ''),
        email=data['email'],
        device_id=device_id
    )

    # Determine which template to use based on newsletter type
    if newsletter_type == 'event':
        template_name = 'newsletter-event.html'
    else:
        template_name = 'newsletter-announcement.html'
    
    # Load image URLs from settings (prefer NEWSLETTER_IMAGES dict)
    icon2_url = _get_image_url('icon2')
    qr_code_url = _get_image_url('qr_code')
    instagram_icon_url = _get_image_url('instagram')
    tiktok_icon_url = _get_image_url('tiktok')
    x_icon_url = _get_image_url('twitter')
    whatsapp_icon_url = _get_image_url('whatsapp')

    # Common context for both templates
    context = {
        'newsletter_title': data.get('newsletter_title', email.subject),
        'newsletter_content': data.get('newsletter_content', data.get('message', '')),
        'highlight_text': data.get('highlight_text', ''),
        'cta_text': data.get('cta_text', ''),
        'cta_url': data.get('cta_url', ''),
        'icon2_image': icon2_url,
        'qr_code_image': qr_code_url,
        'year': datetime.now().year,
        'unsubscribe_url': data.get('unsubscribe_url', '#'),
        'instagram_icon': instagram_icon_url,
        'tiktok_icon': tiktok_icon_url,
        'x_icon': x_icon_url,
        'whatsapp_icon': whatsapp_icon_url,
        'flyer_images': flyer_images,
    }

    # Add event-specific context if newsletter type is event
    if newsletter_type == 'event':
        context.update({
            'event_title': data.get('event_title', data.get('newsletter_title', 'Special Event')),
            'event_date': data.get('event_date', ''),
            'event_time': data.get('event_time', ''),
            'event_location': data.get('event_location', ''),
        })

    # Render the HTML template with context
    html_content = render_to_string(template_name, context)
    text_content = strip_tags(html_content)

    # Send the email via SendGrid (required)
    if not SENDGRID_AVAILABLE or not getattr(settings, 'SENDGRID_API_KEY', ''):
        logger.error('SendGrid not configured: SENDGRID_API_KEY missing or sendgrid package not installed')
        return Response({'error': 'SendGrid not configured on server'}, status=500)

    try:
        sg = SendGridAPIClient(getattr(settings, 'SENDGRID_API_KEY'))
        msg = Mail(
            from_email=settings.DEFAULT_FROM_EMAIL,
            to_emails=email.email,
            subject=email.subject,
            html_content=text_content
        )
        response = sg.send(msg)
        logger.info(f"SendGrid single-send response: status={getattr(response, 'status_code', None)}")
    except Exception as e:
        logger.exception('SendGrid single-send failed')
        return Response({'error': 'Failed to send email via SendGrid'}, status=500)

    serializer = EmailSerializer(email, many=False)
    return Response(serializer.data)

def updateEmail(request, pk, device_id):
    data = request.data
    try:
        if device_id:
            email = Emails.objects.get(id=pk, device_id=device_id)
        else:
            email = Emails.objects.get(id=pk)
    except Emails.DoesNotExist:
        return Response(status=404)
    email.message = data.get('message', email.message)
    if 'subject' in data:
        email.subject = data['subject']
    email.save()
    serializer = EmailSerializer(email, many=False)
    return Response(serializer.data)

def deleteEmail(request, pk, device_id):
    try:
        if device_id:
            email = Emails.objects.get(id=pk, device_id=device_id)
        else:
            email = Emails.objects.get(id=pk)
    except Emails.DoesNotExist:
        return Response(status=404)
    email.delete()
    return Response('Email was deleted!')


# Broadcast email functions
def sendBroadcastEmail(request, device_id):
    """
    Send broadcast emails to multiple recipients at once.
    Expects: { subject, message, recipients, senderEmail, senderName, broadcastId }
    """
    data = request.data

    # Validate required fields
    if 'subject' not in data:
        return Response({'error': 'Subject field is required'}, status=400)
    
    if 'message' not in data:
        return Response({'error': 'Message field is required'}, status=400)
    
    if 'recipients' not in data or not isinstance(data['recipients'], list) or len(data['recipients']) == 0:
        return Response({'error': 'Recipients list is required and must contain at least one email'}, status=400)

    # Get broadcast details
    subject = data['subject']
    message = data['message']
    recipients = data['recipients']
    # Always use DEFAULT_FROM_EMAIL as the sender address (ignore any senderEmail provided)
    sender_email = settings.DEFAULT_FROM_EMAIL
    sender_name = data.get('senderName', '')
    broadcast_id = data.get('broadcastId', str(uuid.uuid4()))
    
    # Parse the message JSON to extract newsletter data
    # Initialize counters for subscriber creation/reactivation
    new_subscribers = 0
    updated_subscribers = 0

    try:
        newsletter_data = json.loads(message)
        template_type = newsletter_data.get('template', 'announcement')
        newsletter_title = newsletter_data.get('title', subject)
        newsletter_content = newsletter_data.get('content', '')
        highlight_text = newsletter_data.get('highlight_text', '')
        cta_text = newsletter_data.get('cta_text', '')
        cta_url = newsletter_data.get('cta_url', '')
        event_date = newsletter_data.get('event_date', '')
        event_time = newsletter_data.get('event_time', '')
        event_location = newsletter_data.get('event_location', '')
        flyer_images = newsletter_data.get('flyer_images', [])
    except json.JSONDecodeError:
        template_type = data.get('templateType', 'announcement')
        newsletter_title = subject
        newsletter_content = message
        highlight_text = ''
        cta_text = ''
        cta_url = ''
        event_date = ''
        event_time = ''
        event_location = ''
        flyer_images = []

    # Auto-save/update subscribers from recipients list (works for both branches)
    for recipient_email in recipients:
        try:
            subscriber, created = Subscriber.objects.get_or_create(
                email=recipient_email,
                defaults={
                    'device_id': device_id,
                    'is_active': True
                }
            )
            if created:
                new_subscribers += 1
            elif not subscriber.is_active:
                subscriber.is_active = True
                subscriber.device_id = device_id
                subscriber.save()
                updated_subscribers += 1
        except Exception:
            pass

    # Create broadcast log
    broadcast_log = BroadcastLog.objects.create(
        device_id=device_id,
        broadcast_id=broadcast_id,
        subject=subject,
        message=message,
        sender_email=sender_email,
        sender_name=sender_name,
        recipients_count=len(recipients),
        status='pending'
    )

    # Require SendGrid for all sending (no SMTP fallback)
    sent_count = 0
    failed_count = 0
    failed_emails = []

    # Log the default from email used for broadcasts
    logger.info(f"Broadcasts will use DEFAULT_FROM_EMAIL={settings.DEFAULT_FROM_EMAIL}")

    if not SENDGRID_AVAILABLE or not getattr(settings, 'SENDGRID_API_KEY', ''):
        broadcast_log.status = 'failed'
        broadcast_log.save()
        logger.error('SendGrid not available or SENDGRID_API_KEY missing; broadcasts aborted')
        return Response({'status': 'error', 'message': 'SendGrid not configured on server'}, status=500)

    try:
        sg_client = SendGridAPIClient(getattr(settings, 'SENDGRID_API_KEY'))
        logger.info('SendGrid client initialized for broadcasts')
    except Exception as e:
        broadcast_log.status = 'failed'
        broadcast_log.save()
        logger.exception('Failed to initialize SendGrid client')
        return Response({'status': 'error', 'message': 'Failed to initialize SendGrid client'}, status=500)

    # Load image URLs from settings (prefer NEWSLETTER_IMAGES dict)
    icon2_url = _get_image_url('icon2')
    qr_code_url = _get_image_url('qr_code')
    icon_url = _get_image_url('icon')
    instagram_icon_url = _get_image_url('instagram')
    tiktok_icon_url = _get_image_url('tiktok')
    twitter_icon_url = _get_image_url('twitter')
    whatsapp_icon_url = _get_image_url('whatsapp')
    header_bg_url = _get_image_url('header_bg')
    footer_bg_url = _get_image_url('footer_bg')

    for recipient_email in recipients:
        try:
            # Generate unsubscribe URL for Angular frontend
            unsubscribe_url = f'https://restless-society.web.app/unsubscribe?email={recipient_email}'

            # Create context for template with parsed newsletter data
            context = {
                'newsletter_title': newsletter_title,
                'newsletter_content': newsletter_content,
                'highlight_text': highlight_text,
                'cta_text': cta_text,
                'cta_url': cta_url,
                'year': datetime.now().year,
                'unsubscribe_url': unsubscribe_url,
                # Firebase Storage URLs (no encoding!)
                'icon2_image': icon2_url,
                'qr_code_image': qr_code_url,
                'icon_image': icon_url,
                'background_header_image': header_bg_url,
                'background_footer_image': footer_bg_url,
                'flyer_images': flyer_images,  # Dynamic flyer images from admin upload
                'instagram_icon': instagram_icon_url,
                'tiktok_icon': tiktok_icon_url,
                'x_icon': twitter_icon_url,
                'whatsapp_icon': whatsapp_icon_url,
            }
            
            # Add event-specific fields if template type is 'event'
            if template_type == 'event':
                context.update({
                    'event_title': newsletter_title,
                    'event_date': event_date,
                    'event_time': event_time,
                    'event_location': event_location,
                })
            
            # Select template based on type
            template_name = 'newsletter-event.html' if template_type == 'event' else 'newsletter-announcement.html'

            # Render HTML template
            try:
                html_content = render_to_string(template_name, context)
                text_content = strip_tags(html_content)
                
                # Create a fixed plain text message
                debug_text = f"""
{newsletter_title}

{newsletter_content}

{highlight_text if highlight_text else ''}

{cta_text if cta_text else ''}: {cta_url if cta_url else ''}

---
This email was sent by {sender_name}
Year: {datetime.now().year}
                """.strip()
                
                text_content = debug_text
                
            except Exception as render_error:
                raise

            # Prepare from_email
            if sender_name and '@' not in sender_name:
                from_email_formatted = f'{sender_name} <{sender_email}>'
            else:
                from_email_formatted = sender_email

            # Send email via SendGrid or SMTP
           
            # Send via SendGrid
            try:
                msg = Mail(
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    to_emails=recipient_email,
                    subject=subject,
                    html_content=html_content
                )
                response = sg_client.send(msg)
                logger.info(f'SendGrid response: status={getattr(response, "status_code", None)}')
                status_code = getattr(response, 'status_code', 0)
                if status_code < 200 or status_code >= 300:
                    raise Exception(f'SendGrid send failed, status={status_code}')
                sent_count += 1
            except Exception:
                raise
            
        except Exception as e:
            failed_count += 1
            failed_emails.append({'email': recipient_email, 'error': str(e)})

    # No SMTP connection to close when using SendGrid

    # Save broadcast email once
    if sent_count > 0:
        try:
            Emails.objects.create(
                device_id=device_id,
                subject=subject,
                message=message[:500],
                email=sender_email
            )
        except Exception:
            pass

    # Update broadcast log
    broadcast_log.sent_count = sent_count
    broadcast_log.failed_count = failed_count
    
    if failed_count == 0:
        broadcast_log.status = 'sent'
    elif sent_count == 0:
        broadcast_log.status = 'failed'
    else:
        broadcast_log.status = 'partial'
    
    broadcast_log.save()

    # Prepare response
    response_data = {
        'broadcast_id': broadcast_id,
        'subject': subject,
        'recipients_count': len(recipients),
        'sent_count': sent_count,
        'failed_count': failed_count,
        'status': broadcast_log.status,
        'subscribers_added': new_subscribers,
        'subscribers_reactivated': updated_subscribers
    }

    if failed_emails:
        response_data['failed_emails'] = failed_emails

    return Response(response_data, status=200 if sent_count > 0 else 500)


# Subscriber functions
def getSubscriberList(request, device_id):
    """Get all subscribers"""
    if device_id:
        subscribers = Subscriber.objects.filter(device_id=device_id, is_active=True)
    else:
        subscribers = Subscriber.objects.filter(is_active=True)
    
    serializer = SubscriberSerializer(subscribers, many=True)
    return Response(serializer.data)


def createSubscriber(request, device_id):
    """Create a new subscriber or reactivate existing one"""
    data = request.data
    logger.info(f"Creating subscriber: {data.get('email')}")

    if 'email' not in data:
        logger.error("Subscriber creation failed: Email field missing")
        return Response({'error': 'Email field is required'}, status=400)

    # Use get_or_create to ensure only one record per email
    try:
        subscriber, created = Subscriber.objects.get_or_create(
            email=data['email'],
            defaults={
                'device_id': device_id,
                'is_active': True
            }
        )
        
        if created:
            logger.info(f"New subscriber created: {data['email']} (ID: {subscriber.id})")
            serializer = SubscriberSerializer(subscriber)
            return Response(serializer.data, status=201)
        else:
            # Subscriber already exists
            if not subscriber.is_active:
                # Reactivate inactive subscriber
                logger.info(f"Reactivating subscriber: {data['email']}")
                subscriber.is_active = True
                subscriber.device_id = device_id
                subscriber.save()
                serializer = SubscriberSerializer(subscriber)
                logger.info(f"Subscriber reactivated successfully: {data['email']}")
                return Response(serializer.data, status=200)
            else:
                # Already active
                logger.warning(f"Subscriber already exists and is active: {data['email']}")
                serializer = SubscriberSerializer(subscriber)
                return Response(serializer.data, status=200)  # Return existing subscriber
                
    except Exception as e:
        logger.error(f"Error creating/updating subscriber: {str(e)}")
        return Response({'error': 'Failed to process subscriber'}, status=500)


def getSubscriberDetail(request, pk, device_id):
    """Get a specific subscriber"""
    try:
        if device_id:
            subscriber = Subscriber.objects.get(id=pk, device_id=device_id)
        else:
            subscriber = Subscriber.objects.get(id=pk)
    except Subscriber.DoesNotExist:
        return Response({'error': 'Subscriber not found'}, status=404)
    
    serializer = SubscriberSerializer(subscriber)
    return Response(serializer.data)


def updateSubscriber(request, pk, device_id):
    """Update a subscriber"""
    try:
        if device_id:
            subscriber = Subscriber.objects.get(id=pk, device_id=device_id)
        else:
            subscriber = Subscriber.objects.get(id=pk)
    except Subscriber.DoesNotExist:
        return Response({'error': 'Subscriber not found'}, status=404)

    data = request.data
    subscriber.email = data.get('email', subscriber.email)
    subscriber.is_active = data.get('is_active', subscriber.is_active)
    subscriber.save()

    serializer = SubscriberSerializer(subscriber)
    return Response(serializer.data)


def deleteSubscriber(request, pk, device_id):
    """Delete (deactivate) a subscriber by ID or email"""
    logger.info(f"Attempting to delete/deactivate subscriber: {pk}")
    logger.info(f"Device ID filter: {device_id}")
    
    try:
        # Try to find by email first (if pk contains @)
        if '@' in str(pk):
            logger.info(f"Searching for subscriber by email: {pk}")
            # Don't filter by device_id for deletion - allow deleting any subscriber with matching email
            subscriber = Subscriber.objects.get(email=pk)
            logger.info(f"Found subscriber: {subscriber.email} (ID: {subscriber.id}, Device: {subscriber.device_id})")
        else:
            # Otherwise, treat as ID
            logger.info(f"Searching for subscriber by ID: {pk}")
            if device_id:
                subscriber = Subscriber.objects.get(id=pk, device_id=device_id)
            else:
                subscriber = Subscriber.objects.get(id=pk)
    except Subscriber.DoesNotExist:
        logger.error(f"Subscriber not found: {pk}")
        # Log all subscribers for debugging
        all_subscribers = Subscriber.objects.all()
        logger.debug(f"Total subscribers in database: {all_subscribers.count()}")
        for sub in all_subscribers[:5]:  # Show first 5
            logger.debug(f"  - {sub.email} (ID: {sub.id}, Active: {sub.is_active}, Device: {sub.device_id})")
        return Response({'error': 'Subscriber not found'}, status=404)
    
    # Soft delete by setting is_active to False
    subscriber.is_active = False
    subscriber.save()
    logger.info(f"Subscriber deactivated successfully: {subscriber.email} (ID: {subscriber.id})")
    
    return Response({'message': 'Subscriber deactivated successfully'})
