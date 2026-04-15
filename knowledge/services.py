"""
knowledge/services.py

Pipeline RAG: ingestão de documentos (chunking + embedding) e busca vetorial.
Suporta OpenAI e Google Vertex AI via padrão Adapter (env var AI_PROVIDER).
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod

from django.db import transaction
from pgvector.django import CosineDistance

from knowledge.models import KnowledgeBase, KnowledgeChunk
from knowledge.parsers import extract_text_from_file, extract_text_from_url
from organizations.models import Organization

logger = logging.getLogger(__name__)

EMBEDDING_DIMENSIONS = 768
EMBEDDING_BATCH_SIZE = 64


# ---------------------------------------------------------------------------
# Embedding Provider — Adapter Pattern
# ---------------------------------------------------------------------------


class EmbeddingProvider(ABC):
    """Interface abstrata para provedores de embedding."""

    @abstractmethod
    def get_embeddings(self, texts: list[str]) -> list[list[float]]:
        """Retorna os vetores de embedding para *texts* (em lote)."""

    def get_embedding(self, text: str) -> list[float]:
        """Conveniência — embute 1 texto reusando ``get_embeddings``."""
        return self.get_embeddings([text])[0]


class OpenAIEmbeddingProvider(EmbeddingProvider):
    """Adapter para OpenAI text-embedding-3-small (768 dims)."""

    def __init__(self) -> None:
        from openai import OpenAI

        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "A variável de ambiente OPENAI_API_KEY não está definida."
            )
        self._client = OpenAI(api_key=api_key)

    def get_embeddings(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = self._client.embeddings.create(
                input=texts,
                model="text-embedding-3-small",
                dimensions=EMBEDDING_DIMENSIONS,
            )
            return [item.embedding for item in response.data]
        except Exception as exc:
            logger.error("Erro ao gerar embeddings via OpenAI: %s", exc)
            raise


class VertexAIEmbeddingProvider(EmbeddingProvider):
    """Adapter para Google GenAI text-embedding-004 (768 dims).

    Suporta dois modos de autenticação:
      - API Key (Google AI Studio): defina GOOGLE_API_KEY
      - Service Account (Vertex AI): defina GOOGLE_APPLICATION_CREDENTIALS,
        GOOGLE_CLOUD_PROJECT e GOOGLE_CLOUD_LOCATION
    """

    def __init__(self) -> None:
        from google import genai

        api_key = os.environ.get("GOOGLE_API_KEY")
        if api_key:
            self._client = genai.Client(api_key=api_key)
        else:
            project = os.environ.get("GOOGLE_CLOUD_PROJECT")
            location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
            if not project:
                raise EnvironmentError(
                    "Defina GOOGLE_API_KEY (AI Studio) ou "
                    "GOOGLE_APPLICATION_CREDENTIALS + GOOGLE_CLOUD_PROJECT (Vertex AI)."
                )
            self._client = genai.Client(
                vertexai=True, project=project, location=location,
            )

    def get_embeddings(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            response = self._client.models.embed_content(
                model="gemini-embedding-001",
                contents=texts,
                config={"output_dimensionality": EMBEDDING_DIMENSIONS},
            )
            return [emb.values for emb in response.embeddings]
        except Exception as exc:
            logger.error("Erro ao gerar embeddings via Google GenAI: %s", exc)
            raise


def get_embedding_provider() -> EmbeddingProvider:
    """Factory — retorna o provider configurado em AI_PROVIDER (default: openai)."""
    provider_name = os.environ.get("AI_PROVIDER", "openai").lower().strip()

    providers: dict[str, type[EmbeddingProvider]] = {
        "openai": OpenAIEmbeddingProvider,
        "google": VertexAIEmbeddingProvider,
        "vertexai": VertexAIEmbeddingProvider,  # alias legado
    }

    provider_cls = providers.get(provider_name)
    if provider_cls is None:
        raise ValueError(
            f"AI_PROVIDER '{provider_name}' inválido. "
            f"Opções: {', '.join(providers.keys())}"
        )

    return provider_cls()


# ---------------------------------------------------------------------------
# Knowledge Service
# ---------------------------------------------------------------------------


class KnowledgeService:
    """Orquestra ingestão de documentos e busca vetorial por organização."""

    def __init__(self, provider: EmbeddingProvider | None = None) -> None:
        self._provider = provider or get_embedding_provider()

    # -- Chunking -----------------------------------------------------------

    @staticmethod
    def _split_text(
        text: str,
        chunk_size: int = 1000,
        overlap: int = 200,
    ) -> list[str]:
        """Divide *text* em chunks de ~chunk_size caracteres com overlap.

        Respeita fronteiras de palavra para não cortar no meio.
        """
        words = text.split()
        if not words:
            return []

        chunks: list[str] = []
        current_chunk: list[str] = []
        current_length = 0

        for word in words:
            word_len = len(word) + (1 if current_chunk else 0)  # espaço

            if current_length + word_len > chunk_size and current_chunk:
                chunks.append(" ".join(current_chunk))

                # Calcula overlap: retrocede palavras até atingir ~overlap chars
                overlap_chunk: list[str] = []
                overlap_length = 0
                for w in reversed(current_chunk):
                    if overlap_length + len(w) + (1 if overlap_chunk else 0) > overlap:
                        break
                    overlap_chunk.insert(0, w)
                    overlap_length += len(w) + (1 if len(overlap_chunk) > 1 else 0)

                current_chunk = overlap_chunk
                current_length = sum(len(w) for w in current_chunk) + max(
                    len(current_chunk) - 1, 0
                )
            current_chunk.append(word)
            current_length += word_len

        if current_chunk:
            chunks.append(" ".join(current_chunk))

        return chunks

    # -- Embedding em batch -------------------------------------------------

    def _embed_in_batches(
        self, chunks: list[str], batch_size: int = EMBEDDING_BATCH_SIZE
    ) -> list[list[float]]:
        """Gera embeddings para *chunks* agrupando em requisições de *batch_size*."""
        embeddings: list[list[float]] = []
        for start in range(0, len(chunks), batch_size):
            batch = chunks[start : start + batch_size]
            logger.debug(
                "Gerando embeddings — batch %d-%d de %d",
                start + 1,
                start + len(batch),
                len(chunks),
            )
            embeddings.extend(self._provider.get_embeddings(batch))
        return embeddings

    # -- Ingestão -----------------------------------------------------------

    @transaction.atomic
    def ingest_document(
        self,
        organization: Organization,
        title: str,
        raw_text: str,
    ) -> KnowledgeBase:
        """Processa *raw_text*: cria KnowledgeBase, gera chunks + embeddings."""
        chunks = self._split_text(raw_text)
        if not chunks:
            raise ValueError("O texto fornecido está vazio ou não gerou chunks.")

        logger.info(
            "Ingerindo documento '%s' para org '%s' — %d chunk(s)",
            title,
            organization.slug,
            len(chunks),
        )

        kb = KnowledgeBase.objects.create(organization=organization, title=title)

        embeddings = self._embed_in_batches(chunks)

        KnowledgeChunk.objects.bulk_create(
            [
                KnowledgeChunk(
                    knowledge_base=kb,
                    content=chunk_text,
                    embedding=embedding,
                    metadata={"chunk_index": idx, "source": title},
                )
                for idx, (chunk_text, embedding) in enumerate(zip(chunks, embeddings))
            ]
        )

        logger.info(
            "Documento '%s' ingerido com sucesso — KnowledgeBase %s (%d chunks)",
            title,
            kb.id,
            len(chunks),
        )
        return kb

    def ingest_file(
        self,
        organization: Organization,
        file_obj,
        filename: str,
        title: str | None = None,
    ) -> KnowledgeBase:
        """Extrai texto de um arquivo (TXT/MD/PDF/DOCX) e ingere.

        Se *title* for vazio, usa o nome do arquivo sem extensão.
        """
        text, suggested_title = extract_text_from_file(file_obj, filename)
        return self.ingest_document(
            organization=organization,
            title=(title or suggested_title).strip(),
            raw_text=text,
        )

    def ingest_url(
        self,
        organization: Organization,
        url: str,
        title: str | None = None,
    ) -> KnowledgeBase:
        """Baixa *url*, extrai texto e ingere. Título vem da página se vazio."""
        text, suggested_title = extract_text_from_url(url)
        return self.ingest_document(
            organization=organization,
            title=(title or suggested_title).strip(),
            raw_text=text,
        )

    # -- Busca vetorial -----------------------------------------------------

    def find_relevant_context(
        self,
        organization: Organization,
        query_text: str,
        top_k: int = 3,
    ) -> list[KnowledgeChunk]:
        """Retorna os *top_k* chunks mais relevantes para *query_text*."""
        query_embedding = self._provider.get_embedding(query_text)

        results = (
            KnowledgeChunk.objects.filter(
                knowledge_base__organization=organization,
            )
            .annotate(distance=CosineDistance("embedding", query_embedding))
            .order_by("distance")[:top_k]
        )

        return list(results)
