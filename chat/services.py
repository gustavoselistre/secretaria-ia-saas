"""
chat/services.py

Orquestra o fluxo de conversação: recebe mensagem do cliente, busca contexto RAG,
monta prompt com histórico e gera resposta via LLM (Gemini / OpenAI).
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod

from django.db import transaction

from agents.models import AIAgent
from chat.models import Conversation, Message
from knowledge.services import KnowledgeService

logger = logging.getLogger(__name__)

MAX_HISTORY_MESSAGES = 20


# ---------------------------------------------------------------------------
# LLM Provider — Adapter Pattern
# ---------------------------------------------------------------------------


class LLMProvider(ABC):
    """Interface abstrata para provedores de LLM."""

    @abstractmethod
    def generate(
        self,
        model_name: str,
        temperature: float,
        messages: list[dict[str, str]],
    ) -> str:
        """Gera uma resposta a partir da lista de mensagens."""


class GeminiLLMProvider(LLMProvider):
    """Adapter para Google Gemini via google-genai."""

    def __init__(self) -> None:
        from google import genai

        api_key = os.environ.get("GOOGLE_API_KEY")
        if api_key:
            self._client = genai.Client(api_key=api_key)
        else:
            project = os.environ.get("GOOGLE_CLOUD_PROJECT")
            location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
            if not project:
                raise EnvironmentError(
                    "Defina GOOGLE_API_KEY (AI Studio) ou "
                    "GOOGLE_APPLICATION_CREDENTIALS + GOOGLE_CLOUD_PROJECT (Vertex AI)."
                )
            self._client = genai.Client(
                vertexai=True, project=project, location=location,
            )

    def generate(
        self,
        model_name: str,
        temperature: float,
        messages: list[dict[str, str]],
    ) -> str:
        from google.genai import types

        # Separa system instruction das mensagens de conversa
        system_instruction = None
        conversation_messages: list[types.Content] = []

        for msg in messages:
            if msg["role"] == "system":
                system_instruction = msg["content"]
            else:
                role = "user" if msg["role"] == "user" else "model"
                conversation_messages.append(
                    types.Content(
                        role=role,
                        parts=[types.Part.from_text(text=msg["content"])],
                    )
                )

        try:
            response = self._client.models.generate_content(
                model=model_name,
                contents=conversation_messages,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=temperature,
                ),
            )
            return response.text
        except Exception as exc:
            logger.error("Erro ao gerar resposta via Gemini: %s", exc)
            raise


class OpenAILLMProvider(LLMProvider):
    """Adapter para OpenAI GPT."""

    def __init__(self) -> None:
        from openai import OpenAI

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "A variável de ambiente OPENAI_API_KEY não está definida."
            )
        self._client = OpenAI(api_key=api_key)

    def generate(
        self,
        model_name: str,
        temperature: float,
        messages: list[dict[str, str]],
    ) -> str:
        try:
            response = self._client.chat.completions.create(
                model=model_name,
                temperature=temperature,
                messages=messages,
            )
            return response.choices[0].message.content
        except Exception as exc:
            logger.error("Erro ao gerar resposta via OpenAI: %s", exc)
            raise


def get_llm_provider() -> LLMProvider:
    """Factory — retorna o LLM provider configurado em AI_PROVIDER."""
    provider_name = os.environ.get("AI_PROVIDER", "openai").lower().strip()

    providers: dict[str, type[LLMProvider]] = {
        "openai": OpenAILLMProvider,
        "google": GeminiLLMProvider,
        "vertexai": GeminiLLMProvider,
    }

    provider_cls = providers.get(provider_name)
    if provider_cls is None:
        raise ValueError(
            f"AI_PROVIDER '{provider_name}' inválido. "
            f"Opções: {', '.join(providers.keys())}"
        )

    return provider_cls()


# ---------------------------------------------------------------------------
# Chat Service
# ---------------------------------------------------------------------------


class ChatService:
    """Orquestra conversação: histórico + RAG + LLM."""

    def __init__(
        self,
        llm_provider: LLMProvider | None = None,
        knowledge_service: KnowledgeService | None = None,
    ) -> None:
        self._llm = llm_provider or get_llm_provider()
        self._knowledge = knowledge_service or KnowledgeService()

    @staticmethod
    def get_or_create_conversation(
        agent: AIAgent,
        customer_phone: str,
    ) -> Conversation:
        """Busca conversa ativa ou cria uma nova."""
        conversation, _ = Conversation.objects.get_or_create(
            agent=agent,
            customer_phone=customer_phone,
        )
        return conversation

    def _build_messages(
        self,
        conversation: Conversation,
        user_message: str,
        rag_context: str,
    ) -> list[dict[str, str]]:
        """Monta a lista de mensagens para o LLM.

        Estrutura:
          1. System prompt (instruções do agente + contexto RAG)
          2. Últimas N mensagens do histórico
          3. Mensagem atual do usuário
        """
        agent = conversation.agent

        # System prompt com contexto RAG injetado
        system_content = agent.system_prompt
        if rag_context:
            system_content += (
                "\n\n---\n"
                "Use o contexto abaixo para responder. "
                "Se a informação não estiver no contexto, diga que não tem essa informação.\n\n"
                f"{rag_context}"
            )

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_content},
        ]

        # Histórico recente (exclui a mensagem que acabamos de salvar)
        history = conversation.messages.order_by("-timestamp")[: MAX_HISTORY_MESSAGES]
        for msg in reversed(history):
            messages.append({"role": msg.role, "content": msg.content})

        # Mensagem atual
        messages.append({"role": "user", "content": user_message})

        return messages

    @transaction.atomic
    def generate_response(
        self,
        agent: AIAgent,
        customer_phone: str,
        user_message: str,
    ) -> str:
        """Fluxo completo: salva mensagem → RAG → LLM → salva resposta."""

        # 1. Conversa
        conversation = self.get_or_create_conversation(agent, customer_phone)

        # 2. Salva mensagem do usuário
        Message.objects.create(
            conversation=conversation,
            role=Message.Role.USER,
            content=user_message,
        )

        # 3. Busca contexto RAG
        organization = agent.organization
        rag_chunks = self._knowledge.find_relevant_context(
            organization=organization,
            query_text=user_message,
        )
        rag_context = "\n\n".join(chunk.content for chunk in rag_chunks)

        # 4. Monta mensagens
        messages = self._build_messages(conversation, user_message, rag_context)

        # 5. Gera resposta via LLM
        assistant_text = self._llm.generate(
            model_name=agent.model_name,
            temperature=agent.temperature,
            messages=messages,
        )

        # 6. Salva resposta
        Message.objects.create(
            conversation=conversation,
            role=Message.Role.ASSISTANT,
            content=assistant_text,
        )

        return assistant_text
