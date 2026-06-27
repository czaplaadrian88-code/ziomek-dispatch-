"""pending_proposals_store — zasilanie `pending_proposals.json` z silnika (Opcja B).

Telegram (`telegram_approver`) był jedynym pisarzem `pending_proposals.json`; po jego
wyłączeniu (26.06, propozycje w konsoli koordynatora) plik osierociał (pusty) → cisi
konsumenci straciły dane:
  - `panel_watcher._save_plan_from_pending` (zapas zapisu kanonu trasy po przypisaniu),
  - `panel_watcher` wykrywanie PANEL_OVERRIDE,
  - `tools/pending_global_resweep` (pomiar globalnej re-alokacji — źródło wiszących),
  - fundament Fazy C (globalna alokacja musi mieć żywą listę wiszących).

Ten moduł odtwarza ZAPIS 1:1 ze schematem telegram_approver (decision_record = rekord
shadow), tylko BEZ wysyłania (message_id=None). Pisarz: `shadow_dispatcher` (jedyny po
wyłączeniu Telegrama/postpone → brak wyścigu pisarzy; os.replace atomowy → czytelnicy
nigdy nie widzą połowicznego pliku). Flag-gated w callerze (default OFF = no-op).

panel_watcher usuwa wpis po przypisaniu (pop on ASSIGN); tu dokładamy sweep wygasłych,
żeby plik nie puchł dla zleceń nigdy-nieprzypisanych.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

PENDING_PATH = "/root/.openclaw/workspace/dispatch_state/pending_proposals.json"
DEFAULT_TTL_SEC = 1800  # 30 min — bezpiecznik czyszczenia (liveness realny = status 'planned')


def load(path: str = PENDING_PATH) -> Dict[str, Any]:
    """Wczytaj pending (fail-soft → {})."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def save(pending: Dict[str, Any], path: str = PENDING_PATH) -> None:
    """Atomowy zapis (tmp→fsync→os.replace) — identyczny ze schematem telegram_approver."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(pending, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _parse_iso(s: Any) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def sweep_expired(pending: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    """Usuń wpisy z expires_at < now (bezpiecznik; brak/zły expires_at = zostaw)."""
    out = {}
    for oid, rec in pending.items():
        exp = _parse_iso((rec or {}).get("expires_at"))
        if exp is not None and exp < now:
            continue
        out[oid] = rec
    return out


def build_entry(record: Dict[str, Any], now: datetime, ttl_sec: int = DEFAULT_TTL_SEC) -> Dict[str, Any]:
    """Wpis w schemacie telegram_approver: {message_id, sent_at, expires_at, decision_record}.
    message_id=None (brak Telegrama). decision_record = rekord shadow (ten z shadow_decisions)."""
    from datetime import timedelta
    return {
        "message_id": None,
        "sent_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=ttl_sec)).isoformat(),
        "decision_record": record,
    }


def upsert_proposals(
    upserts: Iterable[tuple],
    now: datetime,
    ttl_sec: int = DEFAULT_TTL_SEC,
    path: str = PENDING_PATH,
) -> int:
    """Jednorazowy (per tick) atomowy load→sweep→merge→save.

    upserts: iterowalne (order_id, shadow_record) — TYLKO PROPOSE (caller filtruje).
    Zwraca liczbę wpisanych propozycji. Fail-soft: błąd → 0 (NIE wywala tick'a)."""
    ups = [(str(o), r) for (o, r) in upserts if o is not None and r is not None]
    if not ups:
        return 0
    try:
        pending = sweep_expired(load(path), now)
        for oid, rec in ups:
            pending[oid] = build_entry(rec, now, ttl_sec)
        save(pending, path)
        return len(ups)
    except Exception:
        return 0
