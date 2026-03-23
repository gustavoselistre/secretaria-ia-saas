"""
organizations/management/commands/create_initial_superuser.py

Cria superuser de forma idempotente a partir de variáveis de ambiente.
Útil para deploy automatizado (Cloud Run, Docker).

Uso:
    DJANGO_SUPERUSER_USERNAME=admin \
    DJANGO_SUPERUSER_EMAIL=admin@example.com \
    DJANGO_SUPERUSER_PASSWORD=secret \
    python manage.py create_initial_superuser
"""

from __future__ import annotations

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

User = get_user_model()


class Command(BaseCommand):
    help = "Cria superuser a partir de variáveis de ambiente (idempotente)."

    def handle(self, *args, **options):
        username = os.environ.get("DJANGO_SUPERUSER_USERNAME", "admin")
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "admin@example.com")
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD")

        if not password:
            self.stderr.write(self.style.ERROR(
                "DJANGO_SUPERUSER_PASSWORD não definida. Abortando."
            ))
            return

        if User.objects.filter(username=username).exists():
            self.stdout.write(f"Superuser '{username}' já existe. Nada a fazer.")
            return

        User.objects.create_superuser(
            username=username,
            email=email,
            password=password,
        )
        self.stdout.write(self.style.SUCCESS(f"Superuser '{username}' criado."))
