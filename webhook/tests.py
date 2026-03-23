"""
webhook/tests.py

Testes de integração para o endpoint do webhook Twilio WhatsApp.
"""

from unittest.mock import MagicMock, patch

from django.test import TestCase, Client, override_settings

from agents.models import AIAgent
from organizations.models import Organization
from webhook.models import WhatsAppConfig


class TwilioWebhookTests(TestCase):
    """Testes para POST /webhook/whatsapp/."""

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
            twilio_phone_number="whatsapp:+14155238886",
            is_active=True,
        )
        self.valid_payload = {
            "From": "whatsapp:+5551999990000",
            "To": "whatsapp:+14155238886",
            "Body": "Olá, tudo bem?",
        }

    @patch("webhook.views.ChatService")
    @patch("webhook.views._validate_twilio_signature", return_value=True)
    def test_valid_message_returns_twiml(self, mock_sig, mock_chat_cls):
        mock_service = MagicMock()
        mock_service.generate_response.return_value = "Oi! Como posso ajudar?"
        mock_chat_cls.return_value = mock_service

        response = self.client.post(
            "/webhook/whatsapp/",
            data=self.valid_payload,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/xml")
        self.assertIn(b"Como posso ajudar", response.content)

    @patch("webhook.views._validate_twilio_signature", return_value=False)
    def test_invalid_signature_returns_403(self, mock_sig):
        response = self.client.post(
            "/webhook/whatsapp/",
            data=self.valid_payload,
        )
        self.assertEqual(response.status_code, 403)

    @patch("webhook.views._validate_twilio_signature", return_value=True)
    def test_empty_body_returns_204(self, mock_sig):
        payload = {**self.valid_payload, "Body": ""}
        response = self.client.post("/webhook/whatsapp/", data=payload)
        self.assertEqual(response.status_code, 204)

    @patch("webhook.views._validate_twilio_signature", return_value=True)
    def test_unknown_number_returns_unavailable_message(self, mock_sig):
        payload = {**self.valid_payload, "To": "whatsapp:+10000000000"}
        response = self.client.post("/webhook/whatsapp/", data=payload)

        self.assertEqual(response.status_code, 200)
        self.assertIn("não está disponível".encode(), response.content)

    @patch("webhook.views.ChatService")
    @patch("webhook.views._validate_twilio_signature", return_value=True)
    def test_llm_error_returns_fallback_message(self, mock_sig, mock_chat_cls):
        mock_service = MagicMock()
        mock_service.generate_response.side_effect = Exception("LLM down")
        mock_chat_cls.return_value = mock_service

        response = self.client.post(
            "/webhook/whatsapp/",
            data=self.valid_payload,
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("erro ao processar".encode(), response.content)
