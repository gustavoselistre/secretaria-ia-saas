"""
webhook/views.py

Endpoint para receber mensagens do WhatsApp. O provider ativo (Twilio ou uazapi)
é escolhido em tempo de request via ``webhook.providers.get_whatsapp_provider``.
"""

from __future__ import annotations

import logging

from django.http import HttpResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from chat.services import ChatService, get_llm_provider
from webhook.providers import get_whatsapp_provider

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def whatsapp_webhook(request) -> HttpResponse:
    """Webhook unificado: recebe mensagem, gera resposta via IA e envia de volta."""

    provider = get_whatsapp_provider()

    # 1. Valida autenticidade (assinatura Twilio ou HMAC uazapi)
    if not provider.validate_signature(request):
        logger.warning("Assinatura %s inválida — request rejeitada.", provider.name)
        return HttpResponseForbidden("Invalid signature")

    # 2. Normaliza payload em IncomingMessage
    message = provider.parse_incoming(request)
    if message is None:
        return HttpResponse(status=204)

    logger.info(
        "Mensagem recebida (%s): %s → tenant=%s: %s",
        provider.name,
        message.from_phone,
        message.tenant_key,
        message.body[:50] if message.body else "[áudio]",
    )

    # 3. Resolve tenant
    config = provider.resolve_tenant(message)
    if config is None:
        logger.warning(
            "Nenhuma config ativa para tenant_key=%s (provider=%s)",
            message.tenant_key,
            provider.name,
        )
        return provider.build_error_response(
            "Desculpe, este serviço não está disponível no momento."
        )

    org = config.organization
    logger.info(
        "Tenant identificado: %s (org_id=%s)",
        org.slug,
        org.id,
        extra={"organization_id": str(org.id), "organization_slug": org.slug},
    )

    # 4. Transcreve áudio se necessário
    if message.is_audio and not message.body:
        try:
            audio_bytes, ctype = provider.download_audio(message, config)
            message.body = get_llm_provider().transcribe_audio(audio_bytes, ctype)
            logger.info("Áudio transcrito: %s", message.body[:100])
        except Exception as exc:
            logger.error("Erro ao transcrever áudio: %s", exc)
            return provider.send_reply(
                config,
                message,
                "Desculpe, não consegui entender o áudio. Pode escrever a mensagem?",
            )

    if not message.body:
        return HttpResponse(status=204)

    # 5. Gera resposta via ChatService (RAG + LLM)
    try:
        service = ChatService()
        assistant_response = service.generate_response(
            agent=config.agent,
            customer_phone=message.from_phone,
            user_message=message.body,
        )
    except Exception as exc:
        logger.error(
            "Erro ao gerar resposta para %s (org=%s): %s",
            message.from_phone,
            org.slug,
            exc,
            extra={"organization_id": str(org.id), "organization_slug": org.slug},
        )
        assistant_response = (
            "Desculpe, ocorreu um erro ao processar sua mensagem. "
            "Por favor, tente novamente em instantes."
        )

    # 6. Envia resposta (TwiML no Twilio, POST /send/text no uazapi)
    return provider.send_reply(config, message, assistant_response)


# Alias retrocompatível — o nome antigo ainda é referenciado em test_webhook.py
twilio_whatsapp_webhook = whatsapp_webhook
