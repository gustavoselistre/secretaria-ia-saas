"""
tools/executors.py

Implementações concretas de tools para o agente de IA.
Cada tool é auto-registrada via BaseTool.__init_subclass__.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Any

from tools.calendar_providers import get_calendar_provider
from tools.models import Appointment, CalendarConfig, Client, Quote, ServiceCatalog
from tools.registry import BaseTool

logger = logging.getLogger(__name__)


class GetCatalogTool(BaseTool):
    name = "get_catalog"
    description = "Consulta o catálogo de serviços e preços da empresa. Use quando o cliente perguntar sobre serviços disponíveis, preços ou categorias."
    parameters = {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "description": "Categoria para filtrar (ex: Cabelo, Unhas, Cílios, Sobrancelha, Bronzeamento). Se vazio, retorna todos.",
            },
        },
        "required": [],
    }

    def execute(self, organization, **kwargs) -> dict[str, Any]:
        qs = ServiceCatalog.objects.filter(organization=organization, is_active=True)

        category = kwargs.get("category", "")
        if category:
            qs = qs.filter(category__icontains=category)

        services = [
            {
                "name": s.name,
                "category": s.category,
                "price": float(s.price),
                "duration_minutes": s.duration_minutes,
            }
            for s in qs
        ]

        return {"services": services, "total_found": len(services)}


class CheckAvailabilityTool(BaseTool):
    name = "check_availability"
    description = "Verifica horários disponíveis para agendamento em uma data específica. Use quando o cliente quiser saber horários livres."
    parameters = {
        "type": "object",
        "properties": {
            "date": {
                "type": "string",
                "description": "Data no formato YYYY-MM-DD.",
            },
        },
        "required": ["date"],
    }

    def execute(self, organization, **kwargs) -> dict[str, Any]:
        date_str = kwargs["date"]
        try:
            datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            return {"error": f"Data inválida: {date_str}. Use o formato YYYY-MM-DD."}

        # Tenta usar calendário externo se configurado
        config = CalendarConfig.objects.filter(
            organization=organization, is_active=True,
        ).first()

        if config:
            return self._external_availability(config, date_str)

        return self._local_availability(organization, date_str)

    def _external_availability(self, config: CalendarConfig, date_str: str) -> dict:
        try:
            provider = get_calendar_provider(config.provider)
            slots = provider.get_available_slots(
                calendar_id=config.calendar_id,
                date=date_str,
                credentials=config.credentials,
                business_hours=config.business_hours,
            )
            return {"date": date_str, "available_slots": slots}
        except Exception as exc:
            logger.error("Erro ao consultar calendário externo (%s): %s", config.provider, exc)
            return {"date": date_str, "available_slots": [], "error": "Erro ao consultar agenda externa."}

    def _local_availability(self, organization, date_str: str) -> dict:
        date = datetime.strptime(date_str, "%Y-%m-%d").date()
        weekday = date.weekday()

        if weekday == 6:
            return {"date": date_str, "available_slots": [], "message": "Fechado aos domingos."}

        end_hour = 17 if weekday == 5 else 19

        booked = set(
            Appointment.objects.filter(
                organization=organization,
                date=date,
                status=Appointment.Status.SCHEDULED,
            ).values_list("time", flat=True)
        )

        slots = []
        for hour in range(9, end_hour):
            t = time(hour, 0)
            if t not in booked:
                slots.append(f"{hour:02d}:00")

        return {"date": date_str, "available_slots": slots}


class ScheduleAppointmentTool(BaseTool):
    name = "schedule_appointment"
    description = "Agenda um horário para o cliente. Use quando o cliente confirmar que quer agendar um serviço."
    parameters = {
        "type": "object",
        "properties": {
            "client_name": {
                "type": "string",
                "description": "Nome do cliente.",
            },
            "client_phone": {
                "type": "string",
                "description": "Telefone do cliente (formato +5551999990000).",
            },
            "service_name": {
                "type": "string",
                "description": "Nome do serviço (ex: Corte feminino, Manicure).",
            },
            "date": {
                "type": "string",
                "description": "Data no formato YYYY-MM-DD.",
            },
            "time": {
                "type": "string",
                "description": "Horário no formato HH:MM.",
            },
            "notes": {
                "type": "string",
                "description": "Observações adicionais.",
            },
        },
        "required": ["client_name", "client_phone", "date", "time"],
    }

    def execute(self, organization, **kwargs) -> dict[str, Any]:
        try:
            date = datetime.strptime(kwargs["date"], "%Y-%m-%d").date()
            appt_time = datetime.strptime(kwargs["time"], "%H:%M").time()
        except ValueError:
            return {"error": "Data ou horário inválido. Use YYYY-MM-DD e HH:MM."}

        # Verificar conflito local
        conflict = Appointment.objects.filter(
            organization=organization,
            date=date,
            time=appt_time,
            status=Appointment.Status.SCHEDULED,
        ).exists()

        if conflict:
            return {"error": f"Horário {kwargs['time']} em {kwargs['date']} já está ocupado."}

        # Get or create client
        client, _ = Client.objects.get_or_create(
            organization=organization,
            phone=kwargs["client_phone"],
            defaults={"name": kwargs["client_name"]},
        )
        if client.name != kwargs["client_name"]:
            client.name = kwargs["client_name"]
            client.save(update_fields=["name"])

        # Buscar serviço
        service = None
        service_name = kwargs.get("service_name", "")
        if service_name:
            service = ServiceCatalog.objects.filter(
                organization=organization,
                name__icontains=service_name,
                is_active=True,
            ).first()

        # Criar evento no calendário externo se configurado
        external_event_id = ""
        config = CalendarConfig.objects.filter(
            organization=organization, is_active=True,
        ).first()

        if config:
            try:
                provider = get_calendar_provider(config.provider)
                title = f"{client.name} — {service.name if service else 'Agendamento'}"
                result = provider.create_event(
                    calendar_id=config.calendar_id,
                    credentials=config.credentials,
                    title=title,
                    date=kwargs["date"],
                    time=kwargs["time"],
                    duration_minutes=service.duration_minutes if service else 60,
                    description=kwargs.get("notes", ""),
                )
                external_event_id = result.get("event_id", "")
            except Exception as exc:
                logger.error(
                    "Erro ao criar evento no calendário externo (%s): %s",
                    config.provider, exc,
                )

        appointment = Appointment.objects.create(
            organization=organization,
            client=client,
            service=service,
            date=date,
            time=appt_time,
            notes=kwargs.get("notes", ""),
            external_event_id=external_event_id,
        )

        return {
            "appointment_id": str(appointment.id),
            "client_name": client.name,
            "service": service.name if service else "Não especificado",
            "date": str(date),
            "time": str(appt_time),
            "status": "Agendado",
            "message": f"Agendamento confirmado para {client.name} em {date.strftime('%d/%m/%Y')} às {appt_time.strftime('%H:%M')}.",
        }


class CreateQuoteTool(BaseTool):
    name = "create_quote"
    description = "Cria um orçamento com os serviços solicitados pelo cliente. Use quando o cliente pedir um orçamento ou quiser saber o total de vários serviços."
    parameters = {
        "type": "object",
        "properties": {
            "client_name": {
                "type": "string",
                "description": "Nome do cliente.",
            },
            "client_phone": {
                "type": "string",
                "description": "Telefone do cliente.",
            },
            "items": {
                "type": "array",
                "description": "Lista de serviços para o orçamento.",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Nome do serviço."},
                        "qty": {"type": "integer", "description": "Quantidade."},
                    },
                    "required": ["name"],
                },
            },
        },
        "required": ["items"],
    }

    def execute(self, organization, **kwargs) -> dict[str, Any]:
        items_input = kwargs.get("items", [])
        if not items_input:
            return {"error": "Nenhum item informado para o orçamento."}

        quote_items = []
        total = Decimal("0")

        for item in items_input:
            name = item.get("name", "")
            qty = item.get("qty", 1)

            service = ServiceCatalog.objects.filter(
                organization=organization,
                name__icontains=name,
                is_active=True,
            ).first()

            if service:
                unit_price = service.price
            else:
                return {"error": f"Serviço '{name}' não encontrado no catálogo."}

            line_total = unit_price * qty
            total += line_total
            quote_items.append({
                "name": service.name,
                "qty": qty,
                "unit_price": float(unit_price),
                "line_total": float(line_total),
            })

        # Client (optional)
        client = None
        if kwargs.get("client_phone"):
            client, _ = Client.objects.get_or_create(
                organization=organization,
                phone=kwargs["client_phone"],
                defaults={"name": kwargs.get("client_name", "")},
            )

        quote = Quote.objects.create(
            organization=organization,
            client=client,
            items=quote_items,
            total=total,
        )

        return {
            "quote_id": str(quote.id),
            "items": quote_items,
            "total": float(total),
            "message": f"Orçamento criado: R$ {total:.2f}.",
        }


class SaveClientInfoTool(BaseTool):
    name = "save_client_info"
    description = "Salva ou atualiza os dados de um cliente. Use quando o cliente fornecer nome, telefone ou email."
    parameters = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Nome do cliente."},
            "phone": {"type": "string", "description": "Telefone do cliente."},
            "email": {"type": "string", "description": "Email do cliente."},
        },
        "required": ["name", "phone"],
    }

    def execute(self, organization, **kwargs) -> dict[str, Any]:
        client, created = Client.objects.get_or_create(
            organization=organization,
            phone=kwargs["phone"],
            defaults={
                "name": kwargs["name"],
                "email": kwargs.get("email", ""),
            },
        )

        if not created:
            client.name = kwargs["name"]
            if kwargs.get("email"):
                client.email = kwargs["email"]
            client.save(update_fields=["name", "email", "updated_at"])

        return {
            "client_id": str(client.id),
            "name": client.name,
            "phone": client.phone,
            "status": "criado" if created else "atualizado",
        }
