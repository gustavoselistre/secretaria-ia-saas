"""
knowledge/tests.py

Testes unitários para o pipeline RAG: chunking, ingestão e busca.
"""

from unittest.mock import MagicMock

from django.test import TestCase

from knowledge.models import KnowledgeBase, KnowledgeChunk
from knowledge.services import KnowledgeService
from organizations.models import Organization


class SplitTextTests(TestCase):
    """Testes para KnowledgeService._split_text."""

    def test_split_text_respects_chunk_size_and_overlap(self):
        text = " ".join(["palavra"] * 200)  # ~1400 chars
        chunks = KnowledgeService._split_text(text, chunk_size=500, overlap=100)

        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 600)

    def test_split_text_empty_returns_empty(self):
        self.assertEqual(KnowledgeService._split_text(""), [])
        self.assertEqual(KnowledgeService._split_text("   "), [])

    def test_split_text_short_returns_single_chunk(self):
        text = "Texto curto."
        chunks = KnowledgeService._split_text(text, chunk_size=1000)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], text)


class IngestDocumentTests(TestCase):
    """Testes para KnowledgeService.ingest_document com embedding mockado."""

    def setUp(self):
        self.org = Organization.objects.create(name="Org Teste", slug="org-teste")
        self.mock_provider = MagicMock()
        self.mock_provider.get_embedding.return_value = [0.1] * 768
        self.service = KnowledgeService(provider=self.mock_provider)

    def test_ingest_creates_kb_and_chunks(self):
        text = " ".join(["exemplo"] * 300)
        kb = self.service.ingest_document(self.org, "Doc Teste", text)

        self.assertIsInstance(kb, KnowledgeBase)
        self.assertEqual(kb.organization, self.org)
        self.assertEqual(kb.title, "Doc Teste")

        chunk_count = KnowledgeChunk.objects.filter(knowledge_base=kb).count()
        self.assertGreater(chunk_count, 0)
        self.assertTrue(self.mock_provider.get_embedding.called)

    def test_ingest_empty_text_raises(self):
        with self.assertRaises(ValueError):
            self.service.ingest_document(self.org, "Vazio", "")
