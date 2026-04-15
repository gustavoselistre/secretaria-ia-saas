"""
knowledge/management/commands/ingest_document.py

Ingere um documento na base de conhecimento de uma organização.

Uso:
  python manage.py ingest_document --org minha-empresa --title "Catálogo" --file ./catalogo.pdf
  python manage.py ingest_document --org minha-empresa --title "FAQ" --url https://site.com/faq
  python manage.py ingest_document --org minha-empresa --title "Horários" --text "Seg a sex 8h-18h"

Formatos de arquivo aceitos: .txt, .md, .pdf, .docx
"""

from __future__ import annotations

import os
import uuid as _uuid

from django.core.management.base import BaseCommand, CommandError

from knowledge.services import KnowledgeService
from organizations.models import Organization


class Command(BaseCommand):
    help = "Ingere um documento (arquivo, URL ou texto) na base de conhecimento (RAG)."

    def add_arguments(self, parser):
        parser.add_argument("--org", required=True, help="Slug ou UUID da Organization.")
        parser.add_argument(
            "--title",
            required=False,
            help="Título do documento (opcional: usa nome do arquivo / título da página).",
        )

        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument("--file", help="Caminho para arquivo .txt, .md, .pdf ou .docx.")
        group.add_argument("--text", help="Texto inline para ingerir.")
        group.add_argument("--url", help="URL pública — baixa e extrai texto limpo.")

    def handle(self, *args, **options):
        org = self._resolve_organization(options["org"])
        title = options.get("title")
        service = KnowledgeService()

        if options.get("file"):
            path = options["file"]
            if not os.path.exists(path):
                raise CommandError(f"Arquivo não encontrado: {path}")
            self.stdout.write(f"Ingerindo '{path}' para {org.name}...")
            try:
                with open(path, "rb") as fh:
                    kb = service.ingest_file(
                        organization=org,
                        file_obj=fh,
                        filename=os.path.basename(path),
                        title=title,
                    )
            except ValueError as exc:
                raise CommandError(str(exc))

        elif options.get("url"):
            url = options["url"]
            self.stdout.write(f"Baixando '{url}' para {org.name}...")
            try:
                kb = service.ingest_url(organization=org, url=url, title=title)
            except ValueError as exc:
                raise CommandError(str(exc))

        else:
            raw_text = options["text"]
            if not raw_text or not raw_text.strip():
                raise CommandError("Texto vazio. Nada para ingerir.")
            if not title:
                raise CommandError("Use --title ao ingerir texto inline com --text.")
            kb = service.ingest_document(
                organization=org, title=title, raw_text=raw_text,
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
