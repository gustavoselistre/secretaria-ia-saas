from django.contrib import admin

from webhook.models import WhatsAppConfig


@admin.register(WhatsAppConfig)
class WhatsAppConfigAdmin(admin.ModelAdmin):
    list_display = ("twilio_phone_number", "organization", "agent", "is_active", "created_at")
    list_filter = ("is_active", "organization")
    search_fields = ("twilio_phone_number", "organization__name")
    readonly_fields = ("id", "created_at")
