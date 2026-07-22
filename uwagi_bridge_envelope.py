"""Authenticated envelope shared by the Epaka bridge and dispatch ingest.

The free-text ``add_uwagi`` field is an untrusted transport. A textual source
marker is not provenance. Version 2 appends a terminal HMAC-SHA256 that binds
three things at once: the exact UTF-8 payload, the source order id, and the
issue time (epoch). Producer-controlled fields are percent-escaped before
joining with ``|`` so a source field cannot inject an envelope segment.

Anti-replay (2026-07-22): the signed material carries ``oid`` (the stable
*source* order id — the ``#<order_id>`` the bridge already prints, since the
final gastro id is unknown when uwagi are built) and ``ts`` (issue epoch). The
consumer rejects an envelope whose signed ``oid`` is not the one literally
present in the content (``order_id_mismatch`` — internal consistency), whose
``oid`` disagrees with an externally supplied expected id when the caller has
one (``order_id_mismatch``), or that is older than a bounded window
(``envelope_expired``, default 24 h). This makes a captured envelope
non-eternal and makes any tamper of payload/oid/ts break the signature.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional


BRIDGE_ENVELOPE_VERSION = 2
BRIDGE_HMAC_PATH_ENV = "EPAKA_BRIDGE_HMAC_FILE"
DEFAULT_BRIDGE_HMAC_PATH = "/etc/ziomek/epaka_bridge_hmac"

# Freshness window: an authenticated envelope older (or implausibly newer) than
# this many seconds is rejected as ``envelope_expired``. Bounds how long a
# captured, still-signed envelope can be replayed. Overridable per host.
ENVELOPE_MAX_AGE_ENV = "EPAKA_BRIDGE_ENVELOPE_MAX_AGE_SECONDS"
DEFAULT_ENVELOPE_MAX_AGE_SECONDS = 24 * 3600
# Tolerated forward clock skew between producer and consumer hosts.
_FUTURE_SKEW_SECONDS = 300

# Order ids are numeric in this system, but keep a conservative safe charset so
# the marker stays unambiguously parseable and cannot smuggle ``;``/``|``/``=``.
_ORDER_ID_RE = re.compile(r"[0-9A-Za-z_-]+")
_SIGNATURE_PREFIX = f"SRC:EPAKA_BRIDGE:v{BRIDGE_ENVELOPE_VERSION};"
_SIGNATURE_RE = re.compile(
    r"SRC:EPAKA_BRIDGE:v(?P<version>\d+);"
    r"oid=(?P<oid>[0-9A-Za-z_-]+);"
    r"ts=(?P<ts>\d{1,20});"
    r"hmac-sha256=(?P<digest>[0-9a-f]{64})"
)
# Any token that merely *begins* with the source family (even malformed / old
# unsigned v1) counts as an envelope sighting so it fails closed, never legacy.
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
    order_id: Optional[str] = None
    issued_at: Optional[int] = None


def _resolve_max_age(max_age_seconds: Optional[float]) -> float:
    """Pick the freshness window: explicit arg, else env, else module default."""
    if max_age_seconds is not None:
        return float(max_age_seconds)
    raw = os.environ.get(ENVELOPE_MAX_AGE_ENV)
    if raw:
        try:
            parsed = float(raw)
            if parsed > 0:
                return parsed
        except (TypeError, ValueError):
            pass
    return float(DEFAULT_ENVELOPE_MAX_AGE_SECONDS)


def _signing_input(order_id: str, issued_at: int, payload: str) -> bytes:
    """Canonical, unambiguous bytes bound by the HMAC (domain-separated)."""
    return "\n".join(
        ("EPAKA_BRIDGE:v" + str(BRIDGE_ENVELOPE_VERSION),
         "oid=" + order_id,
         "ts=" + str(issued_at),
         payload)
    ).encode("utf-8")


def _coerce_order_id(order_id: object) -> str:
    """Normalise + validate an order id for the marker/signing charset."""
    oid = "" if order_id is None else str(order_id).strip()
    if not oid or not _ORDER_ID_RE.fullmatch(oid):
        raise ValueError(
            "bridge envelope order_id must be a non-empty [0-9A-Za-z_-] token"
        )
    return oid


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


def sign_bridge_envelope(
    payload: str,
    material: bytes,
    *,
    order_id: object,
    issued_at: object,
) -> str:
    """Append the terminal v2 marker binding payload + order id + issue time."""
    if not isinstance(material, bytes) or len(material) < 32:
        raise ValueError("bridge HMAC material must be at least 32 bytes")
    if not payload or payload.rstrip() != payload:
        raise ValueError("bridge envelope payload must be non-empty and canonical")
    oid = _coerce_order_id(order_id)
    try:
        ts = int(issued_at)
    except (TypeError, ValueError) as exc:
        raise ValueError("bridge envelope issued_at must be an epoch int") from exc
    if ts < 0:
        raise ValueError("bridge envelope issued_at must be non-negative")
    digest = hmac.new(
        material, _signing_input(oid, ts, payload), hashlib.sha256
    ).hexdigest()
    marker = (
        f"{_SIGNATURE_PREFIX}oid={oid};ts={ts};hmac-sha256={digest}"
    )
    return f"{payload} | {marker}"


def _order_id_in_payload(oid: str, payload: str) -> bool:
    """True iff the signed order id appears verbatim as a ``#<oid>`` token.

    Word-boundary aware so a signed ``#11`` does not spuriously match a
    content ``#111``.
    """
    pattern = re.compile("(?<![0-9A-Za-z_-])#" + re.escape(oid) + r"(?![0-9A-Za-z_-])")
    return bool(pattern.search(payload))


def verify_bridge_envelope(
    text: Optional[str],
    material: Optional[bytes],
    *,
    expected_order_id: object = None,
    now: Optional[float] = None,
    max_age_seconds: Optional[float] = None,
) -> EnvelopeVerification:
    """Authenticate one terminal v2 envelope without exposing its contents.

    ``expected_order_id`` (optional): when the caller independently knows the
    source order id it is processing, a disagreement with the signed id is a
    replay and returns ``order_id_mismatch``. When ``None`` the signed id is
    still pinned to the content via internal consistency.
    """
    if not text or not text.strip():
        return EnvelopeVerification(False, False, None, "empty_text")

    segments = text.split("|")
    family_versions = []
    signed_markers = []
    for index, segment in enumerate(segments):
        token = segment.strip()
        family = _SOURCE_FAMILY_RE.match(token)
        signed = _SIGNATURE_RE.fullmatch(token)
        if family:
            family_versions.append((index, int(family.group("version"))))
        if signed:
            signed_markers.append(
                (
                    index,
                    int(signed.group("version")),
                    signed.group("oid"),
                    int(signed.group("ts")),
                    signed.group("digest"),
                )
            )

    envelope_seen = bool(family_versions or signed_markers)
    if len(signed_markers) != 1:
        version = (signed_markers or family_versions or [(None, None)])[0][1]
        reason = "unsigned_source_marker" if family_versions and not signed_markers else (
            f"signature_marker_count:{len(signed_markers)}"
        )
        return EnvelopeVerification(False, envelope_seen, version, reason)

    marker_index, version, marker_oid, marker_ts, supplied_digest = signed_markers[0]
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
    expected = hmac.new(
        material, _signing_input(marker_oid, marker_ts, payload), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(supplied_digest, expected):
        return EnvelopeVerification(False, True, version, "hmac_mismatch")

    # Signature authentic — now bind the authenticated identity to *this* order.
    # (a) internal consistency: the signed id must be the one shown in content.
    if not _order_id_in_payload(marker_oid, payload):
        return EnvelopeVerification(
            False, True, version, "order_id_mismatch", None, marker_oid, marker_ts
        )
    # (b) external binding: caller's expected id, when known, must agree.
    if expected_order_id is not None and str(expected_order_id).strip() != marker_oid:
        return EnvelopeVerification(
            False, True, version, "order_id_mismatch", None, marker_oid, marker_ts
        )
    # (c) freshness: bound how long a captured, still-signed envelope lives.
    current = time.time() if now is None else float(now)
    age = current - marker_ts
    if age > _resolve_max_age(max_age_seconds) or age < -_FUTURE_SKEW_SECONDS:
        return EnvelopeVerification(
            False, True, version, "envelope_expired", None, marker_oid, marker_ts
        )

    return EnvelopeVerification(
        True, True, version, "authenticated_v2", payload, marker_oid, marker_ts
    )


def build_verbose_uwagi_envelope(
    company: Mapping[str, object],
    order_id: object,
    detail: Mapping[str, object],
    material: bytes,
    *,
    issued_at: Optional[int] = None,
) -> str:
    """Canonical replacement for ``drtusz_bridge.bridge.build_uwagi`` verbose mode.

    ``order_id`` is the stable *source* order id; it is printed as ``#<oid>``
    and bound into the signature. ``issued_at`` defaults to the current epoch.
    """
    oid = _coerce_order_id(order_id)
    ts = int(time.time()) if issued_at is None else int(issued_at)
    field = escape_bridge_field
    parts = [f"{field(company.get('name', ''))} #{oid}"]
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
    return sign_bridge_envelope(
        " | ".join(parts), material, order_id=oid, issued_at=ts
    )


def build_bridge_uwagi(
    company: Mapping[str, object],
    order_id: object,
    detail: Mapping[str, object],
    legacy_verbose_builder,
    *,
    logger=None,
    issued_at: Optional[int] = None,
) -> str:
    """Producer fail-safe entry for verbose firms.

    Emit the authenticated v2 envelope when the 0600 HMAC secret is present.
    If the secret is missing/unreadable, fall back to ``legacy_verbose_builder``
    (the bridge's own byte-identical legacy verbose uwagi) instead of raising —
    order creation must never break. With the v2 consumer enabled, a legacy /
    unsigned envelope fails closed to KOORD, which is safe.
    """
    try:
        material = load_bridge_hmac()
    except BridgeCredentialError as exc:
        if logger is not None:
            logger.warning(
                "EPAKA bridge HMAC unavailable (%s) — building legacy verbose "
                "uwagi; v2 consumer fails closed to KOORD",
                type(exc).__name__,
            )
        return legacy_verbose_builder()
    return build_verbose_uwagi_envelope(
        company, order_id, detail, material, issued_at=issued_at
    )


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
