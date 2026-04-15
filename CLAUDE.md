# CLAUDE.md — Secretaria IA SaaS

AI assistant guide for this codebase. Read this before making any changes.

---

## Project Overview

Multi-tenant SaaS application providing WhatsApp-based AI secretarial services. Organizations configure AI agents that handle customer conversations via WhatsApp, using RAG (Retrieval Augmented Generation) and function-calling tools to schedule appointments, generate quotes, and capture leads.

**Stack**: Python 3.12 + Django 4.2+ + PostgreSQL/pgvector + Twilio / uazapi.dev + OpenAI/Google Gemini

WhatsApp provider is selectable at runtime via the `WHATSAPP_PROVIDER` env var (`twilio` or `uazapi`). Provider-specific logic is encapsulated in `webhook/providers.py`.

---

## Repository Structure

```
secretaria-ia-saas/
├── config/               # Django project settings and URL routing
│   ├── settings.py       # Main config (DB, middleware, apps, logging)
│   ├── urls.py           # Root URL routing
│   ├── wsgi.py / asgi.py # WSGI/ASGI entry points
├── organizations/        # Multi-tenant organization management
├── agents/               # AI agent configuration per organization
├── chat/                 # Conversation state + LLM orchestration
│   └── services.py       # ChatService + LLMProvider adapters (542 lines)
├── knowledge/            # RAG pipeline and pgvector storage
│   └── services.py       # KnowledgeService + EmbeddingProvider adapters
├── webhook/              # Twilio WhatsApp webhook handler
│   └── views.py          # Entry point for all incoming WhatsApp messages
├── tools/                # AI function-calling tools
│   ├── registry.py       # Auto-registration via __init_subclass__
│   ├── executors.py      # 5 tool implementations
│   └── definitions.py    # Format tools for OpenAI/Gemini APIs
├── data/                 # Static data files
├── Dockerfile            # Cloud Run optimized (Python 3.12-slim, port 8080)
├── docker-compose.yaml   # Local dev: PostgreSQL 17 + pgvector + Django
├── entrypoint.sh         # Container startup: migrate → superuser → gunicorn
└── requirements.txt      # Python dependencies
```

---

## Development Setup

### Prerequisites

- Docker and Docker Compose
- Python 3.12 (for local dev without Docker)

### Local Development with Docker

```bash
# Copy env file and configure
cp .env.example .env  # (create if missing; see Environment Variables section)

# Start all services
docker-compose up

# Access Django admin
# http://localhost:8000/admin
```

### Local Development without Docker

```bash
# Install dependencies
pip install -r requirements.txt

# Start PostgreSQL with pgvector separately, then:
python manage.py migrate
python manage.py create_initial_superuser
python manage.py runserver
```

### Database Defaults (docker-compose)

| Variable | Default |
|----------|---------|
| DB_NAME  | secretaria_db |
| DB_USER  | gustavo |
| DB_PASSWORD | 123456 |
| DB_HOST  | localhost |
| DB_PORT  | 5432 |

---

## Environment Variables

All configuration comes from environment variables (via `django-environ`).

### Required in Production

```bash
SECRET_KEY=<long-random-string>
DEBUG=False
ALLOWED_HOSTS=yourdomain.com,localhost
DB_NAME=secretaria_db
DB_USER=<user>
DB_PASSWORD=<password>
DB_HOST=<host>
DB_PORT=5432

# AI Provider — choose one
AI_PROVIDER=openai          # or "google" or "vertexai"
OPENAI_API_KEY=sk-...       # if AI_PROVIDER=openai
GOOGLE_API_KEY=...          # if AI_PROVIDER=google (AI Studio)
GOOGLE_APPLICATION_CREDENTIALS=/path/to/sa.json  # if AI_PROVIDER=vertexai
GOOGLE_CLOUD_PROJECT=my-project   # if AI_PROVIDER=vertexai
GOOGLE_CLOUD_LOCATION=us-central1 # if AI_PROVIDER=vertexai

# WhatsApp provider: "twilio" (padrão) ou "uazapi"
WHATSAPP_PROVIDER=twilio

# Twilio (quando WHATSAPP_PROVIDER=twilio)
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...

# uazapi.dev (quando WHATSAPP_PROVIDER=uazapi)
UAZAPI_BASE_URL=https://free.uazapi.com
UAZAPI_ADMIN_TOKEN=...                     # needed to create instances
UAZAPI_WEBHOOK_HMAC_SECRET=...             # validates x-hmac-signature
UAZAPI_WEBHOOK_URL=https://yourdomain.com/webhook/whatsapp/

# Initial superuser (used by entrypoint.sh)
DJANGO_SUPERUSER_USERNAME=admin
DJANGO_SUPERUSER_EMAIL=admin@example.com
DJANGO_SUPERUSER_PASSWORD=<password>
```

