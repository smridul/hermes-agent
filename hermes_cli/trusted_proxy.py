"""Trusted proxy helpers for the Hermes dashboard."""

from __future__ import annotations

import os
from dataclasses import dataclass

from fastapi import Request
from fastapi.responses import HTMLResponse


@dataclass(frozen=True)
class TrustedProxySettings:
    header: str
    value: str


def _settings() -> TrustedProxySettings:
    return TrustedProxySettings(
        header=os.environ.get("HERMES_TRUSTED_PROXY_HEADER", ""),
        value=os.environ.get("HERMES_TRUSTED_PROXY_VALUE", ""),
    )


def is_trusted_proxy_protection_enabled() -> bool:
    settings = _settings()
    return bool(settings.header and settings.value)


def has_valid_trusted_proxy_header(request: Request) -> bool:
    settings = _settings()
    if not (settings.header and settings.value):
        return True
    return request.headers.get(settings.header, "") == settings.value


def trusted_proxy_unauthorized_response() -> HTMLResponse:
    return HTMLResponse("Unauthorized", status_code=401)
