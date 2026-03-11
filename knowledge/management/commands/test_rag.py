"""
knowledge/management/commands/test_rag.py

Management command para validar o pipeline RAG de ponta a ponta:
  1. Ingere um documento simulado (política de devolução).
  2. Faz uma busca vetorial com uma pergunta de teste.
  3. Imprime os chunks mais relevantes no terminal.

Uso:
  python manage.py test_rag --org minha-loja
  python manage.py test_rag --org <uuid>
"""

from __future__ import annotations

import uuid as _uuid

from django.core.management.base import BaseCommand, CommandError

from knowledge.services import KnowledgeService
from organizations.models import Organization


SAMPLE_DOCUMENT = (
    "Política de Devolução da Loja TechNova. "
    "O cliente pode solicitar a devolução de qualquer produto em até 30 dias "
    "corridos após a data de entrega, desde que o item esteja em sua embalagem "
    "original e sem sinais de uso. Para iniciar o processo, basta acessar a área "
    "'Meus Pedidos' no site ou entrar em contato pelo WhatsApp (51) 99999-0000. "
    "Após a aprovação da devolução, a TechNova enviará um código de postagem "
    "gratuito pelos Correios. O reembolso será processado em até 10 dias úteis "
    "após o recebimento do produto em nosso centro de distribuição, utilizando "
    "o mesmo método de pagamento da compra original. "
    "Produtos da categoria 'Eletrônicos' possuem garantia estendida de 12 meses "
    "diretamente com o fabricante, independente da política de devolução da loja. "
    "Itens em promoção do tipo 'Liquidação Final' não são elegíveis para "
    "devolução, exceto em caso de defeito de fabricação comprovado. "
    "Para trocas por tamanho ou cor, o prazo é de 15 dias e o frete é por conta "
    "do cliente, salvo erro no envio por parte da TechNova. "
    "Dúvidas frequentes: Posso devolver um produto usado? Não, apenas produtos "
    "sem sinais de uso são aceitos. Posso trocar um presente? Sim, desde que "
    "apresente o comprovante de compra ou o código do pedido."
)

SAMPLE_QUERY = "Qual é o prazo para devolver um produto?"


class Command(BaseCommand):
    help = "Testa o pipeline RAG: ingere documento simulado e faz busca vetorial."

    def add_arguments(self, parser):
        parser.add_argument(
            "--org",
            type=str,
            required=True,
            help="Slug ou UUID da Organization.",
        )
        parser.add_argument(
            "--query",
            type=str,
            default=SAMPLE_QUERY,
            help="Pergunta de teste (default: prazo de devolução).",
        )
        parser.add_argument(
            "--top-k",
            type=int,
            default=3,
            help="Número de chunks a retornar (default: 3).",
        )

    def handle(self, *args, **options):
        org = self._resolve_organization(options["org"])
        query = options["query"]
        top_k = options["top_k"]

        self.stdout.write(self.style.HTTP_INFO(f"\n{'=' * 60}"))
        self.stdout.write(self.style.HTTP_INFO(f"  Organization: {org.name} ({org.slug})"))
        self.stdout.write(self.style.HTTP_INFO(f"{'=' * 60}\n"))

        # --- Ingestão -------------------------------------------------------
        self.stdout.write(self.style.WARNING("▶ Ingerindo documento de teste…"))
        try:
            service = KnowledgeService()
            kb = service.ingest_document(
                organization=org,
                title="Política de Devolução — TechNova",
                raw_text=SAMPLE_DOCUMENT,
            )
        except Exception as exc:
            raise CommandError(f"Erro na ingestão: {exc}") from exc

        chunks_count = kb.chunks.count()
        self.stdout.write(
            self.style.SUCCESS(
                f"  ✔ KnowledgeBase criada: {kb.id}\n"
                f"  ✔ Chunks gerados: {chunks_count}"
            )
        )

        # Mostra os chunks criados
        self.stdout.write(self.style.WARNING("\n▶ Chunks criados:"))
        for i, chunk in enumerate(kb.chunks.all(), 1):
            preview = chunk.content[:100].replace("\n", " ")
            self.stdout.write(f"  [{i}] {preview}…")

        # --- Busca vetorial --------------------------------------------------
        self.stdout.write(self.style.WARNING(f'\n▶ Busca vetorial: "{query}"'))
        try:
            results = service.find_relevant_context(
                organization=org,
                query_text=query,
                top_k=top_k,
            )
        except Exception as exc:
            raise CommandError(f"Erro na busca: {exc}") from exc

        if not results:
            self.stdout.write(self.style.ERROR("  Nenhum resultado encontrado."))
            return

        self.stdout.write(self.style.SUCCESS(f"  ✔ {len(results)} chunk(s) retornado(s):\n"))
        for i, chunk in enumerate(results, 1):
            distance = getattr(chunk, "distance", "N/A")
            similarity = f"{1 - float(distance):.4f}" if distance != "N/A" else "N/A"
            self.stdout.write(self.style.HTTP_INFO(f"  ── Resultado {i} (similaridade: {similarity}) ──"))
            self.stdout.write(f"  {chunk.content}\n")

        self.stdout.write(self.style.SUCCESS("✔ Pipeline RAG validado com sucesso!"))

    def _resolve_organization(self, identifier: str) -> Organization:
        """Busca Organization por slug ou UUID."""
        # Tenta como UUID
        try:
            uid = _uuid.UUID(identifier)
            return Organization.objects.get(id=uid)
        except (ValueError, Organization.DoesNotExist):
            pass

        # Tenta como slug
        try:
            return Organization.objects.get(slug=identifier)
        except Organization.DoesNotExist:
            raise CommandError(
                f"Organization '{identifier}' não encontrada. "
                "Informe um slug ou UUID válido."
            )
