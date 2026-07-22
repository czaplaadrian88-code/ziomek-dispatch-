"""Authenticated envelope shared by the Epaka bridge and dispatch ingest.

The free-text ``add_uwagi`` field is an untrusted transport. A textual source
marker is not provenance. Version 2 appends a terminal HMAC-SHA256 over the
exact UTF-8 payload. Producer-controlled fields are percent-escaped before
joining with ``|`` so a source field cannot inject an envelope segment.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional


BRIDGE_ENVELOPE_VERSION = 2
BRIDGE_HMAC_PATH_ENV = "EPAKA_BRIDGE_HMAC_FILE"
DEFAULT_BRIDGE_HMAC_PATH = "/etc/ziomek/epaka_bridge_hmac"
_SIGNATURE_PREFIX = f"SRC:EPAKA_BRIDGE:v{BRIDGE_ENVELOPE_VERSION};hmac-sha256="
_SIGNATURE_RE = re.compile(
    r"SRC:EPAKA_BRIDGE:v(?P<version>\d+);hmac-sha256=(?P<digest>[0-9a-f]{64})"
)
_SOURCE_FAMILY_RE = re.compile(r"SRC:EPAKA_BRIDGE:v(?P<version>\d+)")
_FIELD_ESCAPES = (("%", "%25"), ("|", "%7C"), ("\r", "%0D"), ("\n", "%0A"))


class BridgeCredentialError(RuntimeError):
    """The HMAC material cannot be proven to be a private regular file."""


@dataclass(frozen=True)
class EnvelopeVerification:
    authenticated: bool
    envelope_seen: bool
    version: Optional[int]
    reason: str
    payload: Optional[str] = None


def escape_bridge_field(value: object) -> str:
    """Escape transport separators in one producer-controlled field."""
    escaped = "" if value is None else str(value)
    for raw, replacement in _FIELD_ESCAPES:
        escaped = escaped.replace(raw, replacement)
    return escaped


def load_bridge_hmac(path: str | os.PathLike | None = None) -> bytes:
    """Read HMAC material fail-closed from a regular, non-symlink 0600 file."""
    hmac_path = Path(path or os.environ.get(BRIDGE_HMAC_PATH_ENV) or DEFAULT_BRIDGE_HMAC_PATH)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(hmac_path, flags)
    except OSError as exc:
        raise BridgeCredentialError(f"bridge HMAC unavailable: {type(exc).__name__}") from exc
    try:
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode):
            raise BridgeCredentialError("bridge HMAC source is not a regular file")
        if stat.S_IMODE(meta.st_mode) != 0o600:
            raise BridgeCredentialError("bridge HMAC source mode must be exactly 0600")
        if meta.st_size < 32 or meta.st_size > 4096:
            raise BridgeCredentialError("bridge HMAC source size must be 32..4096 bytes")
        chunks = []
        while True:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(fd)
    material = b"".join(chunks)
    if len(material) < 32:
        raise BridgeCredentialError("bridge HMAC source must contain at least 32 bytes")
    return material


def sign_bridge_envelope(payload: str, material: bytes) -> str:
    """Append the terminal v2 marker bound to the exact payload bytes."""
    if not isinstance(material, bytes) or len(material) < 32:
        raise ValueError("bridge HMAC material must be at least 32 bytes")
    if not payload or payload.rstrip() != payload:
        raise ValueError("bridge envelope payload must be non-empty and canonical")
    digest = hmac.new(material, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload} | {_SIGNATURE_PREFIX}{digest}"


def verify_bridge_envelope(
    text: Optional[str], material: Optional[bytes]
) -> EnvelopeVerification:
    """Authenticate one terminal v2 envelope without exposing its contents."""
    if not text or not text.strip():
        return EnvelopeVerification(False, False, None, "empty_text")

    segments = text.split("|")
    family_versions = []
    signed_markers = []
    for index, segment in enumerate(segments):
        token = segment.strip()
        family = _SOURCE_FAMILY_RE.fullmatch(token)
        signed = _SIGNATURE_RE.fullmatch(token)
        if family:
            family_versions.append((index, int(family.group("version"))))
        if signed:
            signed_markers.append(
                (index, int(signed.group("version")), signed.group("digest"))
            )

    envelope_seen = bool(family_versions or signed_markers)
    if len(signed_markers) != 1:
        version = (signed_markers or family_versions or [(None, None)])[0][1]
        reason = "unsigned_source_marker" if family_versions and not signed_markers else (
            f"signature_marker_count:{len(signed_markers)}"
        )
        return EnvelopeVerification(False, envelope_seen, version, reason)

    marker_index, version, supplied_digest = signed_markers[0]
    nonempty_indices = [i for i, segment in enumerate(segments) if segment.strip()]
    if marker_index == 0 or marker_index != nonempty_indices[-1]:
        return EnvelopeVerification(False, True, version, "signature_marker_not_terminal")
    if version != BRIDGE_ENVELOPE_VERSION:
        return EnvelopeVerification(False, True, version, f"unsupported_source_version:v{version}")
    if material is None:
        return EnvelopeVerification(False, True, version, "hmac_unavailable")
    if not isinstance(material, bytes) or len(material) < 32:
        return EnvelopeVerification(False, True, version, "hmac_invalid")

    separator = " | " + segments[marker_index].strip()
    if not text.endswith(separator):
        return EnvelopeVerification(False, True, version, "signature_boundary_noncanonical")
    payload = text[: -len(separator)]
    expected = hmac.new(material, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied_digest, expected):
        return EnvelopeVerification(False, True, version, "hmac_mismatch")
    return EnvelopeVerification(True, True, version, "authenticated_v2", payload)


def build_verbose_uwagi_envelope(
    company: Mapping[str, object],
    order_id: object,
    detail: Mapping[str, object],
    material: bytes,
) -> str:
    """Canonical replacement for ``drtusz_bridge.bridge.build_uwagi`` verbose mode."""
    field = escape_bridge_field
    parts = [f"{field(company.get('name', ''))} #{field(order_id)}"]
    sender = detail.get("sender") or {}
    if not isinstance(sender, Mapping):
        sender = {}

    sender_name = " ".join(
        field(value) for value in (sender.get("name"), sender.get("lastname")) if value
    ).strip()
    sender_head = [sender_name] if sender_name else []
    if sender.get("phone"):
        sender_head.append(f"tel {field(sender['phone'])}")
    if sender_head:
        parts.append("NADAWCA: " + " ".join(sender_head))

    sender_meta = []
    if sender.get("company"):
        sender_meta.append(field(sender["company"]))
    if sender.get("invoice_nip"):
        sender_meta.append(f"NIP {field(sender['invoice_nip'])}")
    address = []
    if sender.get("street"):
        address.append(field(sender["street"]))
    post_city = " ".join(
        field(value) for value in (sender.get("post_code"), sender.get("city")) if value
    )
    if post_city:
        address.append(post_city)
    if address:
        sender_meta.append(", ".join(address))
    if sender.get("email"):
        sender_meta.append(field(sender["email"]))
    if sender_meta:
        parts.append(", ".join(sender_meta))

    recipient = " ".join(
        field(value) for value in (detail.get("name"), detail.get("lastname")) if value
    ).strip()
    if recipient:
        parts.append(f"Odbiorca: {recipient}")
    if detail.get("company"):
        parts.append(field(detail["company"]))
    if detail.get("address"):
        parts.append(f"oryg. adres: {field(detail['address'])}")
    if detail.get("czas_odbioru_okno"):
        parts.append(f"Okno odbioru: {field(detail['czas_odbioru_okno'])}")
    if detail.get("czas_doreczenia_okno"):
        parts.append(f"Okno doreczenia: {field(detail['czas_doreczenia_okno'])}")
    if detail.get("ilosc_paczek"):
        parts.append(f"Paczek: {field(detail['ilosc_paczek'])}")
    return sign_bridge_envelope(" | ".join(parts), material)


def bridge_envelope_was_rejected(order: Mapping[str, object]) -> bool:
    """Return the persisted fail-closed decision shared by downstream twins.

    The marker is written only by the bridge-enabled ingest path.  Its absence
    therefore preserves the legacy/OFF behaviour byte-for-byte.
    """
    audit = order.get("uwagi_pickup_parsed")
    return bool(
        isinstance(audit, Mapping)
        and audit.get("bridge_envelope_rejected") is True
    )
