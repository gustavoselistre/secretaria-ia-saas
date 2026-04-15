from __future__ import annotations

from django import forms
from django.contrib import admin

from agents.models import AIAgent
from organizations.models import Organization
from webhook.models import WhatsAppConfig


class AIAgentInline(admin.StackedInline):
    model = AIAgent
    extra = 1
    max_num = 1
    fields = ("name", "system_prompt", "model_name", "temperature")
    verbose_name = "AI Agent"
    verbose_name_plural = "AI Agent (um por organização)"


class WhatsAppConfigInlineForm(forms.ModelForm):
    """Torna `agent` opcional no form — se vazio, é auto-ligado ao único agent
    da organização durante o save (ver ``OrganizationAdmin.save_related``)."""

    class Meta:
        model = WhatsAppConfig
        fields = ("twilio_phone_number", "agent", "is_active")

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.fields["agent"].required = False
        self.fields["agent"].help_text = (
            "Opcional no cadastro inicial — se deixado vazio, é ligado "
            "automaticamente ao agente criado acima."
        )
        self.fields["twilio_phone_number"].help_text = (
            "Formato: <code>whatsapp:+5551999990000</code>"
        )


class WhatsAppConfigInline(admin.StackedInline):
    model = WhatsAppConfig
    form = WhatsAppConfigInlineForm
    extra = 1
    max_num = 1
    fields = ("twilio_phone_number", "agent", "is_active")


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    readonly_fields = ("id", "created_at")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [AIAgentInline, WhatsAppConfigInline]

    def save_related(self, request, form, formsets, change):
        """Salva Organization + Agent + WhatsAppConfig em 1 passo.

        Se o formset de WhatsAppConfig trouxer ``agent`` vazio (comum no add,
        já que o agente é criado no mesmo submit), auto-liga ao único agente
        da organização.
        """
        organization: Organization = form.instance
        form.save_m2m()

        # Garante que AIAgent seja salvo antes do WhatsAppConfig para que o
        # auto-linking encontre o agente recém-criado.
        formsets_sorted = sorted(
            formsets, key=lambda fs: 0 if fs.model is AIAgent else 1
        )

        for fs in formsets_sorted:
            if fs.model is WhatsAppConfig:
                for inline_form in fs.forms:
                    if not getattr(inline_form, "cleaned_data", None):
                        continue
                    if inline_form.cleaned_data.get("DELETE"):
                        continue
                    if not inline_form.cleaned_data.get("agent"):
                        default_agent = (
                            AIAgent.objects.filter(organization=organization)
                            .order_by("name")
                            .first()
                        )
                        if default_agent is not None:
                            inline_form.instance.agent = default_agent
            self.save_formset(request, form, fs, change=change)
