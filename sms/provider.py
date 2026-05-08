"""SMS provider abstract interface + factory (MP-#9, 2026-05-08).

`SMSProvider.send(message, recipient)` is single-call API. Implementations:
  - `dispatch_v2.sms.ovh.OVHSMSProvider` — production OVH SMS API
  - `dispatch_v2.sms.stub.StubSMSProvider` — dev/test, logs to file only

Factory `get_provider()` reads env `SMS_PROVIDER` (default 'stub'); future
providers (e.g. Twilio fallback) added via subclass + factory branch.

Design constraints:
  - send() returns True/False — caller logs/retries.
  - Exceptions klasyfikowane jako SMSDeliveryError (catchable specifically).
  - NO retry policy w provider — caller (tg_heartbeat) decyduje.
  - NO provider state — stateless functions (cache invalidation simpler).

Per master plan: 2nd tiny serwis dispatch-tg-heartbeat.timer co 60s `getMe`;
≥3× fail → SMS Adrian. Eliminuje chicken-egg outage.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Optional


class SMSDeliveryError(Exception):
    """Raised by SMSProvider.send() when delivery cannot be attempted or fails.

    Carries optional `provider`, `status_code`, `body` dla diagnostics.
    """

    def __init__(self, message: str, *, provider: str = "?", status_code: Optional[int] = None, body: Optional[str] = None):
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code
        self.body = body


class SMSProvider(ABC):
    """Abstract base for SMS providers. Single-call API."""

    name: str = "abstract"

    @abstractmethod
    def send(self, message: str, recipient: str) -> bool:
        """Send `message` to `recipient` (E.164 format, e.g. +48123456789).

        Returns True on accepted-by-provider (NIE delivered-to-handset; SMS is
        best-effort async). Returns False on retryable infra fail. Raises
        SMSDeliveryError na unrecoverable misconfiguration (np. brak creds).

        Implementation MUST NOT block longer than ~10s (heartbeat caller czeka
        synchronously). Use timeout w urllib/requests.
        """
        raise NotImplementedError

    def is_configured(self) -> bool:
        """True jeśli provider ma wszystkie wymagane creds dostępne.

        Default returns True; override in concrete providers checking env vars.
        Caller (heartbeat) może log warning gdy not configured — fall back na
        stub or skip SMS path.
        """
        return True


def get_provider(provider_name: Optional[str] = None) -> SMSProvider:
    """Factory: instantiate SMS provider by name.

    Args:
        provider_name: 'ovh' | 'stub' | None. None reads env `SMS_PROVIDER`
                       (default 'stub' — safe dev default).

    Raises:
        ValueError: unknown provider name.
    """
    name = (provider_name or os.environ.get("SMS_PROVIDER") or "stub").strip().lower()

    if name == "ovh":
        from dispatch_v2.sms.ovh import OVHSMSProvider
        return OVHSMSProvider()
    if name == "stub":
        from dispatch_v2.sms.stub import StubSMSProvider
        return StubSMSProvider()
    raise ValueError(f"unknown SMS_PROVIDER={name!r}; supported: ovh, stub")
