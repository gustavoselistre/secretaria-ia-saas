"""
webhook/tests.py

Testes de integração para o endpoint ``POST /webhook/whatsapp/``.
Cobre Twilio (fluxo síncrono com TwiML) e uazapi (fluxo assíncrono com /send/text).
"""

import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

from django.test import Client, TestCase, override_settings

from agents.models import AIAgent
from organizations.models import Organization
from webhook.models import WhatsAppConfig


@override_settings(DEBUG=True)
@patch.dict("os.environ", {"WHATSAPP_PROVIDER": "twilio", "TWILIO_AUTH_TOKEN": ""})
class TwilioWebhookTests(TestCase):
    """Testes do fluxo Twilio (TwiML síncrono)."""

    def setUp(self):
        self.client = Client()
        self.org = Organization.objects.create(
            name="Org Teste", slug="org-teste", is_active=True
        )
        self.agent = AIAgent.objects.create(
            organization=self.org,
            name="Agente Teste",
            system_prompt="Assistente de testes.",
            model_name="test-model",
        )
        self.config = WhatsAppConfig.objects.create(
            organization=self.org,
            agent=self.agent,
            phone_number="whatsapp:+14155238886",
            is_active=True,
        )
        self.valid_payload = {
            "From": "whatsapp:+5551999990000",
            "To": "whatsapp:+14155238886",
            "Body": "Olá, tudo bem?",
        }

    @patch("webhook.views.ChatService")
    def test_valid_message_returns_twiml(self, mock_chat_cls):
        mock_service = MagicMock()
        mock_service.generate_response.return_value = "Oi! Como posso ajudar?"
        mock_chat_cls.return_value = mock_service

        response = self.client.post("/webhook/whatsapp/", data=self.valid_payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/xml")
        self.assertIn(b"Como posso ajudar", response.content)

    @patch.dict("os.environ", {"TWILIO_AUTH_TOKEN": "bad-token"})
    def test_invalid_signature_returns_403(self):
        # Com token presente e sem assinatura válida, retorna 403
        response = self.client.post("/webhook/whatsapp/", data=self.valid_payload)
        self.assertEqual(response.status_code, 403)

    def test_empty_body_returns_204(self):
        payload = {**self.valid_payload, "Body": ""}
        response = self.client.post("/webhook/whatsapp/", data=payload)
        self.assertEqual(response.status_code, 204)

    def test_unknown_number_returns_unavailable_message(self):
        payload = {**self.valid_payload, "To": "whatsapp:+10000000000"}
        response = self.client.post("/webhook/whatsapp/", data=payload)

        self.assertEqual(response.status_code, 200)
        self.assertIn("não está disponível".encode(), response.content)

    @patch("webhook.views.ChatService")
    def test_llm_error_returns_fallback_message(self, mock_chat_cls):
        mock_service = MagicMock()
        mock_service.generate_response.side_effect = Exception("LLM down")
        mock_chat_cls.return_value = mock_service

        response = self.client.post("/webhook/whatsapp/", data=self.valid_payload)

        self.assertEqual(response.status_code, 200)
        self.assertIn("erro ao processar".encode(), response.content)


@override_settings(DEBUG=True)
@patch.dict(
    "os.environ",
    {"WHATSAPP_PROVIDER": "uazapi", "UAZAPI_WEBHOOK_HMAC_SECRET": ""},
)
class UazapiWebhookTests(TestCase):
    """Testes do fluxo uazapi (webhook JSON + envio via /send/text)."""

    def setUp(self):
        self.client = Client()
        self.org = Organization.objects.create(
            name="Org UazApi", slug="org-uazapi", is_active=True
        )
        self.agent = AIAgent.objects.create(
            organization=self.org,
            name="Agente UazApi",
            system_prompt="Assistente de testes.",
            model_name="test-model",
        )
        self.config = WhatsAppConfig.objects.create(
            organization=self.org,
            agent=self.agent,
            phone_number="5551999990001",
            uazapi_instance_id="inst-abc-123",
            uazapi_instance_token="tok-xyz",
            is_active=True,
        )
        self.payload = {
            "event": "messages.upsert",
            "instance": "inst-abc-123",
            "data": {
                "key": {
                    "id": "msg-1",
                    "fromMe": False,
                    "remoteJid": "5551988887777@s.whatsapp.net",
                },
                "message": {"conversation": "Oi, tudo bem?"},
                "messageType": "conversation",
                "pushName": "Fulano",
            },
        }

    def _post(self, body: dict) -> "HttpResponse":
        return self.client.post(
            "/webhook/whatsapp/",
            data=json.dumps(body),
            content_type="application/json",
        )

    @patch("webhook.providers.uazapi_client.send_text")
    @patch("webhook.views.ChatService")
    def test_valid_message_sends_reply_via_uazapi(self, mock_chat_cls, mock_send):
        mock_service = MagicMock()
        mock_service.generate_response.return_value = "Oi Fulano!"
        mock_chat_cls.return_value = mock_service
        mock_send.return_value = {"status": "ok"}

        response = self._post(self.payload)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")  # uazapi: resposta vazia
        mock_send.assert_called_once_with(
            token="tok-xyz", number="5551988887777", text="Oi Fulano!"
        )

    def test_message_from_me_is_ignored(self):
        payload = json.loads(json.dumps(self.payload))
        payload["data"]["key"]["fromMe"] = True
        response = self._post(payload)
        self.assertEqual(response.status_code, 204)

    def test_unknown_instance_returns_200_empty(self):
        payload = json.loads(json.dumps(self.payload))
        payload["instance"] = "instancia-desconhecida"
        response = self._post(payload)
        # uazapi não tem como responder ao usuário final sem tenant → 200 vazio
        self.assertEqual(response.status_code, 200)

    @patch.dict("os.environ", {"UAZAPI_WEBHOOK_HMAC_SECRET": "supersecret"})
    def test_valid_hmac_signature_accepted(self):
        body = json.dumps(self.payload).encode("utf-8")
        sig = hmac.new(b"supersecret", body, hashlib.sha256).hexdigest()
        with patch("webhook.providers.uazapi_client.send_text") as mock_send, patch(
            "webhook.views.ChatService"
        ) as mock_chat_cls:
            mock_service = MagicMock()
            mock_service.generate_response.return_value = "ok"
            mock_chat_cls.return_value = mock_service
            mock_send.return_value = {}

            response = self.client.post(
                "/webhook/whatsapp/",
                data=body,
                content_type="application/json",
                HTTP_X_HMAC_SIGNATURE=sig,
            )

        self.assertEqual(response.status_code, 200)

    @patch.dict("os.environ", {"UAZAPI_WEBHOOK_HMAC_SECRET": "supersecret"})
    def test_invalid_hmac_signature_rejected(self):
        response = self.client.post(
            "/webhook/whatsapp/",
            data=json.dumps(self.payload),
            content_type="application/json",
            HTTP_X_HMAC_SIGNATURE="deadbeef",
        )
        self.assertEqual(response.status_code, 403)

    def test_extended_text_message_is_parsed(self):
        payload = json.loads(json.dumps(self.payload))
        payload["data"]["message"] = {"extendedTextMessage": {"text": "mensagem estendida"}}
        with patch("webhook.providers.uazapi_client.send_text") as mock_send, patch(
            "webhook.views.ChatService"
        ) as mock_chat_cls:
            mock_service = MagicMock()
            mock_service.generate_response.return_value = "resposta"
            mock_chat_cls.return_value = mock_service
            mock_send.return_value = {}

            self._post(payload)

        # Verifica que o ChatService foi chamado com o texto estendido
        mock_service.generate_response.assert_called_once()
        call = mock_service.generate_response.call_args
        self.assertEqual(call.kwargs["user_message"], "mensagem estendida")
