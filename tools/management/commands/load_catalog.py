"""
tools/management/commands/load_catalog.py

Carrega catálogo de serviços a partir de um arquivo JSON.

Uso:
  python manage.py load_catalog --org minha-empresa --file data/catalogo.json
"""

from __future__ import annotations

import json
import uuid as _uuid

from django.core.management.base import BaseCommand, CommandError

from organizations.models import Organization
from tools.models import ServiceCatalog


class Command(BaseCommand):
    help = "Carrega catálogo de serviços a partir de arquivo JSON."

    def add_arguments(self, parser):
        parser.add_argument("--org", required=True, help="Slug ou UUID da Organization.")
        parser.add_argument("--file", required=True, help="Caminho para arquivo JSON.")
        parser.add_argument(
            "--clear", action="store_true",
            help="Remove todos os serviços existentes antes de carregar.",
        )

    def handle(self, *args, **options):
        org = self._resolve_organization(options["org"])

        try:
            with open(options["file"], encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            raise CommandError(f"Arquivo não encontrado: {options['file']}")
        except json.JSONDecodeError as exc:
            raise CommandError(f"JSON inválido: {exc}")

        if not isinstance(data, list):
            raise CommandError("O JSON deve ser uma lista de objetos.")

        if options["clear"]:
            deleted, _ = ServiceCatalog.objects.filter(organization=org).delete()
            self.stdout.write(f"Removidos {deleted} serviços existentes.")

        created = 0
        for item in data:
            ServiceCatalog.objects.update_or_create(
                organization=org,
                name=item["name"],
                defaults={
                    "category": item.get("category", ""),
                    "description": item.get("description", ""),
                    "price": item["price"],
                    "duration_minutes": item.get("duration_minutes", 60),
                    "is_active": item.get("is_active", True),
                },
            )
            created += 1

        self.stdout.write(self.style.SUCCESS(
            f"OK! {created} serviço(s) carregados para {org.name}."
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
