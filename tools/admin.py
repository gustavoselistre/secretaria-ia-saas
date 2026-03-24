from django.contrib import admin

from tools.models import Appointment, Client, Quote, ServiceCatalog


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "organization", "created_at")
    list_filter = ("organization",)
    search_fields = ("name", "phone", "email")


@admin.register(ServiceCatalog)
class ServiceCatalogAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "price", "duration_minutes", "is_active", "organization")
    list_filter = ("organization", "category", "is_active")
    search_fields = ("name", "category")


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ("client", "service", "date", "time", "status", "organization")
    list_filter = ("organization", "status", "date")
    search_fields = ("client__name", "client__phone")


@admin.register(Quote)
class QuoteAdmin(admin.ModelAdmin):
    list_display = ("__str__", "client", "total", "status", "organization", "created_at")
    list_filter = ("organization", "status")
