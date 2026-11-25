from django.shortcuts import render
from .serializers import BroadcastSerializer
from django.core.mail import send_mail, EmailMultiAlternatives
from rest_framework.response import Response
from rest_framework.decorators import api_view
from django.views.decorators.csrf import csrf_exempt
from .utils import *
from .firebase_auth import firebase_admin_required
import logging

# Create your views here.
logger = logging.getLogger(__name__)

@api_view(['GET'])
def getRoutes(request):
    routes = [
        {
            'Endpoint': '/test-post',
            'method': 'POST',
            'body': {'test': 'data'},
            'description': 'Test endpoint for POST requests'
        },
        {
            'Endpoint': '/emails/create/',
            'method': 'POST',
            'body': {'subject': "", 'message': "", 'email': ""},
            'description': 'Sends a single newsletter email (not persisted to a DB; subscriber data lives in Firestore)'
        },
        {
            'Endpoint': '/broadcast/send',
            'method': 'POST',
            'body': {'subject': "", 'message': "", 'recipients': [], 'senderEmail': "", 'senderName': "", 'broadcastId': ""},
            'description': 'Sends broadcast emails to multiple recipients'
        },
    ]
    return Response(routes)


@api_view(['POST'])
@firebase_admin_required
def getEmails(request):
    device_id = request.headers.get('X-Device-ID')
    logger.info(f"Creating email - Data: {request.data}")
    return createEmail(request, device_id)


@api_view(['POST'])
@csrf_exempt
@firebase_admin_required
def broadcastEmail(request):
    """
    Broadcast endpoint matching Angular's data structure.
    Expects: { subject, message, recipients, senderEmail, senderName, broadcastId }
    """
    device_id = request.headers.get('X-Device-ID')
    logger.info(f"Broadcast requested by admin uid={request.firebase_claims.get('uid')}")

    try:
        # Log only the COUNT, never the addresses. Printing every recipient
        # dumped the whole mailing list into the server log (personal data),
        # and large broadcasts were failing right around that loop.
        recipients = request.data.get('recipients', [])
        count = len(recipients) if isinstance(recipients, list) else 0
        logger.info(f"Broadcast recipients: {count}")

        serializer = BroadcastSerializer(data=request.data)
        if not serializer.is_valid():
            return Response({'error': serializer.errors}, status=400)

        return sendBroadcastEmail(request, device_id)

    except Exception as exc:
        # PythonAnywhere's log viewer truncates the long single-line traceback
        # Django writes, hiding the real error. Log a SHORT line first so the
        # exception type and message always survive, then the full traceback.
        logger.error("Broadcast failed: %s: %s", type(exc).__name__, str(exc)[:300])
        logger.exception("Broadcast traceback")
        return Response({
            'status': 'error',
            'message': f'Broadcast failed: {type(exc).__name__}: {str(exc)[:300]}',
        }, status=500)