from django.contrib import admin
from django.urls import path
from . import views

urlpatterns = [
    path('', views.getRoutes, name="routes" ),
    path('emails/', views.getEmails, name="emails"),
    path('emails/<str:pk>/', views.getEmail, name="email"),
    
    # Broadcast endpoint for sending to multiple recipients
    path('broadcast/send', views.broadcastEmail, name="broadcast-send"),
    
    # Subscriber endpoints
    path('subscribers/', views.subscribers, name="subscribers"),
    path('subscribers/<str:pk>/', views.subscriberDetail, name="subscriber-detail"),
]