### Optional

```bash
CLOUD_SQL_INSTANCE=project:region:instance  # Cloud Run + Cloud SQL socket
PORT=8080                   # Gunicorn port (default: 8080)
GUNICORN_WORKERS=2          # Worker processes
GUNICORN_THREADS=4          # Threads per worker
```

---

## Architecture Patterns

### Multi-Tenant Isolation

Every model references `Organization` as its tenant root. All queries **must** be scoped by organization. The tenant is resolved at the webhook entry point via `WhatsAppConfig` (maps Twilio phone number → Organization → AIAgent).

```python
# Always scope queries by organization
ServiceCatalog.objects.filter(organization=organization, is_active=True)
```

### LLM Provider Adapter Pattern

`chat/services.py` defines an abstract `LLMProvider` interface. The active provider is selected from the `AI_PROVIDER` env var. Add new providers by implementing `LLMProvider` — never call provider SDKs directly from views or tools.

```python
class LLMProvider(ABC):
    def generate(model, temp, messages) -> str
    def generate_with_tools(model, temp, messages, tools) -> LLMResponse
    def build_tool_round_messages(llm_response, tool_results) -> list[dict]
    def transcribe_audio(audio_bytes, content_type) -> str
```

Implementations: `OpenAILLMProvider`, `GeminiLLMProvider`.

### Embedding Provider Adapter Pattern

`knowledge/services.py` defines `EmbeddingProvider`. Currently 768-dimensional vectors (matches both text-embedding-3-small and gemini-embedding-001).

Implementations: `OpenAIEmbeddingProvider`, `VertexAIEmbeddingProvider`.

**Important**: If you change embedding dimensions, you must re-migrate the `KnowledgeChunk.embedding` VectorField and re-embed all documents.

### Tool Auto-Registration

`tools/registry.py` uses `__init_subclass__` to auto-register all tools. Create a new tool by:

1. Subclass `BaseTool` in `tools/executors.py`
2. Set `name`, `description`, `parameters` (JSON Schema) class attributes
3. Implement `execute(self, organization, **kwargs) -> dict`
4. Add Gemini format in `tools/definitions.py:get_gemini_tools()`
5. Add OpenAI format in `tools/definitions.py:get_openai_tools()`

```python
class MyTool(BaseTool):
    name = "my_tool"
    description = "What this tool does"
    parameters = {
        "type": "object",
        "properties": {"param": {"type": "string", "description": "..."}},
        "required": ["param"]
    }

    def execute(self, organization, **kwargs) -> dict:
        # Always use organization for tenant isolation
        ...
```

### Tool Execution Loop (Chat Flow)

```
Webhook → ChatService.generate_response()
  → find_relevant_context() (RAG, top 3 chunks)
  → build_messages() (system prompt + RAG context + history + user message)
  → Tool loop (max 5 rounds):
      LLM call → if tool_calls → execute → append results → repeat
  → Save assistant response
  → Return text
```

---

## Data Models

### Key Relationships

```
Organization (tenant root)
  ├── AIAgent (1:many)
  ├── WhatsAppConfig (1:1) → maps phone number → Agent
  ├── KnowledgeBase (1:many) → KnowledgeChunk (pgvector)
  ├── Client (1:many)
  ├── ServiceCatalog (1:many)
  ├── Appointment (1:many) → Client + ServiceCatalog
  └── Quote (1:many) → Client

AIAgent → Conversation (1:many) → Message (1:many)
```

All primary keys are UUIDs (`UUIDField(primary_key=True, default=uuid.uuid4)`).

---

## URL Routes

| URL | Handler | Purpose |
|-----|---------|---------|
| `/healthz/` | inline | Health check → `{"status": "ok"}` |
| `/admin/` | Django admin | Full model management |
| `/webhook/whatsapp/` | `webhook.views.whatsapp_webhook` | Twilio/uazapi POST endpoint |

The webhook delegates parsing and signature validation to the active provider (see `webhook/providers.py`). Twilio validates via `X-Twilio-Signature`; uazapi validates via `x-hmac-signature` (HMAC-SHA256 of the raw body with `UAZAPI_WEBHOOK_HMAC_SECRET`). In production, the full URL must be configured in the active provider's console/API.

---

## Management Commands

Use these to set up data and test features without a real WhatsApp connection:

```bash
# Initial tenant setup (creates Org + Agent + WhatsAppConfig)
python manage.py setup_tenant

# Load sample service catalog for an organization
python manage.py load_catalog --org-slug <slug>

# Ingest a document into the RAG knowledge base
python manage.py ingest_document --org-slug <slug> --file <path> --title "Doc Title"

# Test the full chat service with a simulated message
python manage.py test_chat --org-slug <slug> --message "Quais serviços vocês oferecem?"

# Test the RAG pipeline
python manage.py test_rag --org-slug <slug> --query "seu texto de busca"

# Test the webhook flow
python manage.py test_webhook
```

