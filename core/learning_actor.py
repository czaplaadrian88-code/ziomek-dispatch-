"""Kanoniczna, PII-free tożsamość operatora dla strumienia learning.

Jeden helper jest współdzielony przez writer A-3 i offline extractor. Surowa
tożsamość służy wyłącznie do walidacji + jednokierunkowego SHA-256 i nigdy nie
jest zwracana.
"""
from __future__ import annotations

import hashlib
import os
import shlex
from typing import Mapping, Optional

ALLOWED_ACTOR_DOMAINS = frozenset({"nadajesz.pl"})
FILTERED_ACTOR_IDENTITIES = frozenset({
    "",
    "t",
    "test@op",
    "test@nadajesz.pl",
    "admin@ziomek.pl",
})
FILTERED_LOCAL_PARTS = frozenset({"admin", "test"})


def actor_status(value: object) -> tuple[str, Optional[str]]:
    """Zwróć ``(attested, pseudonym)`` albo fail-closed ``(filtered, None)``."""
    normalized = str(value or "").strip().casefold()
    if normalized in FILTERED_ACTOR_IDENTITIES or normalized.count("@") != 1:
        return "filtered", None
    local, domain = normalized.split("@", 1)
    if not local or domain not in ALLOWED_ACTOR_DOMAINS:
        return "filtered", None
    if local in FILTERED_LOCAL_PARTS or local.startswith("test"):
        return "filtered", None
    pseudonym = "actor_sha256:" + hashlib.sha256(
        normalized.encode("utf-8")
    ).hexdigest()[:16]
    return "attested", pseudonym


def legacy_live_assign_signature(record: Mapping) -> bool:
    """Wąski podpis obecnego writera konsoli sprzed pola ``kind=assign``."""
    if record.get("mode") != "live":
        return False
    if record.get("kind") not in (None, ""):
        return False
    required = ("actor", "command", "courier", "order_id", "ts")
    if any(key not in record for key in required):
        return False
    command = record.get("command")
    if not isinstance(command, str):
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    basenames = {os.path.basename(token) for token in tokens[:4]}
    return "gastro_assign.py" in basenames


def effective_live_assign(record: Mapping) -> Optional[str]:
    """Zwróć atestowany schemat wykonanego assignu albo ``None``.

    ``kind=assign`` jest docelowym kontraktem. Legacy jest dopuszczony tylko
    po pełnym podpisie command/courier oraz jawnym sukcesie ``ok=true, rc=0``.
    """
    if record.get("mode") != "live":
        return None
    failed = record.get("ok") is False or record.get("rc") not in (None, 0)
    if record.get("kind") == "assign":
        return None if failed else "kind_assign"
    if not legacy_live_assign_signature(record):
        return None
    if record.get("ok") is not True or record.get("rc") != 0:
        return None
    return "legacy_gastro_assign_signature"
