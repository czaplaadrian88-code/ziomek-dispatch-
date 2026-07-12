"""Kanoniczna, niezmienna koperta zdarzenia Ziomka (A360-E1).

Modul nie ma zegara ani generatora identyfikatorow. Produkcyjny call-site musi
utworzyc kopertę raz, z jawnym kluczem zdarzenia pochodzacym ze zrodla oraz z
jawnym czasem utworzenia/obserwacji koperty. Czas domenowy pickup/delivery jest
osobnym polem payloadu. Odtworzenie rekordu z bazy ma ten sam twardy
kontrakt: brak pola jest bledem, nigdy pretekstem do uzycia ``now()``.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional


ENVELOPE_VERSION = "order_event.v1"
IDENTITY_SCHEME = "source_event_key.v1"


class EnvelopeValidationError(ValueError):
    """Koperta nie spelnia trwalego kontraktu identity/time/schema."""


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise EnvelopeValidationError(f"{field} is required")
    if len(text) > 512:
        raise EnvelopeValidationError(f"{field} exceeds 512 characters")
    return text


def normalize_created_at(value: Any) -> str:
    """Waliduje jawny aware timestamp i normalizuje go do osi UTC.

    Celowo brak obslugi ``None``, naive datetime i wartosci pustych. Caller zna
    zrodlo czasu; ta warstwa nie moze go zgadywac.
    """
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise EnvelopeValidationError("created_at is not valid ISO-8601") from exc
    else:
        raise EnvelopeValidationError("created_at is required")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EnvelopeValidationError("created_at must include an explicit timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def canonical_payload_json(payload: Mapping[str, Any]) -> str:
    if not isinstance(payload, Mapping):
        raise EnvelopeValidationError("payload must be a mapping")
    try:
        return json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise EnvelopeValidationError("payload is not canonical JSON") from exc


def event_id_after_state_revision(
    base_event_id: Any,
    current_record: Mapping[str, Any],
) -> str:
    """Buduje source identity przejscia po jawnej durable rewizji stanu.

    Digest dotyczy klucza poprzedniego eventu, nie tresci nowego payloadu, a
    czytelny prefix nadal niesie order/type/source identity. Brak rewizji jest
    bramka migracji/backfillu, nie miejscem na timestamp ``now()``.
    """
    base = _required_text(base_event_id, "base_event_id")
    if not isinstance(current_record, Mapping):
        raise EnvelopeValidationError("current state record is required")
    revision = current_record.get("durable_last_event_order")
    if not isinstance(revision, Mapping):
        raise EnvelopeValidationError("durable state revision is required")
    previous_event_id = _required_text(revision.get("event_id"), "revision.event_id")
    previous_created_at = normalize_created_at(revision.get("created_at"))
    raw = "\x1f".join((previous_created_at, previous_event_id))
    token = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
    result = f"{base}:after:{token}"
    if len(result) > 512:
        raise EnvelopeValidationError("revision-qualified event_id exceeds 512 characters")
    return result


@dataclass(frozen=True)
class EventEnvelope:
    """Jedyna koperta uzywana przez DB, reducer, outbox i replay.

    ``producer_key`` jest jawnym kluczem z granicy producenta (np. stabilnym
    identyfikatorem przejscia panelu), a nie hashem tresci. Hash payloadu sluzy
    tylko do wykrycia konfliktu pod tym samym ``event_id``.
    """

    event_id: str
    event_type: str
    order_id: Optional[str]
    courier_id: Optional[str]
    payload_json: str
    created_at: str
    source: str
    envelope_version: str
    policy_version: str
    producer_key: str
    identity_scheme: str

    @classmethod
    def from_parts(
        cls,
        *,
        event_id: Any,
        event_type: Any,
        order_id: Any,
        courier_id: Any,
        payload: Mapping[str, Any],
        created_at: Any,
        source: Any,
        envelope_version: Any,
        policy_version: Any,
        producer_key: Any,
        identity_scheme: Any,
    ) -> "EventEnvelope":
        identity = _required_text(identity_scheme, "identity_scheme")
        if identity != IDENTITY_SCHEME:
            raise EnvelopeValidationError(
                f"unsupported identity_scheme: {identity}"
            )
        version = _required_text(envelope_version, "envelope_version")
        if version != ENVELOPE_VERSION:
            raise EnvelopeValidationError(
                f"unsupported envelope_version: {version}"
            )
        event_id_text = _required_text(event_id, "event_id")
        producer_key_text = _required_text(producer_key, "producer_key")
        payload_json = canonical_payload_json(payload)
        payload_digest = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        if (
            event_id_text.lower() == payload_digest
            and producer_key_text.lower() == payload_digest
        ):
            raise EnvelopeValidationError(
                "content hash cannot be the sole event identity"
            )
        return cls(
            event_id=event_id_text,
            event_type=_required_text(event_type, "event_type"),
            order_id=(str(order_id) if order_id is not None else None),
            courier_id=(str(courier_id) if courier_id not in (None, "") else None),
            payload_json=payload_json,
            created_at=normalize_created_at(created_at),
            source=_required_text(source, "source"),
            envelope_version=version,
            policy_version=_required_text(policy_version, "policy_version"),
            producer_key=producer_key_text,
            identity_scheme=identity,
        )

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "EventEnvelope":
        """Hydratuje dokladny rekord; brak pola nie ma fallbacku."""
        required = (
            "event_id",
            "event_type",
            "payload",
            "created_at",
            "source",
            "envelope_version",
            "policy_version",
            "producer_key",
            "identity_scheme",
        )
        missing = [name for name in required if name not in record]
        if missing:
            raise EnvelopeValidationError(
                "missing envelope fields: " + ",".join(sorted(missing))
            )
        raw_payload = record["payload"]
        if isinstance(raw_payload, str):
            try:
                payload = json.loads(raw_payload)
            except (TypeError, ValueError) as exc:
                raise EnvelopeValidationError("payload is not valid JSON") from exc
        else:
            payload = raw_payload
        return cls.from_parts(
            event_id=record["event_id"],
            event_type=record["event_type"],
            order_id=record.get("order_id"),
            courier_id=record.get("courier_id"),
            payload=payload,
            created_at=record["created_at"],
            source=record["source"],
            envelope_version=record["envelope_version"],
            policy_version=record["policy_version"],
            producer_key=record["producer_key"],
            identity_scheme=record["identity_scheme"],
        )

    @property
    def payload(self) -> dict[str, Any]:
        return json.loads(self.payload_json)

    @property
    def payload_sha256(self) -> str:
        return hashlib.sha256(self.payload_json.encode("utf-8")).hexdigest()

    @property
    def idempotency_key(self) -> str:
        identity = "\x1f".join(
            (self.identity_scheme, self.source, self.producer_key)
        )
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    def as_event(self) -> dict[str, Any]:
        """Kopia przekazywana bez zmian do reducera/replayu."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "order_id": self.order_id,
            "courier_id": self.courier_id,
            "payload": self.payload,
            "created_at": self.created_at,
            "source": self.source,
            "envelope_version": self.envelope_version,
            "policy_version": self.policy_version,
            "producer_key": self.producer_key,
            "identity_scheme": self.identity_scheme,
        }

    def canonical_json(self) -> str:
        return json.dumps(
            self.as_event(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )


__all__ = [
    "ENVELOPE_VERSION",
    "IDENTITY_SCHEME",
    "EnvelopeValidationError",
    "EventEnvelope",
    "canonical_payload_json",
    "event_id_after_state_revision",
    "normalize_created_at",
]
