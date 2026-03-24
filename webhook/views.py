"""
webhook/views.py

Endpoint para receber mensagens do WhatsApp via Twilio.
Valida assinatura, identifica o tenant e responde via ChatService.
"""

from __future__ import annotations

import logging
import os

from django.conf import settings
from django.http import HttpResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from twilio.request_validator import RequestValidator
from twilio.twiml.messaging_response import MessagingResponse

import requests

from chat.services import ChatService, get_llm_provider
from webhook.models import WhatsAppConfig

logger = logging.getLogger(__name__)


def _validate_twilio_signature(request) -> bool:
    """Valida X-Twilio-Signature para garantir autenticidade."""
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


@csrf_exempt
@require_POST
def twilio_whatsapp_webhook(request) -> HttpResponse:
    """Recebe mensagem do Twilio WhatsApp, gera resposta via IA e devolve TwiML."""

    # 1. Valida assinatura Twilio
    if not _validate_twilio_signature(request):
        logger.warning("Assinatura Twilio inválida — request rejeitada.")
        return HttpResponseForbidden("Invalid signature")

    # 2. Extrai dados da mensagem
    from_number = request.POST.get("From", "")       # whatsapp:+5551...
    to_number = request.POST.get("To", "")             # whatsapp:+5551...
    body = request.POST.get("Body", "").strip()
    num_media = int(request.POST.get("NumMedia", "0"))

    # 2b. Se tem áudio, transcreve para texto
    if num_media > 0 and not body:
        media_type = request.POST.get("MediaContentType0", "")
        media_url = request.POST.get("MediaUrl0", "")

        if media_type.startswith("audio/") and media_url:
            logger.info("Áudio recebido de %s: %s (%s)", from_number, media_url, media_type)
            try:
                # Baixar áudio do Twilio (requer autenticação)
                account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
                auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
                audio_response = requests.get(
                    media_url, auth=(account_sid, auth_token), timeout=30,
                )
                audio_response.raise_for_status()

                # Transcrever via LLM provider
                provider = get_llm_provider()
                body = provider.transcribe_audio(audio_response.content, media_type)
                logger.info("Áudio transcrito: %s", body[:100])
            except Exception as exc:
                logger.error("Erro ao transcrever áudio: %s", exc)
                body = ""

    if not body:
        return HttpResponse(status=204)

    logger.info("Mensagem recebida: %s → %s: %s", from_number, to_number, body[:50])

    # 3. Identifica o tenant pelo número de destino
    try:
        config = WhatsAppConfig.objects.select_related(
            "organization", "agent",
        ).get(
            twilio_phone_number=to_number,
            is_active=True,
            organization__is_active=True,
        )
    except WhatsAppConfig.DoesNotExist:
        logger.warning("Nenhuma config ativa para o número %s", to_number)
        twiml = MessagingResponse()
        twiml.message("Desculpe, este serviço não está disponível no momento.")
        return HttpResponse(str(twiml), content_type="text/xml")

    org = config.organization
    logger.info(
        "Tenant identificado: %s (org_id=%s)",
        org.slug,
        org.id,
        extra={"organization_id": str(org.id), "organization_slug": org.slug},
    )

    # 4. Gera resposta via ChatService (RAG + LLM)
    customer_phone = from_number.replace("whatsapp:", "")

    try:
        service = ChatService()
        assistant_response = service.generate_response(
            agent=config.agent,
            customer_phone=customer_phone,
            user_message=body,
        )
    except Exception as exc:
        logger.error(
            "Erro ao gerar resposta para %s (org=%s): %s",
            customer_phone,
            org.slug,
            exc,
            extra={"organization_id": str(org.id), "organization_slug": org.slug},
        )
        assistant_response = (
            "Desculpe, ocorreu um erro ao processar sua mensagem. "
            "Por favor, tente novamente em instantes."
        )

    # 5. Retorna resposta como TwiML
    twiml = MessagingResponse()
    twiml.message(assistant_response)

    return HttpResponse(str(twiml), content_type="text/xml")
