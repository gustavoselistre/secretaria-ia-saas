# Secretaria IA SaaS

Plataforma SaaS multi-tenant que fornece secretárias virtuais via WhatsApp. Organizações configuram agentes de IA que atendem clientes automaticamente — agendando consultas, gerando orçamentos e capturando leads — tudo via conversa natural no WhatsApp.

## Como funciona

```
Cliente envia mensagem no WhatsApp
        ↓
   Twilio ou uazapi recebe e repassa via webhook
        ↓
   Agente de IA processa (RAG + function calling)
        ↓
   Executa ações: agenda, orçamento, cadastro de lead
        ↓
   Responde ao cliente em português
```

O agente usa RAG (Retrieval Augmented Generation) para consultar a base de conhecimento da organização e function calling para executar ações no banco de dados.

## Stack

- **Backend**: Python 3.12 + Django 4.2
- **Banco de dados**: PostgreSQL 17 + pgvector (busca semântica)
- **WhatsApp**: Twilio (padrão) ou [uazapi.dev](https://uazapi.dev/) — selecionável via `WHATSAPP_PROVIDER`
- **IA**: OpenAI (GPT-4o) ou Google Gemini 2.5 Flash
- **Deploy**: Docker / Google Cloud Run

---

## Rodando localmente

### Pré-requisitos

- Docker e Docker Compose

### 1. Configure as variáveis de ambiente

Crie um arquivo `.env` na raiz do projeto:

```bash
SECRET_KEY=sua-secret-key-longa-e-aleatoria
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

# Banco de dados (já configurado no docker-compose)
DB_NAME=secretaria_db
DB_USER=gustavo
DB_PASSWORD=123456
DB_HOST=db
DB_PORT=5432

# Provedor de IA — escolha um
AI_PROVIDER=openai
OPENAI_API_KEY=sk-...

# ou Google Gemini
# AI_PROVIDER=google
# GOOGLE_API_KEY=...

# Provedor WhatsApp: "twilio" (padrão) ou "uazapi"
WHATSAPP_PROVIDER=twilio

# Twilio (quando WHATSAPP_PROVIDER=twilio)
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...

# uazapi.dev (quando WHATSAPP_PROVIDER=uazapi)
UAZAPI_BASE_URL=https://free.uazapi.com
UAZAPI_ADMIN_TOKEN=<token-admin-da-uazapi>          # necessário apenas para criar instâncias
UAZAPI_WEBHOOK_HMAC_SECRET=<segredo-para-validar-x-hmac-signature>
UAZAPI_WEBHOOK_URL=https://seu-dominio/webhook/whatsapp/

# Superusuário inicial
DJANGO_SUPERUSER_USERNAME=admin
DJANGO_SUPERUSER_EMAIL=admin@example.com
DJANGO_SUPERUSER_PASSWORD=admin123
```

### 2. Suba os serviços

```bash
docker-compose up
```

O Django estará disponível em `http://localhost:8000`.  
O admin em `http://localhost:8000/admin`.

### 3. Configure um tenant de exemplo

```bash
# Cria organização + agente + configuração do WhatsApp
docker-compose exec web python manage.py setup_tenant

# Carrega catálogo de serviços de exemplo
docker-compose exec web python manage.py load_catalog --org-slug <slug>

# Ingere um documento na base de conhecimento (RAG)
docker-compose exec web python manage.py ingest_document \
  --org-slug <slug> \
  --file caminho/para/documento.pdf \
  --title "Nome do Documento"
```

### 4. Teste sem WhatsApp real

```bash
# Testa o fluxo completo de chat
docker-compose exec web python manage.py test_chat \
  --org-slug <slug> \
  --message "Quais serviços vocês oferecem?"

# Testa a busca RAG
docker-compose exec web python manage.py test_rag \
  --org-slug <slug> \
  --query "horários disponíveis"
```

---

## Rodando sem Docker

```bash
# Instale as dependências
pip install -r requirements.txt

# Configure o PostgreSQL com pgvector separadamente, depois:
python manage.py migrate
python manage.py create_initial_superuser
python manage.py runserver
```

---

## Estrutura do projeto

```
secretaria-ia-saas/
├── config/          # Settings e URLs do Django
├── organizations/   # Gestão multi-tenant de organizações
├── agents/          # Configuração dos agentes de IA por organização
├── chat/            # Orquestração de conversas e provedores LLM
├── knowledge/       # Pipeline RAG e armazenamento pgvector
├── webhook/         # Handler do webhook Twilio WhatsApp
├── tools/           # Ferramentas de function calling (agendamento, orçamento, leads)
└── data/            # Arquivos de dados estáticos
```

---

## Deploy (Google Cloud Run)

```bash
# Build da imagem
docker build -t secretaria-ia .

# Push para o registry
docker tag secretaria-ia gcr.io/PROJECT_ID/secretaria-ia
docker push gcr.io/PROJECT_ID/secretaria-ia

# Deploy
gcloud run deploy secretaria-ia \
  --image gcr.io/PROJECT_ID/secretaria-ia \
  --platform managed \
  --region us-central1 \
  --set-env-vars "SECRET_KEY=...,DB_NAME=..."
```

Configure o webhook do provider escolhido para apontar para `https://seu-dominio/webhook/whatsapp/`.

### Usando uazapi.dev

```bash
# Cria a instância, configura webhook e exibe o QR code para escanear
python manage.py setup_tenant \
  --provider uazapi \
  --org "Minha Empresa" --slug minha-empresa \
  --phone "+5551999990000" \
  --webhook-url "https://seu-dominio/webhook/whatsapp/"

# Para reexibir o QR code se a sessão desconectar:
python manage.py uazapi_qr --org minha-empresa
```

O QR code é salvo em `/tmp/uazapi_qr.png`. Escaneie com o app do WhatsApp em **Aparelhos conectados → Conectar um aparelho**.

---

## Testes

```bash
python manage.py test
```
