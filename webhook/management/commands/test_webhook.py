"""
webhook/management/commands/test_webhook.py

Simula uma request de WhatsApp localmente (sem precisar de ngrok).
Suporta os dois providers via --provider.

Uso:
  python manage.py test_webhook --org technova
  python manage.py test_webhook --org technova --provider uazapi
  python manage.py test_webhook --org technova --message "Quanto custa?"
  python manage.py test_webhook --org technova --from "+5551988887777"
"""

from __future__ import annotations

import json
import os
import uuid as _uuid
from unittest.mock import patch

from django.core.management.base import BaseCommand, CommandError
from django.test import RequestFactory

from agents.models import AIAgent
from organizations.models import Organization
from webhook.models import WhatsAppConfig
from webhook.views import whatsapp_webhook


class Command(BaseCommand):
    help = "Simula uma mensagem WhatsApp para testar o webhook localmente."

    def add_arguments(self, parser):
        parser.add_argument("--org", type=str, required=True, help="Slug ou UUID da Organization.")
        parser.add_argument(
            "--provider",
            type=str,
            choices=["twilio", "uazapi"],
            default=os.environ.get("WHATSAPP_PROVIDER", "twilio"),
            help="Provider a simular (default: WHATSAPP_PROVIDER env ou twilio).",
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
            default="+5551988887777",
            help="Número do cliente (ex: +5551988887777).",
        )

    def handle(self, *args, **options):
        org = self._resolve_organization(options["org"])
        provider = options["provider"]
        message = options["message"]
        from_number = options["from_number"]

        self.stdout.write(self.style.HTTP_INFO(f"\n{'=' * 60}"))
        self.stdout.write(self.style.HTTP_INFO(f"  Organization: {org.name} ({org.slug})"))
        self.stdout.write(self.style.HTTP_INFO(f"  Provider: {provider}"))
        self.stdout.write(self.style.HTTP_INFO(f"{'=' * 60}\n"))

        config = self._get_or_create_config(org, provider)

        if provider == "twilio":
            request = self._build_twilio_request(config, from_number, message)
        else:
            request = self._build_uazapi_request(config, from_number, message)

        # Desliga validação de assinatura no modo teste
        env_overrides = {
            "WHATSAPP_PROVIDER": provider,
            "TWILIO_AUTH_TOKEN": "",
            "UAZAPI_WEBHOOK_HMAC_SECRET": "",
        }

        self.stdout.write(self.style.WARNING("▶ Simulando request…\n"))

        with patch.dict(os.environ, env_overrides), patch(
            "webhook.providers.uazapi_client.send_text",
            return_value={"simulated": True},
        ) as mock_send:
            try:
                response = whatsapp_webhook(request)
            except Exception as exc:
                raise CommandError(f"Erro no webhook: {exc}") from exc

            self.stdout.write(self.style.SUCCESS(f"✔ Status: {response.status_code}"))
            self.stdout.write(
                self.style.SUCCESS(
                    f"✔ Content-Type: {response.get('Content-Type', '')}"
                )
            )
            if response.content:
                self.stdout.write(self.style.WARNING("\n▶ Corpo da resposta:"))
                self.stdout.write(f"  {response.content.decode(errors='replace')}")
            if provider == "uazapi" and mock_send.called:
                call = mock_send.call_args
                self.stdout.write(self.style.WARNING("\n▶ send_text chamada com:"))
                self.stdout.write(f"  number={call.kwargs.get('number')}")
                self.stdout.write(f"  text={call.kwargs.get('text')}")

        self.stdout.write(self.style.SUCCESS("\n✔ Webhook testado com sucesso!"))

    def _build_twilio_request(self, config: WhatsAppConfig, from_number: str, message: str):
        factory = RequestFactory()
        return factory.post(
            "/webhook/whatsapp/",
            data={
                "From": f"whatsapp:{from_number}",
                "To": config.phone_number,
                "Body": message,
                "MessageSid": f"SM{_uuid.uuid4().hex[:32]}",
                "AccountSid": "ACtest000000000000000000000000000",
            },
            HTTP_X_TWILIO_SIGNATURE="test-skip",
        )

    def _build_uazapi_request(self, config: WhatsAppConfig, from_number: str, message: str):
        factory = RequestFactory()
        payload = {
            "event": "messages.upsert",
            "instance": config.uazapi_instance_id,
            "data": {
                "key": {
                    "id": f"MSG{_uuid.uuid4().hex[:16]}",
                    "fromMe": False,
                    "remoteJid": f"{from_number.lstrip('+')}@s.whatsapp.net",
                },
                "message": {"conversation": message},
                "messageType": "conversation",
                "pushName": "Teste CLI",
            },
        }
        return factory.post(
            "/webhook/whatsapp/",
            data=json.dumps(payload),
            content_type="application/json",
        )

    def _resolve_organization(self, identifier: str) -> Organization:
        try:
            uid = _uuid.UUID(identifier)
            return Organization.objects.get(id=uid)
        except (ValueError, Organization.DoesNotExist):
            pass
        try:
            return Organization.objects.get(slug=identifier)
        except Organization.DoesNotExist:
            raise CommandError(f"Organization '{identifier}' não encontrada.")

    def _get_or_create_config(
        self, org: Organization, provider: str
    ) -> WhatsAppConfig:
        """Busca ou cria WhatsAppConfig compatível com o provider escolhido."""
        config = WhatsAppConfig.objects.filter(organization=org).first()
        agent = AIAgent.objects.filter(organization=org).first()
        if not agent:
            agent = AIAgent.objects.create(
                organization=org,
                name=f"Secretária {org.name}",
                system_prompt=(
                    "Você é uma secretária virtual educada e eficiente. "
                    "Responda em português brasileiro."
                ),
                model_name="gemini-2.5-flash",
                temperature=0.7,
            )
            self.stdout.write(self.style.SUCCESS(f"  ✔ Agente criado: {agent.name}"))

        if config is None:
            suffix = org.slug[:4].ljust(4, "0")
            config = WhatsAppConfig.objects.create(
                organization=org,
                agent=agent,
                phone_number=(
                    f"whatsapp:+555199999{suffix}"
                    if provider == "twilio"
                    else f"555199999{suffix}"
                ),
                uazapi_instance_id=(
                    f"inst-test-{org.slug}" if provider == "uazapi" else None
                ),
                uazapi_instance_token=(
                    "test-token" if provider == "uazapi" else None
                ),
            )
            self.stdout.write(
                self.style.SUCCESS(f"  ✔ WhatsAppConfig criada: {config.phone_number}")
            )
            return config

        # Garante que campos uazapi existam quando provider=uazapi
        if provider == "uazapi" and not config.uazapi_instance_id:
            config.uazapi_instance_id = f"inst-test-{org.slug}"
            config.uazapi_instance_token = "test-token"
            config.save(update_fields=["uazapi_instance_id", "uazapi_instance_token"])
        return config
