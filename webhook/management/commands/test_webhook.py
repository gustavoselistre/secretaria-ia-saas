"""
webhook/management/commands/test_webhook.py

Simula uma request do Twilio WhatsApp localmente (sem precisar de ngrok).
Testa o fluxo completo: roteamento por número → ChatService → resposta TwiML.

Uso:
  python manage.py test_webhook --org technova
  python manage.py test_webhook --org technova --message "Quanto custa o frete?"
  python manage.py test_webhook --org technova --from "+5551988887777"
"""

from __future__ import annotations

import uuid as _uuid

from django.core.management.base import BaseCommand, CommandError
from django.test import RequestFactory

from agents.models import AIAgent
from organizations.models import Organization
from webhook.models import WhatsAppConfig
from webhook.views import twilio_whatsapp_webhook


class Command(BaseCommand):
    help = "Simula uma mensagem WhatsApp via Twilio para testar o webhook localmente."

    def add_arguments(self, parser):
        parser.add_argument(
            "--org",
            type=str,
            required=True,
            help="Slug ou UUID da Organization.",
        )
        parser.add_argument(
            "--message",
            type=str,
            default="Qual é o prazo para devolver um produto?",
            help="Mensagem simulada do cliente.",
        )
        parser.add_argument(
            "--from",
            type=str,
            dest="from_number",
            default="whatsapp:+5551988887777",
            help="Número do cliente (default: whatsapp:+5551988887777).",
        )

    def handle(self, *args, **options):
        org = self._resolve_organization(options["org"])
        message = options["message"]
        from_number = options["from_number"]

        self.stdout.write(self.style.HTTP_INFO(f"\n{'=' * 60}"))
        self.stdout.write(self.style.HTTP_INFO(f"  Organization: {org.name} ({org.slug})"))
        self.stdout.write(self.style.HTTP_INFO(f"{'=' * 60}\n"))

        # Garante WhatsAppConfig existe
        config = self._get_or_create_config(org)
        to_number = config.twilio_phone_number

        self.stdout.write(self.style.WARNING(f"▶ Agente: {config.agent.name}"))
        self.stdout.write(self.style.WARNING(f"▶ De: {from_number}"))
        self.stdout.write(self.style.WARNING(f"▶ Para: {to_number}"))
        self.stdout.write(self.style.WARNING(f'▶ Mensagem: "{message}"\n'))

        # Simula request POST do Twilio (sem validação de assinatura)
        self.stdout.write(self.style.WARNING("▶ Simulando request do Twilio…"))

        factory = RequestFactory()
        request = factory.post(
            "/webhook/whatsapp/",
            data={
                "From": from_number,
                "To": to_number,
                "Body": message,
                "MessageSid": f"SM{_uuid.uuid4().hex[:32]}",
                "AccountSid": "ACtest000000000000000000000000000",
            },
            HTTP_X_TWILIO_SIGNATURE="test-skip",
        )

        # Patch: pula validação de assinatura no modo teste
        import os
        original_token = os.environ.get("TWILIO_AUTH_TOKEN")
        os.environ["TWILIO_AUTH_TOKEN"] = ""

        try:
            response = twilio_whatsapp_webhook(request)
        except Exception as exc:
            raise CommandError(f"Erro no webhook: {exc}") from exc
        finally:
            if original_token is not None:
                os.environ["TWILIO_AUTH_TOKEN"] = original_token
            elif "TWILIO_AUTH_TOKEN" in os.environ:
                del os.environ["TWILIO_AUTH_TOKEN"]

        self.stdout.write(self.style.SUCCESS(f"\n  ✔ Status: {response.status_code}"))
        self.stdout.write(self.style.SUCCESS(f"  ✔ Content-Type: {response['Content-Type']}"))
        self.stdout.write(self.style.WARNING(f"\n▶ Resposta TwiML:"))
        self.stdout.write(f"  {response.content.decode()}\n")

        self.stdout.write(self.style.SUCCESS("✔ Webhook testado com sucesso!"))

    def _resolve_organization(self, identifier: str) -> Organization:
        try:
            uid = _uuid.UUID(identifier)
            return Organization.objects.get(id=uid)
        except (ValueError, Organization.DoesNotExist):
            pass
        try:
            return Organization.objects.get(slug=identifier)
        except Organization.DoesNotExist:
            raise CommandError(
                f"Organization '{identifier}' não encontrada."
            )

    def _get_or_create_config(self, org: Organization) -> WhatsAppConfig:
        """Busca ou cria WhatsAppConfig + AIAgent de teste."""
        config = WhatsAppConfig.objects.filter(organization=org).first()
        if config:
            return config

        self.stdout.write(self.style.NOTICE("  Criando configuração de teste…"))

        agent = AIAgent.objects.filter(organization=org).first()
        if not agent:
            agent = AIAgent.objects.create(
                organization=org,
                name=f"Secretária {org.name}",
                system_prompt=(
                    "Você é uma secretária virtual educada e eficiente. "
                    "Responda de forma clara usando apenas o contexto fornecido. "
                    "Responda sempre em português brasileiro."
                ),
                model_name="gemini-2.5-flash",
                temperature=0.7,
            )
            self.stdout.write(self.style.SUCCESS(f"  ✔ Agente criado: {agent.name}"))

        config = WhatsAppConfig.objects.create(
            organization=org,
            agent=agent,
            twilio_phone_number=f"whatsapp:+555199999{org.slug[:4].ljust(4, '0')}",
        )
        self.stdout.write(self.style.SUCCESS(
            f"  ✔ WhatsAppConfig criada: {config.twilio_phone_number}"
        ))
        return config
