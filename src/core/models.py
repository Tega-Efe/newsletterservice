from django.db import models  

class Emails(models.Model):
    device_id = models.CharField(max_length=255,null=True, blank=True) 
    subject = models.CharField(max_length=500)
    message = models.TextField(max_length=500)
    email = models.EmailField()
    created_at = models.DateTimeField(auto_now_add=True,blank=True, null=True)
    edited_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.subject[:50]


class Subscriber(models.Model):
    device_id = models.CharField(max_length=255, null=True, blank=True)
    email = models.EmailField(unique=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.email

    class Meta:
        ordering = ['-created_at']


class BroadcastLog(models.Model):
    device_id = models.CharField(max_length=255, null=True, blank=True)
    broadcast_id = models.CharField(max_length=255, unique=True)
    subject = models.CharField(max_length=500)
    message = models.TextField()
    sender_email = models.EmailField(blank=True, null=True)
    sender_name = models.CharField(max_length=255, blank=True, null=True)
    recipients_count = models.IntegerField(default=0)
    sent_count = models.IntegerField(default=0)
    failed_count = models.IntegerField(default=0)
    status = models.CharField(max_length=50, default='pending')  # pending, sent, failed, partial
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.subject[:50]} - {self.broadcast_id}"

    class Meta:
        ordering = ['-created_at']
    

