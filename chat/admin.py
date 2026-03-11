from django.contrib import admin

from chat.models import Conversation, Message


class MessageInline(admin.TabularInline):
    model = Message
    readonly_fields = ("id", "role", "content", "timestamp")
    fields = ("role", "content", "timestamp")
    extra = 0
    can_delete = False
    ordering = ("timestamp",)


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("customer_phone", "agent", "message_count", "created_at", "updated_at")
    list_filter = ("agent__organization", "agent")
    search_fields = ("customer_phone",)
    readonly_fields = ("id", "created_at", "updated_at")
    inlines = (MessageInline,)

    @admin.display(description="Mensagens")
    def message_count(self, obj: Conversation) -> int:
        return obj.messages.count()


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("short_content", "role", "conversation", "timestamp")
    list_filter = ("role", "conversation__agent__organization")
    search_fields = ("content",)
    readonly_fields = ("id", "timestamp")

    @admin.display(description="Conteúdo")
    def short_content(self, obj: Message) -> str:
        return obj.content[:80] + "…" if len(obj.content) > 80 else obj.content
