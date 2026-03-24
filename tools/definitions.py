"""
tools/definitions.py

Converte as tools do registry para o formato de cada provider LLM.
"""

from __future__ import annotations

from tools.registry import get_all_tools


def get_gemini_tools() -> list:
    """Retorna tools no formato google.genai para Gemini."""
    from google.genai import types

    declarations = []
    for tool_cls in get_all_tools().values():
        declarations.append(
            types.FunctionDeclaration(
                name=tool_cls.name,
                description=tool_cls.description,
                parameters=tool_cls.parameters,
            )
        )
    return [types.Tool(function_declarations=declarations)]


def get_openai_tools() -> list[dict]:
    """Retorna tools no formato OpenAI."""
    tools = []
    for tool_cls in get_all_tools().values():
        tools.append(
            {
                "type": "function",
                "function": {
                    "name": tool_cls.name,
                    "description": tool_cls.description,
                    "parameters": tool_cls.parameters,
                },
            }
        )
    return tools
