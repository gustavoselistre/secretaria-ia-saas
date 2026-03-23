"""
organizations/management/commands/setup_tenant.py

Cria ou atualiza os dados necessários para um tenant funcionar com o WhatsApp:
  - Organization
  - AIAgent
  - WhatsAppConfig

Uso:
    python manage.py setup_tenant \
        --org "Minha Empresa" \
        --slug minha-empresa \
        --phone "whatsapp:+14155238886" \
        --prompt "Você é uma secretária virtual..."
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from agents.models import AIAgent
from organizations.models import Organization
from webhook.models import WhatsAppConfig


class Command(BaseCommand):
    help = "Cria ou atualiza Organization, AIAgent e WhatsAppConfig para um tenant."

    def add_arguments(self, parser):
        parser.add_argument("--org", required=True, help="Nome da organização")
        parser.add_argument("--slug", required=True, help="Slug único da organização (ex: minha-empresa)")
        parser.add_argument("--phone", required=True, help="Número Twilio WhatsApp (ex: whatsapp:+14155238886)")
        parser.add_argument(
            "--prompt",
            default="Você é uma secretária virtual prestativa. Responda de forma clara e educada.",
            help="System prompt do agente de IA",
        )
        parser.add_argument("--model", default="gpt-4o", help="Modelo LLM do agente (padrão: gpt-4o)")

    def handle(self, *args, **options):
        org_name = options["org"]
        slug = options["slug"]
        phone = options["phone"]
        prompt = options["prompt"]
        model = options["model"]

        # 1. Organization
        org, org_created = Organization.objects.get_or_create(
            slug=slug,
            defaults={"name": org_name, "is_active": True},
        )
        if not org_created and org.name != org_name:
            org.name = org_name
            org.save(update_fields=["name"])

        status = "criada" if org_created else "já existente"
        self.stdout.write(f"Organization '{org.name}' ({org.slug}): {status}")

        # 2. AIAgent (um por org — reutiliza o primeiro se já existir)
        agent = AIAgent.objects.filter(organization=org).first()
        if agent is None:
            agent = AIAgent.objects.create(
                organization=org,
                name=f"Agente {org_name}",
                system_prompt=prompt,
                model_name=model,
            )
            self.stdout.write(f"AIAgent '{agent.name}': criado")
        else:
            self.stdout.write(f"AIAgent '{agent.name}': já existente")

        # 3. WhatsAppConfig
        config, config_created = WhatsAppConfig.objects.get_or_create(
            organization=org,
            defaults={
                "agent": agent,
                "twilio_phone_number": phone,
                "is_active": True,
            },
        )
        if not config_created and config.twilio_phone_number != phone:
            config.twilio_phone_number = phone
            config.agent = agent
            config.save(update_fields=["twilio_phone_number", "agent"])

        status = "criada" if config_created else "já existente"
        self.stdout.write(f"WhatsAppConfig '{config.twilio_phone_number}': {status}")

        self.stdout.write(self.style.SUCCESS(
            f"\nTenant '{org_name}' pronto. "
            f"Número Twilio: {config.twilio_phone_number}"
        ))
        self.stdout.write(
            f"\nPróximo passo: configure o webhook no Twilio Console apontando para\n"
            f"  https://<seu-ngrok>.ngrok-free.app/webhook/whatsapp/\n"
        )
