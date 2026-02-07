from rest_framework.serializers import ModelSerializer
from rest_framework import serializers
from .models import Emails, Subscriber, BroadcastLog

class EmailSerializer(ModelSerializer):
    class Meta:
        model = Emails
        fields = '__all__'


class SubscriberSerializer(ModelSerializer):
    class Meta:
        model = Subscriber
        fields = '__all__'


class BroadcastSerializer(serializers.Serializer):
    """Serializer matching Angular's broadcast data structure"""
    subject = serializers.CharField(max_length=500)
    message = serializers.CharField()
    recipients = serializers.ListField(
        child=serializers.EmailField(),
        allow_empty=False
    )
    senderEmail = serializers.EmailField(required=False, allow_blank=True)
    senderName = serializers.CharField(max_length=255, required=False, allow_blank=True)
    broadcastId = serializers.CharField(max_length=255, required=False, allow_blank=True)
    templateType = serializers.ChoiceField(
        choices=['announcement', 'event'],
        default='announcement',
        required=False
    )
    # Optional event-specific fields
    eventTitle = serializers.CharField(max_length=500, required=False, allow_blank=True)
    eventDate = serializers.CharField(max_length=100, required=False, allow_blank=True)
    eventTime = serializers.CharField(max_length=100, required=False, allow_blank=True)
    eventLocation = serializers.CharField(max_length=500, required=False, allow_blank=True)


class BroadcastLogSerializer(ModelSerializer):
    class Meta:
        model = BroadcastLog
        fields = '__all__'
