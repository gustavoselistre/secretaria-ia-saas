"""
tools/registry.py

Base class para tools com auto-registro.
Cada subclasse de BaseTool é registrada automaticamente pelo nome.
"""

from __future__ import annotations

import abc
from typing import Any

_TOOL_REGISTRY: dict[str, type[BaseTool]] = {}


class BaseTool(abc.ABC):
    """Tool que o agente de IA pode invocar via function calling."""

    name: str = ""
    description: str = ""
    parameters: dict = {}

    @abc.abstractmethod
    def execute(self, organization, **kwargs) -> dict[str, Any]:
        """Executa a tool e retorna resultado JSON-serializável."""

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if getattr(cls, "name", ""):
            _TOOL_REGISTRY[cls.name] = cls


def get_all_tools() -> dict[str, type[BaseTool]]:
    return dict(_TOOL_REGISTRY)


def get_tool(name: str) -> type[BaseTool]:
    return _TOOL_REGISTRY[name]
