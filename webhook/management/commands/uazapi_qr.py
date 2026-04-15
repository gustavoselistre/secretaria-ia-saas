"""
webhook/management/commands/uazapi_qr.py

Reexibe o QR code de uma instância uazapi (útil se a sessão desconectar).

Uso:
  python manage.py uazapi_qr --org minha-empresa
"""

from __future__ import annotations

import base64
import uuid as _uuid

from django.core.management.base import BaseCommand, CommandError

from organizations.models import Organization
from webhook.models import WhatsAppConfig


class Command(BaseCommand):
    help = "Reexibe o QR code da instância uazapi de um tenant."

    def add_arguments(self, parser):
        parser.add_argument("--org", required=True, help="Slug ou UUID da Organization.")

    def handle(self, *args, **options):
        from webhook import uazapi_client

        org = self._resolve_org(options["org"])
        try:
            config = WhatsAppConfig.objects.get(organization=org)
        except WhatsAppConfig.DoesNotExist:
            raise CommandError(f"Organization '{org.slug}' não tem WhatsAppConfig.")

        if not config.uazapi_instance_token:
            raise CommandError(
                f"Org '{org.slug}' não tem instância uazapi configurada. "
                "Rode 'setup_tenant --provider uazapi' primeiro."
            )

        self.stdout.write(self.style.WARNING("▶ Solicitando QR code…"))
        try:
            result = uazapi_client.connect_instance(token=config.uazapi_instance_token)
        except uazapi_client.UazapiError as exc:
            raise CommandError(f"Falha ao obter QR: {exc}") from exc

        qr_data = (
            result.get("qrcode")
            or result.get("qr")
            or (result.get("instance") or {}).get("qrcode")
        )
        if not qr_data:
            raise CommandError(f"Resposta sem campo qrcode: {result}")

        if qr_data.startswith("data:image"):
            qr_data = qr_data.split(",", 1)[-1]
        try:
            png_bytes = base64.b64decode(qr_data)
        except (ValueError, TypeError):
            png_bytes = None

        if png_bytes:
            path = "/tmp/uazapi_qr.png"
            with open(path, "wb") as fh:
                fh.write(png_bytes)
            self.stdout.write(self.style.SUCCESS(f"✔ QR salvo em {path}"))
        else:
            self.stdout.write(qr_data)

    def _resolve_org(self, identifier: str) -> Organization:
        try:
            return Organization.objects.get(id=_uuid.UUID(identifier))
        except (ValueError, Organization.DoesNotExist):
            pass
        try:
            return Organization.objects.get(slug=identifier)
        except Organization.DoesNotExist:
            raise CommandError(f"Organization '{identifier}' não encontrada.")
