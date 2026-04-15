"""
organizations/management/commands/setup_tenant.py

Cria ou atualiza os dados necessários para um tenant funcionar com o WhatsApp:
  - Organization
  - AIAgent
  - WhatsAppConfig

Para provider=uazapi, também cria a instância na uazapi.dev, configura o webhook
e exibe o QR code no terminal (escaneável pelo WhatsApp).

Uso (Twilio):
    python manage.py setup_tenant \\
        --org "Minha Empresa" --slug minha-empresa \\
        --phone "whatsapp:+14155238886" \\
        --prompt "Você é uma secretária virtual..."

Uso (uazapi):
    python manage.py setup_tenant \\
        --org "Minha Empresa" --slug minha-empresa \\
        --phone "+5551999990000" --provider uazapi \\
        --webhook-url https://meuapp.com/webhook/whatsapp/
"""

from __future__ import annotations

import base64
import os
import time

from django.core.management.base import BaseCommand, CommandError

from agents.models import AIAgent
from organizations.models import Organization
from webhook.models import WhatsAppConfig


class Command(BaseCommand):
    help = "Cria ou atualiza Organization, AIAgent e WhatsAppConfig para um tenant."

    def add_arguments(self, parser):
        parser.add_argument("--org", required=True, help="Nome da organização")
        parser.add_argument("--slug", required=True, help="Slug único da organização.")
        parser.add_argument(
            "--phone",
            required=True,
            help=(
                "Número WhatsApp do bot. Twilio: 'whatsapp:+14155238886'. "
                "uazapi: '+5551999990000' (será armazenado só com dígitos)."
            ),
        )
        parser.add_argument(
            "--provider",
            choices=["twilio", "uazapi"],
            default=os.environ.get("WHATSAPP_PROVIDER", "twilio"),
        )
        parser.add_argument(
            "--prompt",
            default="Você é uma secretária virtual prestativa. Responda de forma clara e educada.",
        )
        parser.add_argument("--model", default="gpt-4o", help="Modelo LLM do agente.")
        parser.add_argument(
            "--webhook-url",
            default=os.environ.get("UAZAPI_WEBHOOK_URL", ""),
            help="URL pública do webhook (uazapi). Ex: https://meuapp.com/webhook/whatsapp/",
        )

    def handle(self, *args, **options):
        org_name = options["org"]
        slug = options["slug"]
        phone = options["phone"]
        provider = options["provider"]
        prompt = options["prompt"]
        model = options["model"]
        webhook_url = options["webhook_url"]

        # 1. Organization
        org, org_created = Organization.objects.get_or_create(
            slug=slug,
            defaults={"name": org_name, "is_active": True},
        )
        if not org_created and org.name != org_name:
            org.name = org_name
            org.save(update_fields=["name"])
        self.stdout.write(
            f"Organization '{org.name}' ({org.slug}): "
            f"{'criada' if org_created else 'já existente'}"
        )

        # 2. AIAgent
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
        if provider == "uazapi":
            self._setup_uazapi(org, agent, phone, slug, webhook_url)
        else:
            self._setup_twilio(org, agent, phone)

    # ------------------------------------------------------------------
    # Twilio
    # ------------------------------------------------------------------

    def _setup_twilio(self, org: Organization, agent: AIAgent, phone: str) -> None:
        config, created = WhatsAppConfig.objects.get_or_create(
            organization=org,
            defaults={
                "agent": agent,
                "phone_number": phone,
                "is_active": True,
            },
        )
        if not created and (
            config.phone_number != phone or config.agent_id != agent.id
        ):
            config.phone_number = phone
            config.agent = agent
            config.save(update_fields=["phone_number", "agent"])

        self.stdout.write(
            f"WhatsAppConfig '{config.phone_number}': "
            f"{'criada' if created else 'atualizada'}"
        )
        self.stdout.write(self.style.SUCCESS(
            f"\nTenant '{org.name}' pronto. Número Twilio: {config.phone_number}"
        ))
        self.stdout.write(
            "\nPróximo passo: configure o webhook no Twilio Console apontando para\n"
            "  https://<seu-dominio>/webhook/whatsapp/\n"
        )

    # ------------------------------------------------------------------
    # uazapi
    # ------------------------------------------------------------------

    def _setup_uazapi(
        self,
        org: Organization,
        agent: AIAgent,
        phone: str,
        slug: str,
        webhook_url: str,
    ) -> None:
        from webhook import uazapi_client

        normalized_phone = "".join(ch for ch in phone if ch.isdigit())

        config = WhatsAppConfig.objects.filter(organization=org).first()

        # Cria instância se ainda não existir
        if config is None or not config.uazapi_instance_token:
            self.stdout.write(self.style.WARNING("▶ Criando instância na uazapi…"))
            try:
                result = uazapi_client.init_instance(name=slug)
            except uazapi_client.UazapiError as exc:
                raise CommandError(f"Falha ao criar instância uazapi: {exc}") from exc

            token = (
                result.get("token")
                or (result.get("instance") or {}).get("token")
            )
            instance_id = (
                result.get("id")
                or (result.get("instance") or {}).get("id")
                or result.get("instanceId")
            )
            if not token or not instance_id:
                raise CommandError(
                    f"Resposta de /instance/init sem token/id: {result}"
                )

            if config is None:
                config = WhatsAppConfig.objects.create(
                    organization=org,
                    agent=agent,
                    phone_number=normalized_phone,
                    uazapi_instance_id=str(instance_id),
                    uazapi_instance_token=str(token),
                    is_active=True,
                )
            else:
                config.agent = agent
                config.phone_number = normalized_phone
                config.uazapi_instance_id = str(instance_id)
                config.uazapi_instance_token = str(token)
                config.is_active = True
                config.save()
            self.stdout.write(
                self.style.SUCCESS(f"  ✔ Instância criada: {instance_id}")
            )
        else:
            self.stdout.write(
                f"Instância uazapi existente: {config.uazapi_instance_id}"
            )

        # Configura webhook
        if webhook_url:
            self.stdout.write(
                self.style.WARNING(f"▶ Configurando webhook: {webhook_url}")
            )
            try:
                uazapi_client.set_webhook(
                    token=config.uazapi_instance_token,
                    webhook_url=webhook_url,
                    events=["messages"],
                )
                self.stdout.write(self.style.SUCCESS("  ✔ Webhook configurado"))
            except uazapi_client.UazapiError as exc:
                self.stdout.write(
                    self.style.ERROR(f"  ✗ Falha ao configurar webhook: {exc}")
                )
        else:
            self.stdout.write(
                self.style.WARNING(
                    "  ! --webhook-url não fornecido (e UAZAPI_WEBHOOK_URL não setado). "
                    "Configure o webhook manualmente."
                )
            )

        # Conecta e exibe QR code
        self.stdout.write(self.style.WARNING("▶ Solicitando QR code…"))
        try:
            conn = uazapi_client.connect_instance(token=config.uazapi_instance_token)
        except uazapi_client.UazapiError as exc:
            raise CommandError(f"Falha ao conectar instância: {exc}") from exc

        qr_data = (
            conn.get("qrcode")
            or conn.get("qr")
            or (conn.get("instance") or {}).get("qrcode")
        )
        if qr_data:
            self._render_qr(qr_data)
        else:
            self.stdout.write(
                self.style.WARNING(
                    "  ! Resposta sem campo qrcode. Conteúdo: "
                    f"{list(conn.keys())}"
                )
            )

        # Polling curto de status
        self._await_connection(config.uazapi_instance_token)

        self.stdout.write(self.style.SUCCESS(
            f"\nTenant '{org.name}' pronto. "
            f"Instância uazapi: {config.uazapi_instance_id}"
        ))

    def _render_qr(self, qr_data: str) -> None:
        """Decodifica e exibe o QR code no terminal (ASCII)."""
        raw_png: bytes | None = None
        if qr_data.startswith("data:image"):
            qr_data = qr_data.split(",", 1)[-1]
        try:
            raw_png = base64.b64decode(qr_data)
        except (ValueError, TypeError):
            raw_png = None

        if raw_png:
            # Salva PNG em /tmp pra quem quiser abrir no visualizador
            path = "/tmp/uazapi_qr.png"
            try:
                with open(path, "wb") as fh:
                    fh.write(raw_png)
                self.stdout.write(
                    self.style.SUCCESS(f"  ✔ QR code salvo em {path}")
                )
            except OSError as exc:
                self.stdout.write(
                    self.style.WARNING(f"  ! Não foi possível salvar PNG: {exc}")
                )

        # Tenta renderizar em ASCII se a lib 'qrcode' estiver disponível
        try:
            import qrcode  # type: ignore
            from io import StringIO

            # qr_data pode ser o conteúdo do QR (string) ou base64 de PNG.
            # Se for base64, não dá pra re-extrair o conteúdo sem decoder;
            # imprimimos instrução pro usuário.
            if raw_png:
                self.stdout.write(
                    "  → Abra /tmp/uazapi_qr.png ou escaneie o código salvo."
                )
            else:
                qr = qrcode.QRCode(border=1)
                qr.add_data(qr_data)
                qr.make(fit=True)
                buf = StringIO()
                qr.print_ascii(out=buf, invert=True)
                self.stdout.write(buf.getvalue())
        except ImportError:
            self.stdout.write(
                "  ! Instale 'qrcode[pil]' para renderizar o QR no terminal."
            )

    def _await_connection(self, token: str, timeout: int = 120) -> None:
        from webhook import uazapi_client

        self.stdout.write(
            self.style.WARNING(
                f"▶ Aguardando conexão (até {timeout}s). Escaneie o QR no WhatsApp…"
            )
        )
        deadline = time.monotonic() + timeout
        last_status = ""
        while time.monotonic() < deadline:
            try:
                status = uazapi_client.instance_status(token=token)
            except uazapi_client.UazapiError as exc:
                self.stdout.write(self.style.WARNING(f"  ! Status indisponível: {exc}"))
                time.sleep(3)
                continue

            state = (
                status.get("status")
                or (status.get("instance") or {}).get("status")
                or ""
            )
            if state and state != last_status:
                self.stdout.write(f"  status: {state}")
                last_status = state
            if str(state).lower() in {"connected", "open", "authenticated"}:
                self.stdout.write(self.style.SUCCESS("  ✔ Instância conectada!"))
                return
            time.sleep(3)
        self.stdout.write(
            self.style.WARNING(
                "  ! Timeout aguardando conexão. Rode 'python manage.py uazapi_qr "
                "--org <slug>' para reexibir o QR."
            )
        )
