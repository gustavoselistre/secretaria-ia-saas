from django.contrib import admin
from django.utils.html import format_html

from knowledge.models import KnowledgeBase, KnowledgeChunk


class KnowledgeChunkInline(admin.TabularInline):
    model = KnowledgeChunk
    readonly_fields = ("id", "short_content", "embedding", "metadata")
    fields = ("id", "short_content", "metadata")
    extra = 0
    can_delete = False
    show_change_link = True

    @admin.display(description="Conteúdo (prévia)")
    def short_content(self, obj: KnowledgeChunk) -> str:
        return obj.content[:120] + "…" if len(obj.content) > 120 else obj.content


@admin.register(KnowledgeBase)
class KnowledgeBaseAdmin(admin.ModelAdmin):
    list_display = ("title", "organization", "chunk_count", "created_at")
    list_filter = ("organization",)
    search_fields = ("title",)
    readonly_fields = ("id", "created_at")
    inlines = (KnowledgeChunkInline,)

    @admin.display(description="Chunks")
    def chunk_count(self, obj: KnowledgeBase) -> int:
        return obj.chunks.count()


@admin.register(KnowledgeChunk)
class KnowledgeChunkAdmin(admin.ModelAdmin):
    list_display = ("short_id", "short_content", "knowledge_base", "organization")
    list_filter = ("knowledge_base__organization",)
    search_fields = ("content",)
    readonly_fields = ("id", "embedding")
    fields = ("id", "knowledge_base", "content", "embedding", "metadata")

    @admin.display(description="ID")
    def short_id(self, obj: KnowledgeChunk) -> str:
        return str(obj.id)[:8]

    @admin.display(description="Conteúdo (prévia)")
    def short_content(self, obj: KnowledgeChunk) -> str:
        return obj.content[:80] + "…" if len(obj.content) > 80 else obj.content

    @admin.display(description="Organization")
    def organization(self, obj: KnowledgeChunk) -> str:
        return obj.knowledge_base.organization.name