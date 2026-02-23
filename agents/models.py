import uuid

from django.db import models


class AIAgent(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="agents",
    )
    name = models.CharField(max_length=255)
    system_prompt = models.TextField(
        help_text="Instruções de comportamento enviadas como system message."
    )
    model_name = models.CharField(max_length=100, default="gpt-4o")
    temperature = models.FloatField(default=0.7)

    class Meta:
        ordering = ["name"]
        verbose_name = "AI Agent"
        verbose_name_plural = "AI Agents"

    def __str__(self):
        return f"{self.name} ({self.organization})"
