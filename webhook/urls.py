from django.urls import path

from webhook.views import twilio_whatsapp_webhook

urlpatterns = [
    path("whatsapp/", twilio_whatsapp_webhook, name="twilio-whatsapp-webhook"),
]
