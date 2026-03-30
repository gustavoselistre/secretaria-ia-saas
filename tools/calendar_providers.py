"""
tools/calendar_providers.py

Adapter pattern para provedores de calendário.
Cada tenant pode usar Google Calendar, Cal.com ou Calendly.
"""

from __future__ import annotations

import abc
import logging
from datetime import datetime, time, timedelta

import requests

logger = logging.getLogger(__name__)

# Horário comercial padrão (usado quando business_hours não é configurado)
DEFAULT_BUSINESS_HOURS = {
    "mon": ("09:00", "19:00"),
    "tue": ("09:00", "19:00"),
    "wed": ("09:00", "19:00"),
    "thu": ("09:00", "19:00"),
    "fri": ("09:00", "19:00"),
    "sat": ("09:00", "17:00"),
    "sun": None,  # Fechado
}

DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def _generate_hourly_slots(start: str, end: str) -> list[str]:
    """Gera slots de 1 hora entre start e end (ex: '09:00' a '18:00')."""
    start_h = int(start.split(":")[0])
    end_h = int(end.split(":")[0])
    return [f"{h:02d}:00" for h in range(start_h, end_h)]


def _get_business_hours_for_date(date_str: str, business_hours: dict) -> tuple[str, str] | None:
    """Retorna (start, end) do horário comercial para uma data, ou None se fechado."""
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    day_name = DAY_NAMES[dt.weekday()]

    if business_hours and day_name in business_hours:
        hours = business_hours[day_name]
        if hours is None:
            return None
        return (hours[0], hours[1])

    default = DEFAULT_BUSINESS_HOURS.get(day_name)
    if default is None:
        return None
    return default


class CalendarProvider(abc.ABC):
    """Interface abstrata para provedores de calendário."""

    @abc.abstractmethod
    def get_available_slots(
        self, calendar_id: str, date: str, credentials: dict,
        business_hours: dict | None = None,
    ) -> list[str]:
        """Retorna lista de horários livres ['09:00', '10:00', ...]."""

    @abc.abstractmethod
    def create_event(
        self, calendar_id: str, credentials: dict, *,
        title: str, date: str, time: str,
        duration_minutes: int = 60, description: str = "",
    ) -> dict:
        """Cria evento e retorna {'event_id': '...', 'status': 'confirmed'}."""

    @abc.abstractmethod
    def cancel_event(
        self, calendar_id: str, credentials: dict, event_id: str,
    ) -> dict:
        """Cancela evento e retorna {'status': 'cancelled'}."""


class GoogleCalendarProvider(CalendarProvider):
    """Integração com Google Calendar via API."""

    def _get_service(self, credentials: dict):
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        scopes = ["https://www.googleapis.com/auth/calendar"]
        creds = service_account.Credentials.from_service_account_info(
            credentials, scopes=scopes,
        )
        return build("calendar", "v3", credentials=creds)

    def get_available_slots(
        self, calendar_id: str, date: str, credentials: dict,
        business_hours: dict | None = None,
    ) -> list[str]:
        hours = _get_business_hours_for_date(date, business_hours or {})
        if hours is None:
            return []

        all_slots = _generate_hourly_slots(hours[0], hours[1])

        service = self._get_service(credentials)
        time_min = f"{date}T{hours[0]}:00Z"
        time_max = f"{date}T{hours[1]}:00Z"

        body = {
            "timeMin": time_min,
            "timeMax": time_max,
            "items": [{"id": calendar_id}],
        }
        result = service.freebusy().query(body=body).execute()
        busy_periods = result.get("calendars", {}).get(calendar_id, {}).get("busy", [])

        busy_hours = set()
        for period in busy_periods:
            start_dt = datetime.fromisoformat(period["start"].replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(period["end"].replace("Z", "+00:00"))
            h = start_dt.hour
            while h < end_dt.hour:
                busy_hours.add(f"{h:02d}:00")
                h += 1

        return [s for s in all_slots if s not in busy_hours]

    def create_event(
        self, calendar_id: str, credentials: dict, *,
        title: str, date: str, time: str,
        duration_minutes: int = 60, description: str = "",
    ) -> dict:
        service = self._get_service(credentials)

        start_dt = datetime.strptime(f"{date}T{time}", "%Y-%m-%dT%H:%M")
        end_dt = start_dt + timedelta(minutes=duration_minutes)

        event = {
            "summary": title,
            "description": description,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": "America/Sao_Paulo"},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": "America/Sao_Paulo"},
        }
        created = service.events().insert(calendarId=calendar_id, body=event).execute()
        return {"event_id": created["id"], "status": "confirmed"}

    def cancel_event(
        self, calendar_id: str, credentials: dict, event_id: str,
    ) -> dict:
        service = self._get_service(credentials)
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        return {"status": "cancelled"}


