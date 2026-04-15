from __future__ import annotations

import logging

from django import forms
from django.contrib import admin, messages
from django.db.models import Count
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse

from knowledge.models import KnowledgeBase, KnowledgeChunk
from knowledge.parsers import SUPPORTED_FILE_EXTENSIONS
from knowledge.services import KnowledgeService
from organizations.models import Organization

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Multiple file upload widget/field (Django 4.2 compatível)
# ---------------------------------------------------------------------------


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """FileField que aceita múltiplos arquivos — retorna sempre uma lista."""

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("widget", MultipleFileInput(attrs={"multiple": True}))
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_clean(d, initial) for d in data if d]
        if data in (None, ""):
            return []
        return [single_clean(data, initial)]


# ---------------------------------------------------------------------------
# Form de ingestão
# ---------------------------------------------------------------------------


class KnowledgeIngestForm(forms.Form):
    organization = forms.ModelChoiceField(
        queryset=Organization.objects.filter(is_active=True),
        label="Organization",
        help_text="Cliente a quem o conteúdo pertence.",
    )
    title = forms.CharField(
        max_length=255,
        required=False,
        label="Título (opcional)",
        help_text=(
            "Se vazio, usa o nome do arquivo ou o título da página. "
            "Aplica-se ao primeiro item quando há múltiplos uploads."
        ),
    )
    files = MultipleFileField(
        required=False,
        label="Arquivos",
        help_text=(
            f"Formatos aceitos: {', '.join(SUPPORTED_FILE_EXTENSIONS)}. "
            "Selecione um ou mais arquivos."
        ),
    )
    url = forms.URLField(
        required=False,
        label="URL (opcional)",
        help_text="Baixa e ingere o texto limpo de uma página pública.",
    )

    def clean(self):
        cleaned = super().clean()
        files = cleaned.get("files") or []
        url = cleaned.get("url")
        if not files and not url:
            raise forms.ValidationError(
                "Envie ao menos um arquivo ou informe uma URL."
            )
        return cleaned


# ---------------------------------------------------------------------------
# Admins
# ---------------------------------------------------------------------------


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

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.select_related("organization").annotate(
            _chunk_count=Count("chunks")
        )

    @admin.display(description="Chunks", ordering="_chunk_count")
    def chunk_count(self, obj: KnowledgeBase) -> int:
        return getattr(obj, "_chunk_count", obj.chunks.count())

    # -- Substitui a tela de "add" pelo form de ingestão --------------------

    def add_view(self, request, form_url="", extra_context=None):
        form = KnowledgeIngestForm(request.POST or None, request.FILES or None)

        if request.method == "POST" and form.is_valid():
            organization: Organization = form.cleaned_data["organization"]
            title: str = form.cleaned_data.get("title") or ""
            files = form.cleaned_data.get("files") or []
            url: str = form.cleaned_data.get("url") or ""

            service = KnowledgeService()
            created: list[KnowledgeBase] = []
            errors: list[str] = []

            for uploaded in files:
                # Só o 1º upload herda o título manual; os demais usam o filename.
                effective_title = title if title and not created else ""
                try:
                    kb = service.ingest_file(
                        organization=organization,
                        file_obj=uploaded,
                        filename=uploaded.name,
                        title=effective_title or None,
                    )
                    created.append(kb)
                except ValueError as exc:
                    errors.append(f"{uploaded.name}: {exc}")
                except Exception as exc:  # pragma: no cover — defensivo
                    logger.exception("Falha ao ingerir %s", uploaded.name)
                    errors.append(f"{uploaded.name}: erro inesperado ({exc})")

            if url:
                effective_title = title if title and not created else ""
                try:
                    kb = service.ingest_url(
                        organization=organization,
                        url=url,
                        title=effective_title or None,
                    )
                    created.append(kb)
                except ValueError as exc:
                    errors.append(f"{url}: {exc}")
                except Exception as exc:  # pragma: no cover
                    logger.exception("Falha ao ingerir URL %s", url)
                    errors.append(f"{url}: erro inesperado ({exc})")

            for kb in created:
                total_chunks = kb.chunks.count()
                self.message_user(
                    request,
                    f"Ingerido: «{kb.title}» — {total_chunks} chunk(s).",
                    level=messages.SUCCESS,
                )
            for err in errors:
                self.message_user(request, err, level=messages.ERROR)

            if created:
                return HttpResponseRedirect(
                    reverse("admin:knowledge_knowledgebase_changelist")
                )
            # Se nenhum foi criado, renderiza o form novamente com os erros.

        context = {
            **self.admin_site.each_context(request),
            "title": "Ingerir documento para o RAG",
            "form": form,
            "opts": self.model._meta,
            "has_view_permission": self.has_view_permission(request),
        }
        if extra_context:
            context.update(extra_context)
        return render(request, "admin/knowledge/ingest_form.html", context)


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
