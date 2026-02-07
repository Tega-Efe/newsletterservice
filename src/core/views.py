from django.shortcuts import render
from .serializers import EmailSerializer, BroadcastSerializer
from .models import Emails
from django.core.mail import send_mail, EmailMultiAlternatives
from rest_framework.response import Response
from rest_framework.decorators import api_view
from django.views.decorators.csrf import csrf_exempt
from .utils import *
import logging

# Create your views here.
logger = logging.getLogger(__name__)

@api_view(['GET'])
def getRoutes(request):
    routes = [
        {
            'Endpoint': '/emails/',
            'method': 'GET',
            'body': None,
            'description': 'Returns an array of emails'
        },
        {
            'Endpoint': '/test-post',
            'method': 'POST',
            'body': {'test': 'data'},
            'description': 'Test endpoint for POST requests'
        },
        {
            'Endpoint': '/emails/id',
            'method': 'GET',
            'body': None,
            'description': 'Returns a single email object'
        },
        {
            'Endpoint': '/emails/create/',
            'method': 'POST',
            'body': {'subject': "", 'message': "", 'email': ""},
            'description': 'Creates a new email with data sent in post request'
        },
        {
            'Endpoint': '/emails/id/update/',
            'method': 'PUT',
            'body': {'subject': "", 'message': "", 'email': ""},
            'description': 'Updates an existing email with data sent in put request'
        },
        {
            'Endpoint': '/emails/id/delete/',
            'method': 'DELETE',
            'body': None,
            'description': 'Deletes an existing email'
        },
        {
            'Endpoint': '/broadcast/send',
            'method': 'POST',
            'body': {'subject': "", 'message': "", 'recipients': [], 'senderEmail': "", 'senderName': "", 'broadcastId': ""},
            'description': 'Sends broadcast emails to multiple recipients and auto-saves them as subscribers'
        },
        {
            'Endpoint': '/subscribers/',
            'method': 'GET',
            'body': None,
            'description': 'Returns list of all active subscribers'
        },
        {
            'Endpoint': '/subscribers/',
            'method': 'POST',
            'body': {'email': "", 'name': ""},
            'description': 'Creates a new subscriber or reactivates an existing one'
        },
        {
            'Endpoint': '/subscribers/id/',
            'method': 'GET',
            'body': None,
            'description': 'Returns a single subscriber'
        },
        {
            'Endpoint': '/subscribers/id/',
            'method': 'PUT',
            'body': {'email': "", 'name': "", 'is_active': True},
            'description': 'Updates an existing subscriber'
        },
        {
            'Endpoint': '/subscribers/id/',
            'method': 'DELETE',
            'body': None,
            'description': 'Deactivates a subscriber (soft delete)'
        }
    ]
    return Response(routes)

@api_view(['GET', 'POST'])
def getEmails(request):
    device_id = request.headers.get('X-Device-ID')
    logger.info(f"getEmails called - Method: {request.method}, Device ID: {device_id}")
    
    if request.method == 'GET':
        return getEmailList(request, device_id)

    if request.method == 'POST':
        logger.info(f"Creating email - Data: {request.data}")
        return createEmail(request, device_id)

@api_view(['GET', 'PUT', 'DELETE'])
def getEmail(request, pk):
    device_id = request.headers.get('X-Device-ID')
    if request.method == 'GET':
        return getEmailDetail(request, pk, device_id)

    if request.method == 'PUT':
        return updateEmail(request, pk, device_id)

    if request.method == 'DELETE':
        return deleteEmail(request, pk, device_id)


@api_view(['POST'])
@csrf_exempt
def broadcastEmail(request):
    """
    Broadcast endpoint matching Angular's data structure.
    Expects: { subject, message, recipients, senderEmail, senderName, broadcastId }
    """
    device_id = request.headers.get('X-Device-ID')
    
    # Print recipients array
    recipients = request.data.get('recipients', [])
    print("\n" + "=" * 80)
    print(f"📧 RECIPIENTS ARRAY ({len(recipients)} emails):")
    print("=" * 80)
    for idx, email in enumerate(recipients, 1):
        print(f"  {idx}. {email}")
    print("=" * 80 + "\n")
    
    serializer = BroadcastSerializer(data=request.data)
    if not serializer.is_valid():
        return Response({'error': serializer.errors}, status=400)
    
    return sendBroadcastEmail(request, device_id)


@api_view(['GET', 'POST'])
def subscribers(request):
    """
    Handle subscriber list retrieval and creation
    """
    device_id = request.headers.get('X-Device-ID')
    logger.info(f"Subscribers endpoint - Method: {request.method}, Device ID: {device_id}")
    
    if request.method == 'GET':
        return getSubscriberList(request, device_id)
    
    if request.method == 'POST':
        logger.info(f"Creating subscriber: {request.data.get('email')}")
        return createSubscriber(request, device_id)


@api_view(['GET', 'PUT', 'DELETE'])
def subscriberDetail(request, pk):
    """
    Handle individual subscriber operations
    """
    device_id = request.headers.get('X-Device-ID')
    logger.info(f"Subscriber detail - Method: {request.method}, ID: {pk}, Device ID: {device_id}")
    
    if request.method == 'GET':
        return getSubscriberDetail(request, pk, device_id)
    
    if request.method == 'PUT':
        logger.info(f"Updating subscriber {pk}: {request.data}")
        return updateSubscriber(request, pk, device_id)
    
    if request.method == 'DELETE':
        logger.info(f"Deleting/deactivating subscriber {pk}")
        return deleteSubscriber(request, pk, device_id)