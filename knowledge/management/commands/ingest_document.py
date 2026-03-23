"""
knowledge/management/commands/ingest_document.py

Ingere um documento de texto na base de conhecimento de uma organização.

Uso:
  python manage.py ingest_document --org minha-empresa --title "Catálogo" --file ./catalogo.txt
  python manage.py ingest_document --org minha-empresa --title "Horários" --text "Seg a sex 8h-18h"
"""

from __future__ import annotations

import uuid as _uuid

from django.core.management.base import BaseCommand, CommandError

from knowledge.services import KnowledgeService
from organizations.models import Organization


class Command(BaseCommand):
    help = "Ingere um documento de texto na base de conhecimento (RAG)."

    def add_arguments(self, parser):
        parser.add_argument("--org", required=True, help="Slug ou UUID da Organization.")
        parser.add_argument("--title", required=True, help="Título do documento.")

        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--file", help="Caminho para arquivo .txt ou .md.")
        group.add_argument("--text", help="Texto inline para ingerir.")

    def handle(self, *args, **options):
        org = self._resolve_organization(options["org"])

        if options["file"]:
            try:
                with open(options["file"], encoding="utf-8") as f:
                    raw_text = f.read()
            except FileNotFoundError:
                raise CommandError(f"Arquivo não encontrado: {options['file']}")
        else:
            raw_text = options["text"]

        if not raw_text.strip():
            raise CommandError("Texto vazio. Nada para ingerir.")

        self.stdout.write(f"Ingerindo '{options['title']}' para {org.name}...")

        service = KnowledgeService()
        kb = service.ingest_document(
            organization=org,
            title=options["title"],
            raw_text=raw_text,
        )

        chunks_count = kb.chunks.count()
        self.stdout.write(self.style.SUCCESS(
            f"OK! KnowledgeBase '{kb.title}' criada com {chunks_count} chunk(s)."
        ))

    def _resolve_organization(self, identifier: str) -> Organization:
        try:
            uid = _uuid.UUID(identifier)
            return Organization.objects.get(id=uid)
        except (ValueError, Organization.DoesNotExist):
            pass
        try:
            return Organization.objects.get(slug=identifier)
        except Organization.DoesNotExist:
            raise CommandError(f"Organization '{identifier}' não encontrada.")
