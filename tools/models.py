import uuid

from django.db import models


class Client(models.Model):
    """Lead/cliente capturado durante conversas."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="clients",
    )
    name = models.CharField(max_length=255)
    phone = models.CharField(max_length=30)
    email = models.CharField(max_length=255, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("organization", "phone")]
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.phone})"


class ServiceCatalog(models.Model):
    """Serviço/produto oferecido por uma organização."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="services",
    )
    category = models.CharField(max_length=100)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True, default="")
    price = models.DecimalField(max_digits=10, decimal_places=2)
    duration_minutes = models.PositiveIntegerField(default=60)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["category", "name"]
        verbose_name = "Serviço"
        verbose_name_plural = "Serviços"

    def __str__(self):
        return f"{self.name} — R$ {self.price}"


class Appointment(models.Model):
    """Agendamento de um serviço."""

    class Status(models.TextChoices):
        SCHEDULED = "scheduled", "Agendado"
        CANCELLED = "cancelled", "Cancelado"
        COMPLETED = "completed", "Concluído"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="appointments",
    )
    client = models.ForeignKey(
        Client, on_delete=models.CASCADE, related_name="appointments"
    )
    service = models.ForeignKey(
        ServiceCatalog,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="appointments",
    )
    date = models.DateField()
    time = models.TimeField()
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.SCHEDULED
    )
    notes = models.TextField(blank=True, default="")
    external_event_id = models.CharField(
        max_length=255, blank=True, default="",
        help_text="ID do evento no calendário externo (Google, Cal.com, Calendly)",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "time"]

    def __str__(self):
        return f"{self.client.name} — {self.date} {self.time}"


class Quote(models.Model):
    """Orçamento gerado pelo agente."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Rascunho"
        SENT = "sent", "Enviado"
        ACCEPTED = "accepted", "Aceito"
        REJECTED = "rejected", "Rejeitado"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="quotes",
    )
    client = models.ForeignKey(
        Client,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quotes",
    )
    items = models.JSONField(help_text='[{"name": "...", "qty": 1, "unit_price": 100.0}]')
    total = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.DRAFT
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Orçamento #{str(self.id)[:8]} — R$ {self.total}"


class CalendarConfig(models.Model):
    """Configuração de calendário externo por organização."""

    class Provider(models.TextChoices):
        GOOGLE = "google", "Google Calendar"
        CALCOM = "calcom", "Cal.com"
        CALENDLY = "calendly", "Calendly"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.OneToOneField(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="calendar_config",
    )
    provider = models.CharField(max_length=20, choices=Provider.choices)
    calendar_id = models.CharField(
        max_length=255,
        help_text="ID do calendário (Google), event type ID (Cal.com/Calendly)",
    )
    credentials = models.JSONField(
        default=dict,
        help_text="API key, tokens ou service account JSON conforme o provider",
    )
    business_hours = models.JSONField(
        default=dict,
        blank=True,
        help_text='Override de horários. Ex: {"mon": ["09:00", "18:00"], "sat": ["09:00", "16:00"], "sun": null}',
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Configuração de Calendário"
        verbose_name_plural = "Configurações de Calendário"

    def __str__(self):
        return f"{self.organization.name} — {self.get_provider_display()}"
