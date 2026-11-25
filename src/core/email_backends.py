"""
Django email backend that sends through the Resend API.

Selected in settings.py when EMAIL_PROVIDER=resend:

    EMAIL_BACKEND = 'core.email_backends.ResendEmailBackend'

Because it is a normal Django backend, existing code such as
EmailMultiAlternatives(...).send() and get_connection() works unchanged.

Sending many messages at once
-----------------------------
send_messages() / send_messages_detailed() group messages into calls to
Resend's batch endpoint (POST /emails/batch, up to 100 emails per request),
so 793 recipients take 8 API calls instead of 793.

    connection = get_connection()
    results = connection.send_messages_detailed(messages)   # one SendResult each

Batch limitations handled here:
  * Attachments are not supported by the batch endpoint, so any message with
    attachments is sent individually through the normal endpoint.
  * By default one invalid email rejects the WHOLE batch ("strict"). We ask
    for "permissive" validation so only the bad emails are reported as
    failures. On an older SDK without that option, a rejected batch is
    re-sent one email at a time to isolate the bad address(es).

Resend's default limit is 2 requests/second. Every API call (single or batch)
goes through _throttle() (settings.RESEND_MIN_SEND_INTERVAL) and is retried
on HTTP 429.

Optional setting: RESEND_BATCH_SIZE (default and maximum 100).
"""

import base64
import inspect
import logging
import threading
import time
from collections import namedtuple
from email.mime.base import MIMEBase

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger(__name__)

try:
    import resend
    RESEND_AVAILABLE = True
except ImportError:  # pragma: no cover
    resend = None
    RESEND_AVAILABLE = False

MAX_ATTEMPTS = 4
MAX_BATCH_SIZE = 100  # Resend's hard limit per /emails/batch request

# Outcome for one message. `error` is an Exception when the send failed; a
# message with no recipients is ok=False with error=None (Django convention).
SendResult = namedtuple('SendResult', 'message ok id error')


def _field(obj, name):
    """Read `name` from a dict or an attribute-style response object."""
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _is_rate_limited(exc: Exception) -> bool:
    """True if `exc` looks like Resend's HTTP 429 rate-limit error.

    Checked by name/code/message rather than by importing the SDK's exception
    class, so it works across resend 2.x versions.
    """
    if exc.__class__.__name__ == 'RateLimitError':
        return True
    if getattr(exc, 'code', None) == 429:
        return True
    message = str(exc).lower()
    return 'rate limit' in message or 'too many requests' in message


def _is_validation_error(exc: Exception) -> bool:
    """True if Resend rejected the request payload (HTTP 422 / validation_error)."""
    if exc.__class__.__name__ == 'ValidationError':
        return True
    if getattr(exc, 'code', None) == 422:
        return True
    return 'validation' in str(getattr(exc, 'error_type', '')).lower()


def _batch_accepts_options() -> bool:
    """True if this SDK version's Batch.send takes an `options` argument
    (needed for batch_validation='permissive')."""
    try:
        return 'options' in inspect.signature(resend.Batch.send).parameters
    except (TypeError, ValueError, AttributeError):
        return False