---

## Running Tests

```bash
# Run all tests
python manage.py test

# Run tests for a specific app
python manage.py test chat
python manage.py test webhook
python manage.py test tools
```

Tests use Django's built-in `TestCase`. No pytest configuration is set up. Test files exist in each app but coverage is minimal — expand before adding significant features.

---

## Deployment (Cloud Run)

### Build & Deploy

```bash
# Build Docker image
docker build -t secretaria-ia .

# Push to GCR/Artifact Registry
docker tag secretaria-ia gcr.io/PROJECT_ID/secretaria-ia
docker push gcr.io/PROJECT_ID/secretaria-ia

# Deploy to Cloud Run
gcloud run deploy secretaria-ia \
  --image gcr.io/PROJECT_ID/secretaria-ia \
  --platform managed \
  --region us-central1 \
  --set-env-vars "SECRET_KEY=...,DB_NAME=..."
```

### Startup Sequence (entrypoint.sh)

1. `python manage.py migrate --noinput`
2. `python manage.py create_initial_superuser` (idempotent)
3. `gunicorn config.wsgi:application` (port 8080, configurable workers/threads)

### Cloud SQL Integration

Set `CLOUD_SQL_INSTANCE=project:region:instance` to use Unix socket instead of TCP for Cloud SQL connectivity.

---

## Conventions & Key Rules

### Django Conventions

- Use `django-environ` for all config — never hardcode secrets
- All models use UUID primary keys
- Every model with tenant data must have `organization` FK
- Use `auto_now_add=True` for `created_at`, `auto_now=True` for `updated_at`
- Migrations are committed to the repo — run `makemigrations` after model changes

### Code Style

- Follow PEP 8
- Use type hints where practical
- Keep services in `services.py` per app — don't put business logic in views
- Views are intentionally thin (most are currently empty — the system is driven by webhook)
- Use structured logging: `logger = logging.getLogger(__name__)` at top of each module

### Logging

- **Development**: verbose text format
- **Production**: JSON format (Cloud Run compatible via `python-json-logger`)
- Suppress noisy libraries: `google_genai`, `httpcore`, `httpx` → WARNING level
- Log context: include `org_id`, `phone`, `agent_id` where possible

### Security Rules

- **Never** skip the provider's signature validation in `webhook/providers.py` (Twilio `X-Twilio-Signature` or uazapi `x-hmac-signature`)
- **Always** scope database queries by `organization` for tenant isolation
- **Never** log sensitive data: phone numbers should be partially masked in logs, no API keys, no message content at INFO level
- Set `DEBUG=False` in production
- `SECRET_KEY` must be a long random string from environment

### Embedding Consistency

- All embeddings must use the same model and dimensions (currently 768)
- Switching embedding models requires re-embedding all `KnowledgeChunk` records
- The `KnowledgeChunk.embedding` VectorField dimension is set in the migration — changing it requires a new migration

### Tool Development

- Tools must be stateless except for database reads/writes
- Always accept `organization` as first parameter in `execute()`
- Return dicts that are JSON-serializable (no Django model objects)
- Handle `DoesNotExist` gracefully and return user-friendly error messages
- Both `get_gemini_tools()` and `get_openai_tools()` in `definitions.py` must be updated when adding tools

---

## AI Provider Notes

### OpenAI

- Chat: `gpt-4o` (default model on `AIAgent`)
- Embeddings: `text-embedding-3-small` (768 dims)
- Tool format: `{"type": "function", "function": {...}}`

### Google Gemini (AI Studio)

- Chat: `gemini-2.5-flash`
- Embeddings: `gemini-embedding-001` (768 dims)
- Tool format: `google.genai.types.FunctionDeclaration`
- Auth: `GOOGLE_API_KEY` env var

### Vertex AI

- Same models as Gemini but authenticated via service account
- Auth: `GOOGLE_APPLICATION_CREDENTIALS` path + `GOOGLE_CLOUD_PROJECT`
- Uses `langchain-google-vertexai` for some integrations

---

## What's Not Yet Implemented

These areas exist as stubs or are planned:

- REST API (all `views.py` files except `webhook/views.py` are empty)
- Frontend (no templates — admin only)
- Payment processing
- Email notifications
- Rate limiting on LLM calls
- API authentication (DRF is installed but not configured)
- Audit logging for sensitive operations
- Client data encryption at rest

When implementing these, follow the existing patterns: abstract interfaces for external services, organization-scoped queries, structured logging.

---

## Locale & Timezone

- Language: Portuguese (Brazil) — `LANGUAGE_CODE = "pt-br"`
- Timezone: `America/Sao_Paulo`
- All user-facing messages from the AI agent should be in Brazilian Portuguese