class CalcomProvider(CalendarProvider):
    """Integração com Cal.com via REST API."""

    BASE_URL = "https://api.cal.com/v1"

    def _headers(self, credentials: dict) -> dict:
        return {"Content-Type": "application/json"}

    def _params(self, credentials: dict) -> dict:
        return {"apiKey": credentials.get("api_key", "")}

    def get_available_slots(
        self, calendar_id: str, date: str, credentials: dict,
        business_hours: dict | None = None,
    ) -> list[str]:
        hours = _get_business_hours_for_date(date, business_hours or {})
        if hours is None:
            return []

        resp = requests.get(
            f"{self.BASE_URL}/availability",
            params={
                **self._params(credentials),
                "eventTypeId": calendar_id,
                "dateFrom": f"{date}T{hours[0]}:00.000Z",
                "dateTo": f"{date}T{hours[1]}:00.000Z",
            },
            headers=self._headers(credentials),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        slots = []
        for slot in data.get("slots", {}).get(date, []):
            t = slot.get("time", "")
            if "T" in t:
                hour_str = t.split("T")[1][:5]
                slots.append(hour_str)
        return slots

    def create_event(
        self, calendar_id: str, credentials: dict, *,
        title: str, date: str, time: str,
        duration_minutes: int = 60, description: str = "",
    ) -> dict:
        start_dt = f"{date}T{time}:00.000Z"

        resp = requests.post(
            f"{self.BASE_URL}/bookings",
            params=self._params(credentials),
            headers=self._headers(credentials),
            json={
                "eventTypeId": int(calendar_id),
                "start": start_dt,
                "metadata": {"title": title, "description": description},
                "responses": {
                    "name": title,
                    "email": credentials.get("default_email", "noreply@example.com"),
                },
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        booking_id = str(data.get("id", data.get("booking", {}).get("id", "")))
        return {"event_id": booking_id, "status": "confirmed"}

    def cancel_event(
        self, calendar_id: str, credentials: dict, event_id: str,
    ) -> dict:
        resp = requests.delete(
            f"{self.BASE_URL}/bookings/{event_id}",
            params=self._params(credentials),
            headers=self._headers(credentials),
            timeout=15,
        )
        resp.raise_for_status()
        return {"status": "cancelled"}


class CalendlyProvider(CalendarProvider):
    """Integração com Calendly via REST API."""

    BASE_URL = "https://api.calendly.com"

    def _headers(self, credentials: dict) -> dict:
        return {
            "Authorization": f"Bearer {credentials.get('access_token', '')}",
            "Content-Type": "application/json",
        }

    def get_available_slots(
        self, calendar_id: str, date: str, credentials: dict,
        business_hours: dict | None = None,
    ) -> list[str]:
        hours = _get_business_hours_for_date(date, business_hours or {})
        if hours is None:
            return []

        resp = requests.get(
            f"{self.BASE_URL}/event_type_available_times",
            params={
                "event_type": calendar_id,
                "start_time": f"{date}T{hours[0]}:00.000000Z",
                "end_time": f"{date}T{hours[1]}:00.000000Z",
            },
            headers=self._headers(credentials),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        slots = []
        for item in data.get("collection", []):
            start = item.get("start_time", "")
            if "T" in start:
                hour_str = start.split("T")[1][:5]
                slots.append(hour_str)
        return slots

    def create_event(
        self, calendar_id: str, credentials: dict, *,
        title: str, date: str, time: str,
        duration_minutes: int = 60, description: str = "",
    ) -> dict:
        resp = requests.post(
            f"{self.BASE_URL}/scheduling_links",
            headers=self._headers(credentials),
            json={
                "max_event_count": 1,
                "owner": calendar_id,
                "owner_type": "EventType",
            },
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        booking_url = data.get("resource", {}).get("booking_url", "")
        return {
            "event_id": booking_url,
            "status": "scheduling_link_created",
            "booking_url": booking_url,
        }

    def cancel_event(
        self, calendar_id: str, credentials: dict, event_id: str,
    ) -> dict:
        resp = requests.post(
            f"{self.BASE_URL}/scheduled_events/{event_id}/cancellation",
            headers=self._headers(credentials),
            json={"reason": "Cancelado pelo sistema"},
            timeout=15,
        )
        resp.raise_for_status()
        return {"status": "cancelled"}


_PROVIDERS: dict[str, type[CalendarProvider]] = {
    "google": GoogleCalendarProvider,
    "calcom": CalcomProvider,
    "calendly": CalendlyProvider,
}


def get_calendar_provider(provider_type: str) -> CalendarProvider:
    """Factory: retorna instância do provider pelo tipo."""
    cls = _PROVIDERS.get(provider_type)
    if cls is None:
        raise ValueError(f"Calendar provider desconhecido: {provider_type}")
    return cls()
