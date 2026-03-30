"""
tools/tests.py

Testes para tool registry, executors, models e calendar providers.
"""

from datetime import date, time
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase

from organizations.models import Organization
from tools.calendar_providers import (
    CalcomProvider,
    CalendlyProvider,
    GoogleCalendarProvider,
    get_calendar_provider,
)
from tools.executors import (
    CheckAvailabilityTool,
    CreateQuoteTool,
    GetCatalogTool,
    SaveClientInfoTool,
    ScheduleAppointmentTool,
)
from tools.models import Appointment, CalendarConfig, Client, Quote, ServiceCatalog
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


# --- Calendar Provider Tests ---


class CalendarProviderFactoryTests(TestCase):
    def test_get_google_provider(self):
        provider = get_calendar_provider("google")
        self.assertIsInstance(provider, GoogleCalendarProvider)

    def test_get_calcom_provider(self):
        provider = get_calendar_provider("calcom")
        self.assertIsInstance(provider, CalcomProvider)

    def test_get_calendly_provider(self):
        provider = get_calendar_provider("calendly")
        self.assertIsInstance(provider, CalendlyProvider)

    def test_unknown_provider_raises(self):
        with self.assertRaises(ValueError):
            get_calendar_provider("unknown")


class GoogleCalendarProviderTests(TestCase):
    @patch("tools.calendar_providers.GoogleCalendarProvider._get_service")
    def test_get_available_slots(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        mock_service.freebusy().query().execute.return_value = {
            "calendars": {
                "cal123": {
                    "busy": [
                        {"start": "2026-04-06T10:00:00Z", "end": "2026-04-06T11:00:00Z"},
                        {"start": "2026-04-06T14:00:00Z", "end": "2026-04-06T15:00:00Z"},
                    ],
                },
            },
        }

        provider = GoogleCalendarProvider()
        slots = provider.get_available_slots("cal123", "2026-04-06", {})
        self.assertNotIn("10:00", slots)
        self.assertNotIn("14:00", slots)
        self.assertIn("09:00", slots)
        self.assertIn("11:00", slots)

    @patch("tools.calendar_providers.GoogleCalendarProvider._get_service")
    def test_create_event(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service
        mock_service.events().insert().execute.return_value = {"id": "evt_123"}

        provider = GoogleCalendarProvider()
        result = provider.create_event(
            "cal123", {}, title="Corte", date="2026-04-06", time="10:00",
        )
        self.assertEqual(result["event_id"], "evt_123")
        self.assertEqual(result["status"], "confirmed")

    @patch("tools.calendar_providers.GoogleCalendarProvider._get_service")
    def test_cancel_event(self, mock_get_service):
        mock_service = MagicMock()
        mock_get_service.return_value = mock_service

        provider = GoogleCalendarProvider()
        result = provider.cancel_event("cal123", {}, "evt_123")
        self.assertEqual(result["status"], "cancelled")
        mock_service.events().delete.assert_called()


class CalcomProviderTests(TestCase):
    @patch("tools.calendar_providers.requests.get")
    def test_get_available_slots(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "slots": {
                    "2026-04-06": [
                        {"time": "2026-04-06T09:00:00Z"},
                        {"time": "2026-04-06T11:00:00Z"},
                    ],
                },
            },
        )
        mock_get.return_value.raise_for_status = MagicMock()

        provider = CalcomProvider()
        slots = provider.get_available_slots("123", "2026-04-06", {"api_key": "test"})
        self.assertEqual(slots, ["09:00", "11:00"])

    @patch("tools.calendar_providers.requests.post")
    def test_create_event(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"id": 456},
        )
        mock_post.return_value.raise_for_status = MagicMock()

        provider = CalcomProvider()
        result = provider.create_event(
            "123", {"api_key": "test"}, title="Manicure",
            date="2026-04-06", time="10:00",
        )
        self.assertEqual(result["event_id"], "456")
        self.assertEqual(result["status"], "confirmed")

    @patch("tools.calendar_providers.requests.delete")
    def test_cancel_event(self, mock_delete):
        mock_delete.return_value = MagicMock(status_code=200)
        mock_delete.return_value.raise_for_status = MagicMock()

        provider = CalcomProvider()
        result = provider.cancel_event("123", {"api_key": "test"}, "456")
        self.assertEqual(result["status"], "cancelled")


