import uuid

from django.db import models


class WhatsAppConfig(models.Model):
    """Mapeia um número WhatsApp (Twilio ou uazapi) para uma Organization e seu AIAgent.

    O campo ``phone_number`` armazena o número do bot em formato provider-agnóstico:
      - Twilio: ``whatsapp:+5551999990000``
      - uazapi: ``5551999990000`` (apenas dígitos, sem prefixo)

    Quando o provider ativo é uazapi, os campos ``uazapi_instance_id`` e
    ``uazapi_instance_token`` devem estar preenchidos — o roteamento de mensagens
    recebidas é feito pelo ``uazapi_instance_id`` em vez do número.
    """

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
    phone_number = models.CharField(
        max_length=30,
        unique=True,
        help_text=(
            "Número WhatsApp do bot. Twilio: 'whatsapp:+5551999990000'. "
            "uazapi: '5551999990000'."
        ),
    )
    uazapi_instance_id = models.CharField(
        max_length=64,
        blank=True,
        null=True,
        unique=True,
        help_text="ID da instância uazapi (vazio quando o provider é Twilio).",
    )
    uazapi_instance_token = models.CharField(
        max_length=255,
        blank=True,
        null=True,
        help_text="Token de autenticação da instância uazapi.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "WhatsApp Config"
        verbose_name_plural = "WhatsApp Configs"

    def __str__(self):
        return f"{self.phone_number} → {self.organization.name}"
