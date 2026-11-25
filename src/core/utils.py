from rest_framework.response import Response
from django.core.mail import send_mail, EmailMultiAlternatives, get_connection
from django.conf import settings
from django.template.loader import render_to_string
from django.utils.html import strip_tags, escape
from datetime import datetime
from urllib.parse import quote
import uuid
import logging
import json
import re

logger = logging.getLogger(__name__)


# Colour used for links inside the newsletter body (the template's orange).
# Inline styles are required: most email clients ignore <style> rules for links.
LINK_COLOR = '#fc821f'

# Only these URL schemes may become links. Anything else (javascript:, data:,
# vbscript: ...) is dropped so an admin field can never inject a script link.
_ALLOWED_LINK_SCHEMES = ('http', 'https', 'mailto')

# Two link forms are recognised in the text an admin types:
#   1. [link text](https://example.com)   - the "attach link" form
#   2. https://example.com                - a bare pasted URL
_LINK_RE = re.compile(
    r'\[(?P<label>[^\[\]\n]+)\]\((?P<url>[^\s()]+)\)'
    r'|(?P<bare>https?://[^\s<>"\']+)'
)


def _safe_link_url(url: str):
    """Return a safe absolute URL for `url`, or None if it must not be linked."""
    url = (url or '').strip()
    if not url:
        return None
    if re.match(r'^[a-zA-Z][a-zA-Z0-9+.\-]*:', url):  # has a scheme
        scheme = url.split(':', 1)[0].lower()
        return url if scheme in _ALLOWED_LINK_SCHEMES else None
    if url.startswith('//'):
        return 'https:' + url
    return 'https://' + url  # e.g. "youtube.com/x" typed without a scheme


def _split_trailing_punctuation(url: str):
    """A URL at the end of a sentence shouldn't swallow the '.' or ')' after it."""
    trailing = ''
    while url and (url[-1] in '.,;:!?' or (url[-1] == ')' and '(' not in url)):
        trailing = url[-1] + trailing
        url = url[:-1]
    return url, trailing


def _make_link(label: str, url: str) -> str:
    return (
        '<a href="{href}" target="_blank" rel="noopener noreferrer" '
        'style="color:{color};text-decoration:underline;">{label}</a>'
    ).format(href=escape(url), color=LINK_COLOR, label=escape(label))


def _linkify_and_escape(block: str) -> str:
    """HTML-escape `block`, turning recognised links into <a> tags.

    Text and URLs are escaped individually, so nothing an admin types can
    inject markup - only the <a> tags built here reach the email.
    """
    out = []
    pos = 0
    for match in _LINK_RE.finditer(block):
        out.append(escape(block[pos:match.start()]))
        pos = match.end()

        if match.group('label') is not None:            # [text](url)
            safe_url = _safe_link_url(match.group('url'))
            if safe_url:
                out.append(_make_link(match.group('label'), safe_url))
            else:                                       # unsafe scheme: keep the text, drop the link
                out.append(escape(match.group('label')))
        else:                                           # bare URL
            bare, trailing = _split_trailing_punctuation(match.group('bare'))
            safe_url = _safe_link_url(bare)
            out.append(_make_link(bare, safe_url) if safe_url else escape(bare))
            out.append(escape(trailing))
    out.append(escape(block[pos:]))
    return ''.join(out)


def content_to_plain_text(text: str) -> str:
    """Plain-text (non-HTML) version of the content: '[text](url)' becomes
    'text (url)' so the link is still usable in the text/plain email part."""
    def repl(match):
        if match.group('label') is None:
            return match.group(0)
        safe_url = _safe_link_url(match.group('url'))
        return f"{match.group('label')} ({safe_url})" if safe_url else match.group('label')
    return _LINK_RE.sub(repl, text or '')


def format_content_paragraphs(text: str) -> str:
    """Convert plain text typed by an admin (with blank-line-separated
    paragraphs) into safe HTML paragraphs, preserving the structure the
    admin typed instead of collapsing it into one run-on block.

    - Blocks separated by one or more blank lines become separate <p> tags.
    - A single newline inside a block is treated as an intentional line
      break and becomes <br>.
    - Links: "[text](https://url)" and bare "https://url" become clickable
      links in the template's orange (see _linkify_and_escape).
    - All other text is HTML-escaped, so admin input can never inject
      markup into the outgoing email.
    """
    if not text:
        return ''

    # Normalize line endings, then split into paragraph blocks on blank lines
    normalized = text.replace('\r\n', '\n').replace('\r', '\n')
    blocks = re.split(r'\n\s*\n', normalized.strip())

    paragraphs = []
    for block in blocks:
        block = block.strip('\n')
        if not block.strip():
            continue
        escaped = _linkify_and_escape(block)
        # Preserve single line breaks the admin typed within a paragraph
        escaped = escaped.replace('\n', '<br>')
        paragraphs.append(
            '<p style="margin:0 0 16px 0;">{}</p>'.format(escaped)
        )

    return ''.join(paragraphs)


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


