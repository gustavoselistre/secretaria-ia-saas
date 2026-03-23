import uuid

from django.db import models


class WhatsAppConfig(models.Model):
    """Mapeia um número Twilio WhatsApp para uma Organization e seu AIAgent."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.OneToOneField(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="whatsapp_config",
    )
    agent = models.ForeignKey(
        "agents.AIAgent",
        on_delete=models.CASCADE,
        related_name="whatsapp_configs",
    )
    twilio_phone_number = models.CharField(
        max_length=30,
        unique=True,
        help_text="Número Twilio WhatsApp (ex: whatsapp:+5551999990000)",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "WhatsApp Config"
        verbose_name_plural = "WhatsApp Configs"

    def __str__(self):
        return f"{self.twilio_phone_number} → {self.organization.name}"
