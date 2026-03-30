"""
chat/tests.py

Testes unitários para o ChatService: criação de conversas, mensagens e build de prompts.
"""

from unittest.mock import MagicMock

from django.test import TestCase

from agents.models import AIAgent
from chat.models import Conversation, Message
from chat.services import ChatService, LLMResponse
from organizations.models import Organization


class ChatServiceTests(TestCase):
    """Testes para ChatService com LLM e embedding mockados."""

    def setUp(self):
        self.org = Organization.objects.create(name="Org Teste", slug="org-teste")
        self.agent = AIAgent.objects.create(
            organization=self.org,
            name="Agente Teste",
            system_prompt="Você é um assistente de testes.",
            model_name="test-model",
            temperature=0.5,
        )

        self.mock_llm = MagicMock()
        self.mock_llm.generate.return_value = "Resposta da IA."
        self.mock_llm.generate_with_tools.return_value = LLMResponse(
            text="Resposta da IA.", tool_calls=[],
        )

        self.mock_knowledge = MagicMock()
        self.mock_knowledge.find_relevant_context.return_value = []

        self.service = ChatService(
            llm_provider=self.mock_llm,
            knowledge_service=self.mock_knowledge,
        )

    def test_generate_response_creates_conversation_and_messages(self):
        response = self.service.generate_response(
            agent=self.agent,
            customer_phone="+5551999990000",
            user_message="Olá, tudo bem?",
        )

        self.assertEqual(response, "Resposta da IA.")

        # Deve ter criado 1 conversation
        conversations = Conversation.objects.filter(agent=self.agent)
        self.assertEqual(conversations.count(), 1)

        # Deve ter criado 2 mensagens: user + assistant
        messages = Message.objects.filter(conversation=conversations.first())
        self.assertEqual(messages.count(), 2)
        self.assertEqual(messages.filter(role=Message.Role.USER).count(), 1)
        self.assertEqual(messages.filter(role=Message.Role.ASSISTANT).count(), 1)

    def test_generate_response_reuses_conversation(self):
        self.service.generate_response(self.agent, "+5551999990000", "Mensagem 1")
        self.service.generate_response(self.agent, "+5551999990000", "Mensagem 2")

        # Deve ter apenas 1 conversation
        self.assertEqual(
            Conversation.objects.filter(agent=self.agent).count(), 1
        )
        # Mas 4 mensagens (2 user + 2 assistant)
        self.assertEqual(
            Message.objects.filter(conversation__agent=self.agent).count(), 4
        )

    def test_build_messages_includes_system_prompt(self):
        conversation = ChatService.get_or_create_conversation(
            self.agent, "+5551999990000"
        )
        messages = self.service._build_messages(
            conversation, "Pergunta", "Contexto RAG"
        )

        # Primeiro deve ser system prompt
        self.assertEqual(messages[0]["role"], "system")
        self.assertIn("assistente de testes", messages[0]["content"])
        self.assertIn("Contexto RAG", messages[0]["content"])

        # Último deve ser a mensagem do usuário
        self.assertEqual(messages[-1]["role"], "user")
        self.assertEqual(messages[-1]["content"], "Pergunta")

    def test_build_messages_without_rag_context(self):
        conversation = ChatService.get_or_create_conversation(
            self.agent, "+5551999990000"
        )
        messages = self.service._build_messages(conversation, "Oi", "")

        # System prompt não deve conter seção de contexto
        self.assertNotIn("Use o contexto abaixo", messages[0]["content"])
