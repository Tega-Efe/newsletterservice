from rest_framework.response import Response
from .models import Emails, Subscriber, BroadcastLog
from .serializers import EmailSerializer, SubscriberSerializer, BroadcastLogSerializer
from django.core.mail import send_mail, EmailMultiAlternatives, get_connection
from django.conf import settings
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from datetime import datetime
from pathlib import Path
import uuid
import logging
import json
import base64

logger = logging.getLogger(__name__)


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
    # Flyer image (img.png)
    flyer_data_uri = ''
    flyer_path = images_dir / 'img5.png'
    if flyer_path.exists():
        with open(flyer_path, 'rb') as f:
            flyer_base64 = base64.b64encode(f.read()).decode('utf-8')
            flyer_data_uri = f'data:image/png;base64,{flyer_base64}'
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
    
    # Convert images to base64 data URIs for embedding in template
    images_dir = Path(settings.BASE_DIR) / 'core' / 'static' / 'images'
    
    icon2_data_uri = ''
    icon2_path = images_dir / 'icon2.png'
    if icon2_path.exists():
        with open(icon2_path, 'rb') as f:
            icon2_base64 = base64.b64encode(f.read()).decode('utf-8')
            icon2_data_uri = f'data:image/png;base64,{icon2_base64}'
    
    qr_data_uri = ''
    qr_path = images_dir / 'QRcode.jpg'
    if qr_path.exists():
        with open(qr_path, 'rb') as f:
            qr_base64 = base64.b64encode(f.read()).decode('utf-8')
            qr_data_uri = f'data:image/jpeg;base64,{qr_base64}'
    
    img5_data_uri = ''
    img5_path = images_dir / 'img5.png'
    if img5_path.exists():
        with open(img5_path, 'rb') as f:
            img5_base64 = base64.b64encode(f.read()).decode('utf-8')
            img5_data_uri = f'data:image/png;base64,{img5_base64}'
    
    # Social media icons
    instagram_icon_data_uri = ''
    instagram_icon_path = images_dir / 'instagram.png'
    if instagram_icon_path.exists():
        with open(instagram_icon_path, 'rb') as f:
            instagram_icon_base64 = base64.b64encode(f.read()).decode('utf-8')
            instagram_icon_data_uri = f'data:image/png;base64,{instagram_icon_base64}'

    tiktok_icon_data_uri = ''
    tiktok_icon_path = images_dir / 'tiktok.png'
    if tiktok_icon_path.exists():
        with open(tiktok_icon_path, 'rb') as f:
            tiktok_icon_base64 = base64.b64encode(f.read()).decode('utf-8')
            tiktok_icon_data_uri = f'data:image/png;base64,{tiktok_icon_base64}'

    x_icon_data_uri = ''
    x_icon_path = images_dir / 'twitter.png'
    if x_icon_path.exists():
        with open(x_icon_path, 'rb') as f:
            x_icon_base64 = base64.b64encode(f.read()).decode('utf-8')
            x_icon_data_uri = f'data:image/png;base64,{x_icon_base64}'

    whatsapp_icon_data_uri = ''
    whatsapp_icon_path = images_dir / 'whatsapp.png'
    if whatsapp_icon_path.exists():
        with open(whatsapp_icon_path, 'rb') as f:
            whatsapp_icon_base64 = base64.b64encode(f.read()).decode('utf-8')
            whatsapp_icon_data_uri = f'data:image/png;base64,{whatsapp_icon_base64}'

    # Common context for both templates
    context = {
        'newsletter_title': data.get('newsletter_title', email.subject),
        'newsletter_content': data.get('newsletter_content', data.get('message', '')),
        'highlight_text': data.get('highlight_text', ''),
        'cta_text': data.get('cta_text', ''),
        'cta_url': data.get('cta_url', ''),
        'icon2_image': icon2_data_uri,
        'qr_code_image': qr_data_uri,
        'img5_image': img5_data_uri,
        'year': datetime.now().year,
        'unsubscribe_url': data.get('unsubscribe_url', '#'),
        'instagram_icon': instagram_icon_data_uri,
        'tiktok_icon': tiktok_icon_data_uri,
        'x_icon': x_icon_data_uri,
        'whatsapp_icon': whatsapp_icon_data_uri,
        'flyer_image': flyer_data_uri,
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

    # Send the email
    email_message = EmailMultiAlternatives(
        subject=email.subject,
        body=text_content,
        from_email=settings.EMAIL_HOST_USER,
        to=[email.email]
    )
    email_message.attach_alternative(html_content, "text/html")
    email_message.send()

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
    # Always use EMAIL_HOST_USER from settings, ignore Angular's senderEmail
    sender_email = settings.EMAIL_HOST_USER
    sender_name = data.get('senderName', settings.DEFAULT_FROM_EMAIL)
    broadcast_id = data.get('broadcastId', str(uuid.uuid4()))
    
    # Parse the message JSON to extract newsletter data
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

    # Auto-save/update subscribers from recipients list
    new_subscribers = 0
    updated_subscribers = 0
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

    # Send emails using a single connection
    sent_count = 0
    failed_count = 0
    failed_emails = []
    
    connection = get_connection(
        backend=settings.EMAIL_BACKEND,
        host=settings.EMAIL_HOST,
        port=settings.EMAIL_PORT,
        username=settings.EMAIL_HOST_USER,
        password=settings.EMAIL_HOST_PASSWORD,
        use_tls=settings.EMAIL_USE_TLS
    )
    
    try:
        connection.open()
    except Exception as conn_error:
        broadcast_log.status = 'failed'
        broadcast_log.save()
        return Response({
            'status': 'error',
            'message': f'Failed to connect to email server: {str(conn_error)}'
        }, status=500)

    # Load and encode images ONCE (not per recipient) - PERFORMANCE OPTIMIZATION
    images_dir = Path(settings.BASE_DIR) / 'core' / 'static' / 'images'

    icon2_data_uri = ''
    icon2_path = images_dir / 'icon2.png'
    if icon2_path.exists():
        with open(icon2_path, 'rb') as f:
            icon2_base64 = base64.b64encode(f.read()).decode('utf-8')
            icon2_data_uri = f'data:image/png;base64,{icon2_base64}'

    qr_data_uri = ''
    qr_path = images_dir / 'QRcode.jpg'
    if qr_path.exists():
        with open(qr_path, 'rb') as f:
            qr_base64 = base64.b64encode(f.read()).decode('utf-8')
            qr_data_uri = f'data:image/jpeg;base64,{qr_base64}'

    img5_data_uri = ''
    img5_path = images_dir / 'img5.png'
    if img5_path.exists():
        with open(img5_path, 'rb') as f:
            img5_base64 = base64.b64encode(f.read()).decode('utf-8')
            img5_data_uri = f'data:image/png;base64,{img5_base64}'

    # Social media icons
    instagram_icon_data_uri = ''
    instagram_icon_path = images_dir / 'instagram.png'
    if instagram_icon_path.exists():
        with open(instagram_icon_path, 'rb') as f:
            instagram_icon_base64 = base64.b64encode(f.read()).decode('utf-8')
            instagram_icon_data_uri = f'data:image/png;base64,{instagram_icon_base64}'

    tiktok_icon_data_uri = ''
    tiktok_icon_path = images_dir / 'tiktok.png'
    if tiktok_icon_path.exists():
        with open(tiktok_icon_path, 'rb') as f:
            tiktok_icon_base64 = base64.b64encode(f.read()).decode('utf-8')
            tiktok_icon_data_uri = f'data:image/png;base64,{tiktok_icon_base64}'

    x_icon_data_uri = ''
    x_icon_path = images_dir / 'twitter.png'
    if x_icon_path.exists():
        with open(x_icon_path, 'rb') as f:
            x_icon_base64 = base64.b64encode(f.read()).decode('utf-8')
            x_icon_data_uri = f'data:image/png;base64,{x_icon_base64}'

    whatsapp_icon_data_uri = ''
    whatsapp_icon_path = images_dir / 'whatsapp.png'
    if whatsapp_icon_path.exists():
        with open(whatsapp_icon_path, 'rb') as f:
            whatsapp_icon_base64 = base64.b64encode(f.read()).decode('utf-8')
            whatsapp_icon_data_uri = f'data:image/png;base64,{whatsapp_icon_base64}'

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
                'icon2_image': icon2_data_uri,
                'qr_code_image': qr_data_uri,
                'img5_image': img5_data_uri,
                'flyer_image': img5_data_uri,
                'instagram_icon': instagram_icon_data_uri,
                'tiktok_icon': tiktok_icon_data_uri,
                'x_icon': x_icon_data_uri,
                'whatsapp_icon': whatsapp_icon_data_uri,
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

            # Send email
            try:
                email_message = EmailMultiAlternatives(
                    subject=subject,
                    body=text_content,
                    from_email=from_email_formatted,
                    to=[recipient_email],
                    connection=connection
)
                email_message.attach_alternative(html_content, "text/html")
                
                send_result = email_message.send()
                
                if send_result == 0:
                    raise Exception(f"Email send returned 0 for {recipient_email}")
                    
            except Exception as send_error:
                raise
            
            sent_count += 1
            
        except Exception as e:
            failed_count += 1
            failed_emails.append({'email': recipient_email, 'error': str(e)})

    # Close SMTP connection
    try:
        connection.close()
    except Exception:
        pass

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