def get_sender_email() -> str:
    """Return the bare "from" address for the active email provider
    (see EMAIL_PROVIDER / EMAIL_FROM_ADDRESS in settings.py).

    Don't use settings.EMAIL_HOST_USER for this: it is the literal string
    'apikey' with SendGrid and doesn't exist for Resend.
    """
    return getattr(settings, 'EMAIL_FROM_ADDRESS', '') or settings.DEFAULT_FROM_EMAIL


def createEmail(request, device_id):
    # Flyer images: expect URLs from request
    flyer_images = request.data.get('flyer_images', [])
    """
    Send newsletter emails for a ticketing platform.
    Supports two types:
    1. Newsletter Announcement - General announcements, updates, news
    2. Newsletter Event - Event-specific notifications with event details

    NOTE: this no longer persists a record to the Django DB. Subscriber/email
    history lives in Firestore instead, so this just sends the email and
    echoes back what was sent.
    """
    data = request.data

    # Validate required fields
    if 'email' not in data:
        return Response({'error': 'Email field is missing in the request data.'}, status=400)

    if 'subject' not in data:
        return Response({'error': 'Subject field is missing in the request data.'}, status=400)

    # Get newsletter type (announcement or event)
    newsletter_type = data.get('newsletter_type', 'announcement')  # Default to announcement

    subject = data['subject']
    recipient_email = data['email']
    message = data.get('message', '')

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

    # Raw text as typed by the admin (used for the HTML and plain-text parts)
    raw_content = data.get('newsletter_content', message)

    # Common context for both templates
    context = {
        'newsletter_title': data.get('newsletter_title', subject),
        'newsletter_content': format_content_paragraphs(raw_content),
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
            'event_stub_title': data.get('subject', ''),
            'event_date': data.get('event_date', ''),
            'event_time': data.get('event_time', ''),
            'event_location': data.get('event_location', ''),
        })

    # Render the HTML template with context
    html_content = render_to_string(template_name, context)

    # Plain-text part built from the same fields. (strip_tags on the whole
    # rendered HTML would include the template's CSS and drop link URLs.)
    text_lines = [context['newsletter_title'], '', content_to_plain_text(raw_content)]
    if newsletter_type == 'event':
        details = [context.get('event_date'), context.get('event_time'), context.get('event_location')]
        details = [d for d in details if d]
        if details:
            text_lines += ['', ' | '.join(details)]
    if context['highlight_text']:
        text_lines += ['', context['highlight_text']]
    if context['cta_url']:
        text_lines += ['', f"{context['cta_text'] or 'Link'}: {context['cta_url']}"]

    # Only treat unsubscribe_url as real if the frontend sent an http(s) link
    # (it defaults to '#').
    unsubscribe_url = context['unsubscribe_url']
    has_unsubscribe = bool(re.fullmatch(r'https?://\S+', unsubscribe_url or ''))
    if has_unsubscribe:
        text_lines += ['', '---', f'Not interested? Unsubscribe: {unsubscribe_url}']
    text_content = '\n'.join(text_lines).strip()

    # Send the email
    email_message = EmailMultiAlternatives(
        subject=subject,
        body=text_content,
        from_email=get_sender_email(),
        to=[recipient_email],
        # Lets Gmail/Outlook show their own Unsubscribe button (see broadcast).
        headers={'List-Unsubscribe': f'<{unsubscribe_url}>'} if has_unsubscribe else None,
    )
    email_message.attach_alternative(html_content, "text/html")
    email_message.send()

    # Echo back what was sent instead of serializing a saved DB record.
    return Response({
        'subject': subject,
        'message': message,
        'email': recipient_email,
        'device_id': device_id,
    })