class CalendlyProviderTests(TestCase):
    @patch("tools.calendar_providers.requests.get")
    def test_get_available_slots(self, mock_get):
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "collection": [
                    {"start_time": "2026-04-06T09:00:00Z", "status": "available"},
                    {"start_time": "2026-04-06T13:00:00Z", "status": "available"},
                ],
            },
        )
        mock_get.return_value.raise_for_status = MagicMock()

        provider = CalendlyProvider()
        slots = provider.get_available_slots(
            "https://api.calendly.com/event_types/abc",
            "2026-04-06",
            {"access_token": "test"},
        )
        self.assertEqual(slots, ["09:00", "13:00"])

    @patch("tools.calendar_providers.requests.post")
    def test_create_event_returns_scheduling_link(self, mock_post):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "resource": {"booking_url": "https://calendly.com/d/abc-123"},
            },
        )
        mock_post.return_value.raise_for_status = MagicMock()

        provider = CalendlyProvider()
        result = provider.create_event(
            "https://api.calendly.com/event_types/abc",
            {"access_token": "test"},
            title="Corte", date="2026-04-06", time="10:00",
        )
        self.assertEqual(result["status"], "scheduling_link_created")
        self.assertIn("booking_url", result)


# --- Tools with CalendarConfig Tests ---


class CheckAvailabilityWithCalendarTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Org", slug="org")

    def test_fallback_local_without_config(self):
        """Sem CalendarConfig, usa lógica local (comportamento original)."""
        result = CheckAvailabilityTool().execute(self.org, date="2026-04-06")
        self.assertIn("09:00", result["available_slots"])
        self.assertEqual(len(result["available_slots"]), 10)

    @patch("tools.executors.get_calendar_provider")
    def test_uses_external_provider_when_configured(self, mock_get_provider):
        CalendarConfig.objects.create(
            organization=self.org, provider="google",
            calendar_id="cal@group.calendar.google.com",
            credentials={"type": "service_account"},
        )

        mock_provider = MagicMock()
        mock_provider.get_available_slots.return_value = ["09:00", "11:00", "15:00"]
        mock_get_provider.return_value = mock_provider

        result = CheckAvailabilityTool().execute(self.org, date="2026-04-06")
        self.assertEqual(result["available_slots"], ["09:00", "11:00", "15:00"])
        mock_provider.get_available_slots.assert_called_once()

    @patch("tools.executors.get_calendar_provider")
    def test_returns_error_on_provider_failure(self, mock_get_provider):
        CalendarConfig.objects.create(
            organization=self.org, provider="calcom",
            calendar_id="123", credentials={"api_key": "test"},
        )

        mock_provider = MagicMock()
        mock_provider.get_available_slots.side_effect = Exception("API error")
        mock_get_provider.return_value = mock_provider

        result = CheckAvailabilityTool().execute(self.org, date="2026-04-06")
        self.assertIn("error", result)
        self.assertEqual(result["available_slots"], [])


class ScheduleAppointmentWithCalendarTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.create(name="Org", slug="org")
        ServiceCatalog.objects.create(
            organization=self.org, category="Unhas", name="Manicure",
            price=Decimal("35.00"), duration_minutes=30,
        )

    def test_fallback_local_without_config(self):
        """Sem CalendarConfig, cria apenas no banco."""
        result = ScheduleAppointmentTool().execute(
            self.org, client_name="Maria", client_phone="+5551999990000",
            service_name="Manicure", date="2026-04-06", time="14:00",
        )
        self.assertEqual(result["status"], "Agendado")
        appt = Appointment.objects.first()
        self.assertEqual(appt.external_event_id, "")

    @patch("tools.executors.get_calendar_provider")
    def test_creates_external_event_when_configured(self, mock_get_provider):
        CalendarConfig.objects.create(
            organization=self.org, provider="google",
            calendar_id="cal@group.calendar.google.com",
            credentials={"type": "service_account"},
        )

        mock_provider = MagicMock()
        mock_provider.create_event.return_value = {"event_id": "google_evt_789", "status": "confirmed"}
        mock_get_provider.return_value = mock_provider

        result = ScheduleAppointmentTool().execute(
            self.org, client_name="Maria", client_phone="+5551999990000",
            service_name="Manicure", date="2026-04-06", time="14:00",
        )
        self.assertEqual(result["status"], "Agendado")
        appt = Appointment.objects.first()
        self.assertEqual(appt.external_event_id, "google_evt_789")
        mock_provider.create_event.assert_called_once()

    @patch("tools.executors.get_calendar_provider")
    def test_still_creates_appointment_on_provider_failure(self, mock_get_provider):
        CalendarConfig.objects.create(
            organization=self.org, provider="calendly",
            calendar_id="evt_type_abc", credentials={"access_token": "test"},
        )

        mock_provider = MagicMock()
        mock_provider.create_event.side_effect = Exception("API down")
        mock_get_provider.return_value = mock_provider

        result = ScheduleAppointmentTool().execute(
            self.org, client_name="Maria", client_phone="+5551999990000",
            date="2026-04-06", time="14:00",
        )
        self.assertEqual(result["status"], "Agendado")
        appt = Appointment.objects.first()
        self.assertEqual(appt.external_event_id, "")
