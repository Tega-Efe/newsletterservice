from django.contrib import admin
from django.urls import path
from . import views

urlpatterns = [
    path('', views.getRoutes, name="routes" ),
    path('emails/', views.getEmails, name="emails"),

    # Broadcast endpoint for sending to multiple recipients
    path('broadcast/send/', views.broadcastEmail, name="broadcast-send"),
]