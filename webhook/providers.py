"""
webhook/providers.py

Abstração provider-agnóstica para receber e enviar mensagens WhatsApp.

Implementações:
  - :class:`TwilioProvider`  — mantém o fluxo síncrono (TwiML)
  - :class:`UazapiProvider`  — uazapi.dev (webhook JSON + envio via /send/text)

O provider ativo é escolhido pela env var ``WHATSAPP_PROVIDER`` (default: ``twilio``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import requests
from django.conf import settings
from django.http import HttpResponse, HttpResponseForbidden

from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse

from webhook import uazapi_client
from webhook.models import WhatsAppConfig

logger = logging.getLogger(__name__)


@dataclass
class IncomingMessage:
    """Representação normalizada de uma mensagem WhatsApp recebida."""

    from_phone: str          # número do remetente (só dígitos, sem prefixo)
    body: str                # texto da mensagem (vazio se for áudio não transcrito)
    is_audio: bool           # indica se há áudio a transcrever
    tenant_key: str          # chave de roteamento (To no Twilio, instance no uazapi)
    raw: dict[str, Any]      # payload original (para download de mídia)


class WhatsAppProvider(ABC):
    """Interface para providers de WhatsApp."""

    name: str

    @abstractmethod
    def validate_signature(self, request) -> bool: ...

    @abstractmethod
    def parse_incoming(self, request) -> IncomingMessage | None: ...

    @abstractmethod
    def resolve_tenant(self, message: IncomingMessage) -> WhatsAppConfig | None: ...

    @abstractmethod
    def download_audio(
        self, message: IncomingMessage, config: WhatsAppConfig
    ) -> tuple[bytes, str]:
        """Retorna ``(audio_bytes, content_type)``."""

    @abstractmethod
    def send_reply(
        self, config: WhatsAppConfig, message: IncomingMessage, text: str
    ) -> HttpResponse:
        """Envia a resposta e retorna o HttpResponse adequado do webhook."""

    @abstractmethod
    def build_error_response(self, text: str) -> HttpResponse: ...


# ---------------------------------------------------------------------------
# Twilio
# ---------------------------------------------------------------------------


class TwilioProvider(WhatsAppProvider):
    name = "twilio"

    def validate_signature(self, request) -> bool:
        auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
        if not auth_token:
            if not settings.DEBUG:
                logger.critical(
                    "TWILIO_AUTH_TOKEN não configurado em produção — rejeitando request."
                )
                return False
            logger.warning(
                "TWILIO_AUTH_TOKEN não configurado — validação desabilitada (dev mode)."
            )
            return True

        validator = RequestValidator(auth_token)
        signature = request.META.get("HTTP_X_TWILIO_SIGNATURE", "")
        url = request.build_absolute_uri()
        params = request.POST.dict()
        return validator.validate(url, params, signature)

    def parse_incoming(self, request) -> IncomingMessage | None:
        from_number = request.POST.get("From", "")
        to_number = request.POST.get("To", "")
        body = request.POST.get("Body", "").strip()
        num_media = int(request.POST.get("NumMedia", "0") or "0")

        is_audio = False
        raw: dict[str, Any] = {}
        if num_media > 0 and not body:
            media_type = request.POST.get("MediaContentType0", "")
            media_url = request.POST.get("MediaUrl0", "")
            if media_type.startswith("audio/") and media_url:
                is_audio = True
                raw = {"media_url": media_url, "media_type": media_type}

        if not body and not is_audio:
            return None

        return IncomingMessage(
            from_phone=from_number.replace("whatsapp:", ""),
            body=body,
            is_audio=is_audio,
            tenant_key=to_number,
            raw=raw,
        )

    def resolve_tenant(self, message: IncomingMessage) -> WhatsAppConfig | None:
        try:
            return WhatsAppConfig.objects.select_related(
                "organization", "agent"
            ).get(
                phone_number=message.tenant_key,
                is_active=True,
                organization__is_active=True,
            )
        except WhatsAppConfig.DoesNotExist:
            return None

    def download_audio(
        self, message: IncomingMessage, config: WhatsAppConfig
    ) -> tuple[bytes, str]:
        media_url = message.raw["media_url"]
        media_type = message.raw["media_type"]
        account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
        auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
        resp = requests.get(
            media_url, auth=(account_sid, auth_token), timeout=30
        )
        resp.raise_for_status()
        return resp.content, media_type

    def send_reply(
        self, config: WhatsAppConfig, message: IncomingMessage, text: str
    ) -> HttpResponse:
        twiml = MessagingResponse()
        twiml.message(text)
        return HttpResponse(str(twiml), content_type="text/xml")

    def build_error_response(self, text: str) -> HttpResponse:
        twiml = MessagingResponse()
        twiml.message(text)
        return HttpResponse(str(twiml), content_type="text/xml")


# ---------------------------------------------------------------------------
# uazapi
# ---------------------------------------------------------------------------


def _extract_body(data: dict[str, Any]) -> tuple[str, bool]:
    """Extrai o corpo textual e flag de áudio de um ``data.message``.

    Retorna ``(body, is_audio)``.
    """
    msg = data.get("message") or {}
    # Texto simples
    if isinstance(msg, dict):
        if text := msg.get("conversation"):
            return str(text).strip(), False
        ext = msg.get("extendedTextMessage") or {}
        if isinstance(ext, dict) and (text := ext.get("text")):
            return str(text).strip(), False
        if msg.get("audioMessage"):
            return "", True
    # uazapi às vezes entrega o texto direto em data.body / data.text
    if text := data.get("text"):
        return str(text).strip(), False
    if text := data.get("body"):
        return str(text).strip(), False
    msg_type = (data.get("messageType") or "").lower()
    if "audio" in msg_type:
        return "", True
    return "", False


def _extract_sender(data: dict[str, Any]) -> str:
    """Extrai o telefone do remetente (apenas dígitos)."""
    key = data.get("key") or {}
    if isinstance(key, dict):
        jid = key.get("remoteJid") or key.get("RemoteJID") or ""
    else:
        jid = ""
    if not jid:
        jid = data.get("sender") or data.get("from") or data.get("chatid") or ""
    if isinstance(jid, str) and "@" in jid:
        jid = jid.split("@", 1)[0]
    return "".join(ch for ch in str(jid) if ch.isdigit())


def _is_from_me(data: dict[str, Any]) -> bool:
    key = data.get("key") or {}
    if isinstance(key, dict):
        val = key.get("fromMe", key.get("FromMe"))
        if isinstance(val, bool):
            return val
    return bool(data.get("fromMe") or data.get("FromMe"))


class UazapiProvider(WhatsAppProvider):
    name = "uazapi"

    def validate_signature(self, request) -> bool:
        secret = os.environ.get("UAZAPI_WEBHOOK_HMAC_SECRET", "")
        if not secret:
            if not settings.DEBUG:
                logger.critical(
                    "UAZAPI_WEBHOOK_HMAC_SECRET não configurado em produção — "
                    "rejeitando request."
                )
                return False
            logger.warning(
                "UAZAPI_WEBHOOK_HMAC_SECRET não configurado — "
                "validação desabilitada (dev mode)."
            )
            return True

        signature = request.META.get("HTTP_X_HMAC_SIGNATURE", "")
        if not signature:
            logger.warning("Header x-hmac-signature ausente na request uazapi.")
            return False

        expected = hmac.new(
            secret.encode("utf-8"),
            request.body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(signature.lower(), expected.lower())

    def parse_incoming(self, request) -> IncomingMessage | None:
        try:
            payload = json.loads(request.body or b"{}")
        except ValueError:
            logger.warning("Payload uazapi inválido (JSON mal formado).")
            return None

        event = payload.get("event") or payload.get("type") or ""
        # Ignora eventos que não são mensagens recebidas
        if event and "message" not in str(event).lower():
            logger.debug("Evento uazapi ignorado: %s", event)
            return None

        data = payload.get("data") or payload.get("message") or payload

        if _is_from_me(data):
            return None  # ignora mensagens enviadas pelo próprio bot

        body, is_audio = _extract_body(data)
        if not body and not is_audio:
            return None

        sender = _extract_sender(data)
        instance_id = (
            payload.get("instance")
            or payload.get("instanceId")
            or payload.get("instance_id")
            or data.get("instance")
            or ""
        )

        return IncomingMessage(
            from_phone=sender,
            body=body,
            is_audio=is_audio,
            tenant_key=str(instance_id),
            raw={"payload": payload, "data": data},
        )

    def resolve_tenant(self, message: IncomingMessage) -> WhatsAppConfig | None:
        if not message.tenant_key:
            return None
        try:
            return WhatsAppConfig.objects.select_related(
                "organization", "agent"
            ).get(
                uazapi_instance_id=message.tenant_key,
                is_active=True,
                organization__is_active=True,
            )
        except WhatsAppConfig.DoesNotExist:
            return None

    def download_audio(
        self, message: IncomingMessage, config: WhatsAppConfig
    ) -> tuple[bytes, str]:
        data = message.raw.get("data") or {}
        msg = data.get("message") or {}
        audio = msg.get("audioMessage") or {}
        mimetype = audio.get("mimetype") or "audio/ogg"

        # uazapi pode entregar o conteúdo em base64 direto no payload
        inline = (
            data.get("fileContent")
            or audio.get("fileContent")
            or data.get("base64")
        )
        if inline:
            try:
                return base64.b64decode(inline), mimetype
            except (ValueError, TypeError) as exc:
                logger.warning("Falha ao decodificar áudio inline: %s", exc)

        # caso contrário, chama /message/download
        message_id = (
            (data.get("key") or {}).get("id")
            or data.get("id")
            or data.get("messageId")
        )
        if not message_id:
            raise RuntimeError("uazapi: não foi possível localizar id da mensagem de áudio.")
        result = uazapi_client.download_media(
            token=config.uazapi_instance_token or "",
            message_id=str(message_id),
        )
        content_b64 = result.get("fileContent") or result.get("base64") or ""
        if not content_b64:
            raise RuntimeError(f"uazapi /message/download não retornou fileContent: {result}")
        return base64.b64decode(content_b64), mimetype

    def send_reply(
        self, config: WhatsAppConfig, message: IncomingMessage, text: str
    ) -> HttpResponse:
        try:
            uazapi_client.send_text(
                token=config.uazapi_instance_token or "",
                number=message.from_phone,
                text=text,
            )
        except uazapi_client.UazapiError as exc:
            logger.error("Falha ao enviar resposta uazapi: %s", exc)
        return HttpResponse(status=200)

    def build_error_response(self, text: str) -> HttpResponse:
        # Não dá pra responder via uazapi sem conhecer o tenant — retorna 200 vazio.
        return HttpResponse(status=200)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_whatsapp_provider() -> WhatsAppProvider:
    """Seleciona o provider ativo pela env var ``WHATSAPP_PROVIDER``."""
    provider_name = os.environ.get("WHATSAPP_PROVIDER", "twilio").lower()
    if provider_name == "uazapi":
        return UazapiProvider()
    if provider_name == "twilio":
        return TwilioProvider()
    logger.warning(
        "WHATSAPP_PROVIDER=%s desconhecido — caindo para Twilio.", provider_name
    )
    return TwilioProvider()
