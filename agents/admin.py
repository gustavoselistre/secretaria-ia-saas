from django.contrib import admin

from agents.models import AIAgent


@admin.register(AIAgent)
class AIAgentAdmin(admin.ModelAdmin):
    list_display = ("name", "organization", "model_name", "temperature")
    list_filter = ("organization", "model_name")
    search_fields = ("name",)
    readonly_fields = ("id",)
    fieldsets = (
        (None, {"fields": ("id", "organization", "name")}),
        ("Configuração LLM", {"fields": ("system_prompt", "model_name", "temperature")}),
    )
