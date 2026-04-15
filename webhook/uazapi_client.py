"""
webhook/uazapi_client.py

Cliente HTTP fino para a API uazapi.dev.

Endpoints usados (todos POST, com header ``token: <instance_token>`` exceto os
administrativos que usam ``admintoken: <UAZAPI_ADMIN_TOKEN>``):

- ``POST /instance/init``       → cria uma nova instância, retorna ``token``
- ``POST /instance/connect``    → inicia conexão e retorna QR code base64
- ``GET  /instance/status``     → consulta status da conexão
- ``POST /webhook``             → define URL do webhook da instância
- ``POST /send/text``           → envia mensagem de texto
- ``POST /message/download``    → baixa mídia (áudio, imagem, etc.)

A URL base é configurada por ``UAZAPI_BASE_URL`` (default: ``https://free.uazapi.com``).
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://free.uazapi.com"
DEFAULT_TIMEOUT = 30


def _base_url() -> str:
    return os.environ.get("UAZAPI_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _admin_token() -> str:
    return os.environ.get("UAZAPI_ADMIN_TOKEN", "")


class UazapiError(RuntimeError):
    """Erro retornado pela API uazapi."""


def _request(
    method: str,
    path: str,
    *,
    token: str | None = None,
    use_admin_token: bool = False,
    json: dict[str, Any] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    url = f"{_base_url()}{path}"
    headers = {"Content-Type": "application/json"}
    if use_admin_token:
        admin = _admin_token()
        if not admin:
            raise UazapiError(
                "UAZAPI_ADMIN_TOKEN não configurado — necessário para criar instâncias."
            )
        headers["admintoken"] = admin
    elif token:
        headers["token"] = token

    logger.debug("uazapi %s %s", method, url)
    try:
        resp = requests.request(
            method, url, headers=headers, json=json, timeout=timeout
        )
    except requests.RequestException as exc:
        raise UazapiError(f"Falha de rede ao chamar {url}: {exc}") from exc

    if resp.status_code >= 400:
        raise UazapiError(
            f"uazapi {method} {path} retornou {resp.status_code}: {resp.text[:500]}"
        )
    try:
        return resp.json()
    except ValueError:
        return {"raw": resp.text}


def init_instance(name: str) -> dict[str, Any]:
    """Cria uma nova instância. Requer ``UAZAPI_ADMIN_TOKEN``.

    Retorna o payload com ``token`` (usado nas chamadas subsequentes) e
    ``instance.id``.
    """
    return _request(
        "POST",
        "/instance/init",
        use_admin_token=True,
        json={"name": name},
    )


def connect_instance(token: str, phone: str | None = None) -> dict[str, Any]:
    """Inicia a conexão da instância. Retorna QR code base64 em ``qrcode``.

    Se ``phone`` for fornecido, tenta conectar via pareamento por código
    (pairing code) em vez de QR.
    """
    body: dict[str, Any] = {}
    if phone:
        body["phone"] = phone
    return _request("POST", "/instance/connect", token=token, json=body)


def instance_status(token: str) -> dict[str, Any]:
    """Consulta o status da instância (``connected``, ``disconnected``, etc.)."""
    return _request("GET", "/instance/status", token=token)


def set_webhook(
    token: str,
    webhook_url: str,
    events: list[str] | None = None,
) -> dict[str, Any]:
    """Define a URL de webhook da instância."""
    body: dict[str, Any] = {
        "url": webhook_url,
        "enabled": True,
    }
    if events:
        body["events"] = events
    return _request("POST", "/webhook", token=token, json=body)


def send_text(token: str, number: str, text: str) -> dict[str, Any]:
    """Envia mensagem de texto. ``number`` apenas com dígitos (ex: ``5551999990000``)."""
    return _request(
        "POST",
        "/send/text",
        token=token,
        json={"number": number, "text": text},
    )


def download_media(token: str, message_id: str) -> dict[str, Any]:
    """Baixa mídia anexada a uma mensagem. Retorna ``fileContent`` em base64."""
    return _request(
        "POST",
        "/message/download",
        token=token,
        json={"id": message_id},
    )
