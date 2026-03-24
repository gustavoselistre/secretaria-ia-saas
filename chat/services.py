"""
chat/services.py

Orquestra o fluxo de conversação: recebe mensagem do cliente, busca contexto RAG,
monta prompt com histórico e gera resposta via LLM (Gemini / OpenAI).
Suporta function calling (tool use) com loop de execução de tools.
"""

from __future__ import annotations

import json
import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from agents.models import AIAgent
from chat.models import Conversation, Message
from knowledge.services import KnowledgeService

logger = logging.getLogger(__name__)

MAX_HISTORY_MESSAGES = 20
MAX_TOOL_ROUNDS = 5


# ---------------------------------------------------------------------------
# LLM Response types
# ---------------------------------------------------------------------------


@dataclass
class ToolCall:
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_parts: list = field(default_factory=list)  # provider-specific parts for multi-turn


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

    def generate_with_tools(
        self,
        model_name: str,
        temperature: float,
        messages: list[dict[str, str]],
        tools=None,
    ) -> LLMResponse:
        """Gera resposta com suporte a tools. Default: fallback para generate()."""
        text = self.generate(model_name, temperature, messages)
        return LLMResponse(text=text)

    def build_tool_round_messages(
        self,
        llm_response: LLMResponse,
        tool_results: list[dict],
    ) -> list[dict]:
        """Monta mensagens de retorno das tools para a próxima rodada. Provider-specific."""
        return []

    def transcribe_audio(self, audio_bytes: bytes, content_type: str) -> str:
        """Transcreve áudio para texto. Provider-specific."""
        raise NotImplementedError("Este provider não suporta transcrição de áudio.")


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

    def _build_gemini_contents(self, messages: list[dict]):
        """Converte mensagens para Gemini Content objects.

        Suporta mensagens normais (OpenAI-format) e mensagens de tool round
        que já contêm _gemini_parts.
        """
        from google.genai import types

        system_instruction = None
        contents: list[types.Content] = []

        for msg in messages:
            if msg["role"] == "system":
                system_instruction = msg.get("content", "")
            elif "_gemini_parts" in msg:
                # Mensagens de tool round (model function_call ou function_response)
                role = "user" if msg["role"] == "function_response" else "model"
                contents.append(
                    types.Content(role=role, parts=msg["_gemini_parts"])
                )
            else:
                role = "user" if msg["role"] == "user" else "model"
                contents.append(
                    types.Content(
                        role=role,
                        parts=[types.Part.from_text(text=msg.get("content", ""))],
                    )
                )

        return system_instruction, contents

    def generate(
        self,
        model_name: str,
        temperature: float,
        messages: list[dict[str, str]],
    ) -> str:
        from google.genai import types

        system_instruction, contents = self._build_gemini_contents(messages)

        try:
            response = self._client.models.generate_content(
                model=model_name,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=temperature,
                ),
            )
            return response.text
        except Exception as exc:
            logger.error("Erro ao gerar resposta via Gemini: %s", exc)
            raise

    def generate_with_tools(
        self,
        model_name: str,
        temperature: float,
        messages: list[dict[str, str]],
        tools=None,
    ) -> LLMResponse:
        from google.genai import types

        system_instruction, contents = self._build_gemini_contents(messages)

        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=temperature,
        )
        if tools:
            config.tools = tools

        try:
            response = self._client.models.generate_content(
                model=model_name,
                contents=contents,
                config=config,
            )
        except Exception as exc:
            logger.error("Erro ao gerar resposta via Gemini (tools): %s", exc)
            raise

        # Parse response parts
        tool_calls = []
        text_parts = []
        raw_parts = []

        if response.candidates and response.candidates[0].content:
            raw_parts = list(response.candidates[0].content.parts or [])
            for part in raw_parts:
                if part.function_call:
                    tool_calls.append(
                        ToolCall(
                            name=part.function_call.name,
                            arguments=dict(part.function_call.args) if part.function_call.args else {},
                        )
                    )
                elif part.text:
                    text_parts.append(part.text)

        return LLMResponse(
            text=" ".join(text_parts) if text_parts else None,
            tool_calls=tool_calls,
            raw_parts=raw_parts,
        )

    def build_tool_round_messages(
        self,
        llm_response: LLMResponse,
        tool_results: list[dict],
    ) -> list[dict]:
        """Retorna mensagens Gemini-format para continuar a conversa após tool calls."""
        from google.genai import types

        # Model's function call response
        model_msg = {"role": "model", "_gemini_parts": llm_response.raw_parts}

        # Function results
        function_response_parts = []
        for result in tool_results:
            function_response_parts.append(
                types.Part.from_function_response(
                    name=result["name"],
                    response=result["result"],
                )
            )
        user_msg = {"role": "function_response", "_gemini_parts": function_response_parts}

        return [model_msg, user_msg]

    def transcribe_audio(self, audio_bytes: bytes, content_type: str) -> str:
        from google.genai import types

        try:
            response = self._client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_bytes(data=audio_bytes, mime_type=content_type),
                            types.Part.from_text(
                                text="Transcreva este áudio em português. "
                                     "Retorne apenas o texto falado, sem explicações."
                            ),
                        ],
                    ),
                ],
            )
            return response.text.strip()
        except Exception as exc:
            logger.error("Erro ao transcrever áudio via Gemini: %s", exc)
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

    def generate_with_tools(
        self,
        model_name: str,
        temperature: float,
        messages: list[dict[str, str]],
        tools=None,
    ) -> LLMResponse:
        try:
            kwargs = {
                "model": model_name,
                "temperature": temperature,
                "messages": messages,
            }
            if tools:
                kwargs["tools"] = tools
            response = self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            logger.error("Erro ao gerar resposta via OpenAI (tools): %s", exc)
            raise

        choice = response.choices[0]
        tool_calls = []

        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                tool_calls.append(
                    ToolCall(
                        name=tc.function.name,
                        arguments=json.loads(tc.function.arguments),
                    )
                )

        return LLMResponse(
            text=choice.message.content,
            tool_calls=tool_calls,
        )


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
    """Orquestra conversação: histórico + RAG + LLM + tools."""

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
        """Monta a lista de mensagens para o LLM."""
        agent = conversation.agent

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

        # Histórico recente
        history = conversation.messages.order_by("-timestamp")[: MAX_HISTORY_MESSAGES]
        for msg in reversed(history):
            messages.append({"role": msg.role, "content": msg.content})

        # Mensagem atual
        messages.append({"role": "user", "content": user_message})

        return messages

    def _get_tools_for_provider(self):
        """Retorna as tools no formato correto para o provider atual."""
        # Importa executors para garantir que as tools estão registradas
        import tools.executors  # noqa: F401

        if isinstance(self._llm, GeminiLLMProvider):
            from tools.definitions import get_gemini_tools
            return get_gemini_tools()
        elif isinstance(self._llm, OpenAILLMProvider):
            from tools.definitions import get_openai_tools
            return get_openai_tools()
        return None

    def _execute_tool(self, organization, tool_name: str, arguments: dict) -> dict:
        """Executa uma tool do registry com isolamento por organization."""
        from tools.registry import get_tool

        try:
            tool_cls = get_tool(tool_name)
            tool_instance = tool_cls()
            result = tool_instance.execute(organization=organization, **arguments)
            logger.info(
                "Tool '%s' executada para org=%s: %s",
                tool_name, organization.slug, result,
            )
            return result
        except KeyError:
            logger.error("Tool '%s' não encontrada no registry.", tool_name)
            return {"error": f"Tool '{tool_name}' não disponível."}
        except Exception as exc:
            logger.error("Erro ao executar tool '%s': %s", tool_name, exc)
            return {"error": f"Erro ao executar '{tool_name}': {str(exc)}"}

    def _append_tool_round(self, messages, llm_response, tool_results):
        """Adiciona resultados de tools às mensagens para a próxima rodada."""
        if isinstance(self._llm, GeminiLLMProvider):
            # Para Gemini, usamos o build_tool_round_messages que retorna
            # mensagens com _gemini_parts (tratadas no _build_gemini_contents_with_tools)
            round_msgs = self._llm.build_tool_round_messages(llm_response, tool_results)
            messages.extend(round_msgs)
        else:
            # OpenAI format: assistant message com tool_calls + tool messages
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"call_{i}",
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for i, tc in enumerate(llm_response.tool_calls)
                ],
            })
            for i, result in enumerate(tool_results):
                messages.append({
                    "role": "tool",
                    "tool_call_id": f"call_{i}",
                    "content": json.dumps(result["result"]),
                })

        return messages

    def generate_response(
        self,
        agent: AIAgent,
        customer_phone: str,
        user_message: str,
    ) -> str:
        """Fluxo completo: salva mensagem → RAG → LLM (com tool loop) → salva resposta."""

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

        # 5. Pega tools
        tools = self._get_tools_for_provider()

        # 6. Tool-calling loop
        llm_response = LLMResponse(text="")
        for round_num in range(MAX_TOOL_ROUNDS):
            llm_response = self._llm.generate_with_tools(
                model_name=agent.model_name,
                temperature=agent.temperature,
                messages=messages,
                tools=tools,
            )

            if not llm_response.tool_calls:
                break

            # Executar tools
            tool_results = []
            for tc in llm_response.tool_calls:
                logger.info("Tool call [round %d]: %s(%s)", round_num + 1, tc.name, tc.arguments)
                result = self._execute_tool(organization, tc.name, tc.arguments)
                tool_results.append({"name": tc.name, "result": result})

            # Adicionar resultados às mensagens para próxima rodada
            messages = self._append_tool_round(messages, llm_response, tool_results)

        assistant_text = llm_response.text or "Desculpe, não consegui processar sua solicitação."

        # 7. Salva resposta
        Message.objects.create(
            conversation=conversation,
            role=Message.Role.ASSISTANT,
            content=assistant_text,
        )

        return assistant_text
