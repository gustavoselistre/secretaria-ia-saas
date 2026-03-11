import uuid

from django.db import models
from pgvector.django import VectorField


class KnowledgeBase(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="knowledge_bases",
    )
    title = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Knowledge Base"
        verbose_name_plural = "Knowledge Bases"

    def __str__(self):
        return f"{self.title} ({self.organization})"


class KnowledgeChunk(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    knowledge_base = models.ForeignKey(
        KnowledgeBase,
        on_delete=models.CASCADE,
        related_name="chunks",
    )
    content = models.TextField()
    embedding = VectorField(dimensions=768)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "Knowledge Chunk"
        verbose_name_plural = "Knowledge Chunks"

    def __str__(self):
        return f"Chunk {str(self.id)[:8]} — {self.knowledge_base.title}"