# Broadcast email functions
def sendBroadcastEmail(request, device_id):
    """
    Send broadcast emails to multiple recipients at once.
    Expects: { subject, message, recipients, senderEmail, senderName, broadcastId }

    NOTE: this no longer writes Subscriber / BroadcastLog / Emails rows to the
    Django DB. Subscriber bookkeeping lives in Firestore instead, so this
    function only sends mail and reports counts in the response.
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

    # TEMPORARY OVERRIDE: if OVERRIDE_RECIPIENTS below is non-empty, it takes
    # precedence over whatever recipient list the frontend sent in the payload.
    # Leave it as an empty list to fall back to the payload's recipients as normal.
    # TODO: remove this override block once no longer needed.
    OVERRIDE_RECIPIENTS = []
    if OVERRIDE_RECIPIENTS:
        recipients = OVERRIDE_RECIPIENTS

    # Always use the sender address from settings (works for SendGrid, Gmail
    # and Resend), ignore Angular's senderEmail
    sender_email = get_sender_email()
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

    # Send emails using a single connection
    sent_count = 0
    failed_count = 0
    failed_emails = []

    # get_connection() with no arguments builds the backend selected by
    # EMAIL_PROVIDER in settings.py (SMTP for SendGrid/Gmail, the Resend API
    # backend for Resend), each configured from its own settings.
    logger.info(f"Broadcast {broadcast_id}: sending via {settings.EMAIL_PROVIDER}")
    connection = get_connection()

    try:
        connection.open()
    except Exception as conn_error:
        return Response({
            'status': 'error',
            'message': f'Failed to connect to email server: {str(conn_error)}'
        }, status=500)

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

    # "Name <address>" unless the name is missing or already an address
    if sender_name and '@' not in sender_name:
        from_email_formatted = f'{sender_name} <{sender_email}>'
    else:
        from_email_formatted = sender_email

    # Build one personalised message per recipient (each has its own
    # unsubscribe link). Nothing is sent in this loop.
    prepared = []  # [(recipient_email, EmailMultiAlternatives)]
    for recipient_email in recipients:
        try:
            # Generate unsubscribe URL for Angular frontend
            # URL-encode the address: a raw '+' (e.g. name+tag@gmail.com) would
            # otherwise be read as a space and the unsubscribe would fail.
            unsubscribe_url = f'https://restlesssociety.xyz/unsubscribe?email={quote(recipient_email, safe="")}'

            # Create context for template with parsed newsletter data
            context = {
                'newsletter_title': newsletter_title,
                'newsletter_content': format_content_paragraphs(newsletter_content),
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
                    'event_stub_title': subject,
                    'event_date': event_date,
                    'event_time': event_time,
                    'event_location': event_location,
                })

            # Select template based on type
            template_name = 'newsletter-event.html' if template_type == 'event' else 'newsletter-announcement.html'

            html_content = render_to_string(template_name, context)

            # Fixed plain-text alternative
            text_content = f"""
{newsletter_title}

{content_to_plain_text(newsletter_content)}

{highlight_text if highlight_text else ''}

{cta_text if cta_text else ''}: {cta_url if cta_url else ''}

---
This email was sent by {sender_name}
Not interested? Unsubscribe: {unsubscribe_url}
            """.strip()

            email_message = EmailMultiAlternatives(
                subject=subject,
                body=text_content,
                from_email=from_email_formatted,
                to=[recipient_email],
                connection=connection,
                # Lets Gmail/Outlook show their own "Unsubscribe" button. Without
                # it, people who want out hit "Report spam" instead, which hurts
                # your sender reputation.
                headers={'List-Unsubscribe': f'<{unsubscribe_url}>'},
            )
            email_message.attach_alternative(html_content, "text/html")
            prepared.append((recipient_email, email_message))

        except Exception as e:
            failed_count += 1
            failed_emails.append({'email': recipient_email, 'error': str(e)})

    # Send. The Resend backend groups the messages into batch API calls
    # (100 per call); other backends (SMTP) are sent one by one as before.
    if hasattr(connection, 'send_messages_detailed'):
        results = connection.send_messages_detailed([msg for _, msg in prepared])
        for (recipient_email, _), result in zip(prepared, results):
            if result.ok:
                sent_count += 1
            else:
                failed_count += 1
                failed_emails.append({
                    'email': recipient_email,
                    'error': str(result.error) if result.error else 'Email was not sent',
                })
    else:
        for recipient_email, email_message in prepared:
            try:
                if email_message.send() == 0:
                    raise Exception(f"Email send returned 0 for {recipient_email}")
                sent_count += 1
            except Exception as e:
                failed_count += 1
                failed_emails.append({'email': recipient_email, 'error': str(e)})

    logger.info(f"Broadcast {broadcast_id}: {sent_count} sent, {failed_count} failed")

    # Close the connection (a no-op for the Resend backend)
    try:
        connection.close()
    except Exception:
        pass

    if failed_count == 0:
        status = 'sent'
    elif sent_count == 0:
        status = 'failed'
    else:
        status = 'partial'

    # Prepare response
    response_data = {
        'broadcast_id': broadcast_id,
        'subject': subject,
        'recipients_count': len(recipients),
        'sent_count': sent_count,
        'failed_count': failed_count,
        'status': status,
    }

    if failed_emails:
        response_data['failed_emails'] = failed_emails

    return Response(response_data, status=200 if sent_count > 0 else 500)