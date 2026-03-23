# ---------------------------------------------------------------------------
# Secretaria IA SaaS — Cloud Run ready
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings \
    PORT=8080

# System deps: PostgreSQL client (libpq) + build tools for psycopg2
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential \
       libpq-dev \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps (cached layer)
COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy project
COPY . .

# Collect static files at build time (SECRET_KEY placeholder for collectstatic)
RUN SECRET_KEY=build-placeholder python manage.py collectstatic --noinput

# Create non-root user
RUN useradd --create-home app \
    && chown -R app:app /app
USER app

EXPOSE ${PORT}

ENTRYPOINT ["./entrypoint.sh"]
