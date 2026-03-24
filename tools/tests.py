"""
tools/tests.py

Testes para tool registry, executors e models.
"""

from datetime import date, time
from decimal import Decimal

from django.test import TestCase

from organizations.models import Organization
from tools.executors import (
    CheckAvailabilityTool,
    CreateQuoteTool,
    GetCatalogTool,
    SaveClientInfoTool,
    ScheduleAppointmentTool,
)
from tools.models import Appointment, Client, Quote, ServiceCatalog
from tools.registry import get_all_tools, get_tool


class ToolRegistryTests(TestCase):
    """Testes para o registro automático de tools."""

    def test_all_tools_registered(self):
        tools = get_all_tools()
        self.assertIn("get_catalog", tools)
        self.assertIn("check_availability", tools)
        self.assertIn("schedule_appointment", tools)
        self.assertIn("create_quote", tools)
        self.assertIn("save_client_info", tools)
        self.assertEqual(len(tools), 5)

    def test_get_tool_by_name(self):
        tool_cls = get_tool("get_catalog")
        self.assertEqual(tool_cls, GetCatalogTool)


class GetCatalogToolTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Org", slug="org")
        ServiceCatalog.objects.create(
            organization=self.org, category="Cabelo", name="Corte feminino",
            price=Decimal("80.00"), duration_minutes=45,
        )
        ServiceCatalog.objects.create(
            organization=self.org, category="Unhas", name="Manicure",
            price=Decimal("35.00"), duration_minutes=30,
        )

    def test_get_all_services(self):
        result = GetCatalogTool().execute(self.org)
        self.assertEqual(result["total_found"], 2)

    def test_filter_by_category(self):
        result = GetCatalogTool().execute(self.org, category="Cabelo")
        self.assertEqual(result["total_found"], 1)
        self.assertEqual(result["services"][0]["name"], "Corte feminino")

    def test_isolates_by_org(self):
        other_org = Organization.objects.create(name="Outra", slug="outra")
        result = GetCatalogTool().execute(other_org)
        self.assertEqual(result["total_found"], 0)


class CheckAvailabilityToolTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Org", slug="org")
        self.client = Client.objects.create(
            organization=self.org, name="João", phone="+5551999990000",
        )

    def test_all_slots_free(self):
        result = CheckAvailabilityTool().execute(self.org, date="2026-04-06")  # segunda
        self.assertIn("09:00", result["available_slots"])
        self.assertIn("18:00", result["available_slots"])
        self.assertEqual(len(result["available_slots"]), 10)  # 9h-18h

    def test_slot_occupied(self):
        Appointment.objects.create(
            organization=self.org, client=self.client,
            date=date(2026, 4, 6), time=time(10, 0),
        )
        result = CheckAvailabilityTool().execute(self.org, date="2026-04-06")
        self.assertNotIn("10:00", result["available_slots"])
        self.assertEqual(len(result["available_slots"]), 9)

    def test_sunday_closed(self):
        result = CheckAvailabilityTool().execute(self.org, date="2026-04-05")  # domingo
        self.assertEqual(result["available_slots"], [])

    def test_saturday_shorter_hours(self):
        result = CheckAvailabilityTool().execute(self.org, date="2026-04-04")  # sábado
        self.assertEqual(len(result["available_slots"]), 8)  # 9h-16h


class ScheduleAppointmentToolTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Org", slug="org")
        ServiceCatalog.objects.create(
            organization=self.org, category="Unhas", name="Manicure",
            price=Decimal("35.00"),
        )

    def test_schedule_creates_appointment_and_client(self):
        result = ScheduleAppointmentTool().execute(
            self.org,
            client_name="Maria",
            client_phone="+5551999990000",
            service_name="Manicure",
            date="2026-04-06",
            time="14:00",
        )
        self.assertIn("appointment_id", result)
        self.assertEqual(result["status"], "Agendado")
        self.assertEqual(Client.objects.count(), 1)
        self.assertEqual(Appointment.objects.count(), 1)

    def test_conflict_returns_error(self):
        ScheduleAppointmentTool().execute(
            self.org, client_name="Maria", client_phone="+5551999990000",
            date="2026-04-06", time="14:00",
        )
        result = ScheduleAppointmentTool().execute(
            self.org, client_name="Ana", client_phone="+5551888880000",
            date="2026-04-06", time="14:00",
        )
        self.assertIn("error", result)


class CreateQuoteToolTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Org", slug="org")
        ServiceCatalog.objects.create(
            organization=self.org, category="Cabelo", name="Corte feminino",
            price=Decimal("80.00"),
        )
        ServiceCatalog.objects.create(
            organization=self.org, category="Unhas", name="Manicure",
            price=Decimal("35.00"),
        )

    def test_create_quote_with_items(self):
        result = CreateQuoteTool().execute(
            self.org,
            items=[
                {"name": "Corte feminino", "qty": 1},
                {"name": "Manicure", "qty": 1},
            ],
        )
        self.assertEqual(result["total"], 115.0)
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(Quote.objects.count(), 1)

    def test_unknown_service_returns_error(self):
        result = CreateQuoteTool().execute(
            self.org, items=[{"name": "Serviço inexistente"}],
        )
        self.assertIn("error", result)


class SaveClientInfoToolTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Org", slug="org")

    def test_create_client(self):
        result = SaveClientInfoTool().execute(
            self.org, name="Maria", phone="+5551999990000", email="maria@test.com",
        )
        self.assertEqual(result["status"], "criado")
        self.assertEqual(Client.objects.count(), 1)

    def test_update_existing_client(self):
        Client.objects.create(
            organization=self.org, name="Maria Velha", phone="+5551999990000",
        )
        result = SaveClientInfoTool().execute(
            self.org, name="Maria Nova", phone="+5551999990000",
        )
        self.assertEqual(result["status"], "atualizado")
        self.assertEqual(Client.objects.first().name, "Maria Nova")