class ResendEmailBackend(BaseEmailBackend):
    # Shared across instances so the spacing holds even when a new backend is
    # created per email (as EmailMessage.send() does without a connection).
    _lock = threading.Lock()
    _last_send = 0.0

    def __init__(self, fail_silently=False, **kwargs):
        # **kwargs swallows SMTP-style options (host, port, ...) that generic
        # code may pass to get_connection().
        super().__init__(fail_silently=fail_silently, **kwargs)
        self.api_key = getattr(settings, 'RESEND_API_KEY', '')

    # -- connection lifecycle ------------------------------------------------
    def open(self):
        """Validate configuration. Resend has no persistent connection."""
        error = None
        if not RESEND_AVAILABLE:
            error = "The 'resend' package is not installed (pip install resend)."
        elif not self.api_key:
            error = "RESEND_API_KEY is not set."

        if error:
            if self.fail_silently:
                logger.error(f"[Resend] {error}")
                return False
            raise ImproperlyConfigured(error)

        resend.api_key = self.api_key
        return True

    def close(self):
        pass

    # -- sending -------------------------------------------------------------
    def send_messages(self, email_messages):
        """Standard Django API: send messages, return the number sent.

        Uses batching under the hood. Every message is attempted; unless
        fail_silently is set, the first error is raised afterwards.
        """
        results = self.send_messages_detailed(email_messages)
        if not self.fail_silently:
            for result in results:
                if result.error is not None:
                    raise result.error
        return sum(1 for result in results if result.ok)

    def send_messages_detailed(self, email_messages):
        """Send messages and return one SendResult per message, in order.

        Never raises for a per-message failure (the error is in the result),
        so callers can report exactly which recipients failed. Still raises
        ImproperlyConfigured for missing configuration unless fail_silently.
        """
        email_messages = list(email_messages or [])
        if not email_messages:
            return []
        if not self.open():
            error = ImproperlyConfigured("Resend is not configured")
            return [SendResult(m, False, None, error) for m in email_messages]

        results = [None] * len(email_messages)
        batchable = []  # [(index, message)]
        for index, message in enumerate(email_messages):
            if not message.recipients():
                results[index] = SendResult(message, False, None, None)
            elif message.attachments:
                # The batch endpoint doesn't support attachments.
                results[index] = self._send_single(message)
            else:
                batchable.append((index, message))

        if len(batchable) == 1:
            index, message = batchable[0]
            results[index] = self._send_single(message)
        else:
            size = self._batch_size()
            for start in range(0, len(batchable), size):
                chunk = batchable[start:start + size]
                chunk_results = self._send_chunk([message for _, message in chunk])
                for (index, _), result in zip(chunk, chunk_results):
                    results[index] = result

        return results

    def _batch_size(self) -> int:
        try:
            size = int(getattr(settings, 'RESEND_BATCH_SIZE', MAX_BATCH_SIZE))
        except (TypeError, ValueError):
            size = MAX_BATCH_SIZE
        return max(1, min(size, MAX_BATCH_SIZE))

    def _send_single(self, message) -> SendResult:
        """One email through the regular /emails endpoint."""
        try:
            response = self._send_with_retry(self._build_payload(message))
        except Exception as exc:
            logger.error("[Resend] Failed to send email to %s: %s", message.to, exc)
            return SendResult(message, False, None, exc)

        email_id = _field(response, 'id')
        logger.info("[Resend] Sent to %s (id=%s)", message.to, email_id or 'N/A')
        return SendResult(message, True, email_id, None)

    def _send_chunk(self, messages):
        """Up to 100 emails in one /emails/batch call. Returns SendResults."""
        try:
            payloads = [self._build_payload(m) for m in messages]
            response = self._batch_send_with_retry(payloads)
        except Exception as exc:
            if _is_validation_error(exc) and len(messages) > 1:
                # Strict validation: one bad email rejected the whole batch.
                # Send them one by one so only the bad one(s) fail.
                logger.warning(
                    "[Resend] Batch of %d rejected (%s); resending individually "
                    "to isolate the invalid email(s)", len(messages), exc,
                )
                return [self._send_single(m) for m in messages]
            logger.error("[Resend] Batch of %d failed: %s", len(messages), exc)
            return [SendResult(m, False, None, exc) for m in messages]

        # Permissive mode: `data` holds ids for the accepted emails (in order)
        # and `errors` lists rejected ones by their index in our payload list.
        rejected = {}
        for err in _field(response, 'errors') or []:
            rejected[_field(err, 'index')] = _field(err, 'message') or 'Rejected by Resend'
        ids = iter(_field(item, 'id') for item in (_field(response, 'data') or []))

        results = []
        for position, message in enumerate(messages):
            if position in rejected:
                logger.error("[Resend] Rejected %s: %s", message.to, rejected[position])
                results.append(SendResult(message, False, None, RuntimeError(rejected[position])))
            else:
                results.append(SendResult(message, True, next(ids, None), None))

        logger.info("[Resend] Batch of %d: %d accepted, %d rejected",
                    len(messages), len(messages) - len(rejected), len(rejected))
        return results

    def _build_payload(self, message) -> dict:
        payload = {
            "from": message.from_email or settings.DEFAULT_FROM_EMAIL,
            "to": list(message.to),
            "subject": message.subject,
        }
        if message.cc:
            payload["cc"] = list(message.cc)
        if message.bcc:
            payload["bcc"] = list(message.bcc)
        if message.reply_to:
            payload["reply_to"] = list(message.reply_to)

        # Body: HTML alternative (if any) plus the plain-text body.
        if getattr(message, 'content_subtype', 'plain') == 'html':
            payload["html"] = message.body
        elif message.body:
            payload["text"] = message.body
        for alternative in getattr(message, 'alternatives', []):
            content, mimetype = alternative[0], alternative[1]
            if mimetype == 'text/html':
                payload["html"] = content

        # Attachments: MIMEBase objects or (filename, content, mimetype) tuples.
        # (Only reached for single sends; batch sends never carry attachments.)
        attachments = []
        for attachment in message.attachments:
            if isinstance(attachment, MIMEBase):
                filename = attachment.get_filename() or 'attachment'
                content = attachment.get_payload(decode=True) or b''
            else:
                filename, content = attachment[0], attachment[1]
                if isinstance(content, str):
                    content = content.encode('utf-8')
            attachments.append({
                "filename": filename,
                "content": base64.b64encode(content).decode('ascii'),
            })
        if attachments:
            payload["attachments"] = attachments

        if message.extra_headers:
            payload["headers"] = {k: str(v) for k, v in message.extra_headers.items()}

        return payload

    # -- rate limiting -------------------------------------------------------
    def _throttle(self):
        interval = float(getattr(settings, 'RESEND_MIN_SEND_INTERVAL', 0.55))
        with ResendEmailBackend._lock:
            wait = ResendEmailBackend._last_send + interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            ResendEmailBackend._last_send = time.monotonic()

    def _with_retry(self, func, *args):
        """Call a Resend SDK function, throttled, retrying on HTTP 429."""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._throttle()
            try:
                return func(*args)
            except Exception as exc:
                if attempt < MAX_ATTEMPTS and _is_rate_limited(exc):
                    delay = min(2 ** attempt * 0.5, 8)
                    logger.warning(
                        "[Resend] Rate limited (attempt %d/%d), retrying in %.1fs",
                        attempt, MAX_ATTEMPTS, delay,
                    )
                    time.sleep(delay)
                    continue
                raise

    def _send_with_retry(self, payload: dict):
        return self._with_retry(resend.Emails.send, payload)

    def _batch_send_with_retry(self, payloads: list):
        if _batch_accepts_options():
            return self._with_retry(
                resend.Batch.send, payloads, {"batch_validation": "permissive"}
            )
        return self._with_retry(resend.Batch.send, payloads)