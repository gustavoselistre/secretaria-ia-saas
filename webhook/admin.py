from django.contrib import admin

from webhook.models import WhatsAppConfig


@admin.register(WhatsAppConfig)
class WhatsAppConfigAdmin(admin.ModelAdmin):
    list_display = (
        "phone_number",
        "organization",
        "agent",
        "uazapi_instance_id",
        "is_active",
        "created_at",
    )
    list_filter = ("is_active", "organization")
    search_fields = (
        "phone_number",
        "uazapi_instance_id",
        "organization__name",
    )
    readonly_fields = ("id", "created_at")
