"""
chat/management/commands/test_chat.py

Testa o fluxo completo de conversação: mensagem → RAG → LLM → resposta.

Uso:
  python manage.py test_chat --org technova
  python manage.py test_chat --org technova --message "Posso devolver um produto usado?"
  python manage.py test_chat --org technova --phone 5551999990000
"""

from __future__ import annotations

import uuid as _uuid

from django.core.management.base import BaseCommand, CommandError

from agents.models import AIAgent
from chat.services import ChatService
from organizations.models import Organization


DEFAULT_SYSTEM_PROMPT = (
    "Você é uma secretária virtual educada e eficiente. "
    "Responda de forma clara e objetiva usando apenas as informações do contexto fornecido. "
    "Se não souber a resposta, diga educadamente que não tem essa informação. "
    "Responda sempre em português brasileiro."
)


class Command(BaseCommand):
    help = "Testa o fluxo completo de chat: mensagem → RAG → LLM → resposta."

    def add_arguments(self, parser):
        parser.add_argument(
            "--org",
            type=str,
            required=True,
            help="Slug ou UUID da Organization.",
        )
        parser.add_argument(
            "--message",
            type=str,
            default="Qual é o prazo para devolver um produto?",
            help="Mensagem de teste.",
        )
        parser.add_argument(
            "--phone",
            type=str,
            default="5551999990000",
            help="Número de telefone simulado.",
        )

    def handle(self, *args, **options):
        org = self._resolve_organization(options["org"])
        user_message = options["message"]
        phone = options["phone"]

        self.stdout.write(self.style.HTTP_INFO(f"\n{'=' * 60}"))
        self.stdout.write(self.style.HTTP_INFO(f"  Organization: {org.name} ({org.slug})"))
        self.stdout.write(self.style.HTTP_INFO(f"{'=' * 60}\n"))

        # Garante que existe um AIAgent para teste
        agent = self._get_or_create_agent(org)

        self.stdout.write(self.style.WARNING(f"▶ Agente: {agent.name} ({agent.model_name})"))
        self.stdout.write(self.style.WARNING(f"▶ Telefone: {phone}"))
        self.stdout.write(self.style.WARNING(f'▶ Mensagem: "{user_message}"\n'))

        # Fluxo completo
        self.stdout.write(self.style.WARNING("▶ Gerando resposta…"))
        try:
            service = ChatService()
            response = service.generate_response(
                agent=agent,
                customer_phone=phone,
                user_message=user_message,
            )
        except Exception as exc:
            raise CommandError(f"Erro ao gerar resposta: {exc}") from exc

        self.stdout.write(self.style.SUCCESS("\n  ✔ Resposta da Secretária:\n"))
        self.stdout.write(f"  {response}\n")

        # Mostra histórico
        conversation = ChatService.get_or_create_conversation(agent, phone)
        messages = conversation.messages.all()
        self.stdout.write(self.style.WARNING(f"\n▶ Histórico da conversa ({messages.count()} mensagens):"))
        for msg in messages:
            prefix = "👤" if msg.role == "user" else "🤖"
            self.stdout.write(f"  {prefix} [{msg.role}] {msg.content[:120]}")

        self.stdout.write(self.style.SUCCESS("\n✔ Fluxo de chat validado com sucesso!"))

    def _resolve_organization(self, identifier: str) -> Organization:
        try:
            uid = _uuid.UUID(identifier)
            return Organization.objects.get(id=uid)
        except (ValueError, Organization.DoesNotExist):
            pass
        try:
            return Organization.objects.get(slug=identifier)
        except Organization.DoesNotExist:
            raise CommandError(
                f"Organization '{identifier}' não encontrada. "
                "Informe um slug ou UUID válido."
            )

    def _get_or_create_agent(self, org: Organization) -> AIAgent:
        """Busca o primeiro agente da org ou cria um de teste."""
        agent = AIAgent.objects.filter(organization=org).first()
        if agent:
            return agent

        self.stdout.write(self.style.NOTICE(
            "  Nenhum agente encontrado — criando agente de teste…"
        ))
        agent = AIAgent.objects.create(
            organization=org,
            name=f"Secretária {org.name}",
            system_prompt=DEFAULT_SYSTEM_PROMPT,
            model_name="gemini-2.5-flash",
            temperature=0.7,
        )
        self.stdout.write(self.style.SUCCESS(f"  ✔ Agente criado: {agent.name}"))
        return agent
