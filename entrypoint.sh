#!/usr/bin/env bash
set -euo pipefail

echo "Running migrations..."
python manage.py migrate --noinput

echo "Creating initial superuser (if configured)..."
python manage.py create_initial_superuser || true

echo "Starting Gunicorn on port ${PORT:-8080}..."
exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT:-8080}" \
    --workers "${GUNICORN_WORKERS:-2}" \
    --threads "${GUNICORN_THREADS:-4}" \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -
