from django.contrib import admin
from .models import Emails, Subscriber, BroadcastLog

# Register your models here.
admin.site.register(Emails)
admin.site.register(Subscriber)
admin.site.register(BroadcastLog)