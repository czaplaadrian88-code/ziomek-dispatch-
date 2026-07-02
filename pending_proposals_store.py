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

Współbieżność (L7.5, audyt 2.0 finding O1): `pending_proposals.json` ma potencjalnie
3 pisarzy (shadow_dispatcher przez ten moduł, telegram_approver po re-enable, postpone_sweeper).
Sam `os.replace` daje TYLKO brak torn-read (czytelnik nigdy nie widzi połówki), NIE serializuje
cykli read-modify-write → klasyczny lost-update. Kanon dostępu: KAŻDY pisarz przechodzi przez
`locked_upsert` / `locked_mutate` / `locked_save`, które trzymają `fcntl.LOCK_EX` na dedykowanym
lockfile przez CAŁY cykl load→merge→save (wzorzec `plan_manager._locked` / `state_machine._locked_write`).
Dedykowany lockfile przeżywa `os.replace` pliku pending. `load`/`save` pozostają prymitywami
BEZ locka (wołane wewnątrz locka) — NIE wołaj `locked_*` z wnętrza `mutate_fn` (zagnieżdżony
LOCK_EX na osobnym fd = deadlock).
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

PENDING_PATH = "/root/.openclaw/workspace/dispatch_state/pending_proposals.json"
DEFAULT_TTL_SEC = 1800  # 30 min — bezpiecznik czyszczenia (liveness realny = status 'planned')


def _lock_path(path: str) -> str:
    """Dedykowany lockfile obok pliku pending (przeżywa os.replace pending)."""
    return f"{path}.lock"


@contextmanager
def _locked(path: str = PENDING_PATH, exclusive: bool = True):
    """Guard fcntl na dedykowanym lockfile — obejmuje CAŁY cykl RMW pisarza.

    LOCK_EX serializuje wszystkich pisarzy (shadow/telegram/postpone); read wykonany
    WEWNĄTRZ locka widzi ostatni committed os.replace → brak lost-update. Wzorzec 1:1
    z plan_manager._locked / state_machine._locked_write."""
    lp = _lock_path(path)
    Path(lp).parent.mkdir(parents=True, exist_ok=True)
    fh = open(lp, "w")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


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
    """Atomowy zapis (unikalny tmp→fsync→os.replace). Prymityw BEZ locka — wołany
    WEWNĄTRZ `_locked`. Unikalny tmp (mkstemp) zamiast współdzielonego `{path}.tmp`
    eliminuje kolizję dwóch pisarzy na wspólnym pliku tymczasowym (audyt O1). Format
    JSON (indent=2, ensure_ascii=False) niezmieniony → czytelnicy wsteczni bez zmian."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(pending, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


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


def locked_mutate(mutate_fn: Callable[[Dict[str, Any]], Any], path: str = PENDING_PATH) -> Dict[str, Any]:
    """KANON read-modify-write pod LOCK_EX: load→mutate_fn(pending in-place)→save, cały
    cykl w jednym locku (brak lost-update między pisarzami). Zwraca zmutowany dict.
    `mutate_fn` mutuje dict w miejscu i NIE wywołuje żadnej z funkcji `locked_*`
    (zagnieżdżony LOCK_EX = deadlock). Wyjątki propagują — caller decyduje (fail-soft
    tam, gdzie tick nie może paść)."""
    with _locked(path):
        pending = load(path)
        mutate_fn(pending)
        save(pending, path)
    return pending


def locked_save(pending: Dict[str, Any], path: str = PENDING_PATH) -> None:
    """KANON blind-overwrite pod LOCK_EX (dla pisarza trzymającego pełny dict w pamięci,
    np. telegram_approver). Serializuje zapis względem innych pisarzy i eliminuje kolizję
    tmp. NIE robi merge — pisarz z nieświeżą pamięcią wciąż może nadpisać cudze wpisy;
    pełna eliminacja lost-update dla takiego pisarza = przejście na `locked_mutate`
    per-operację (patrz raport, checklist pre-re-enable Telegrama)."""
    with _locked(path):
        save(pending, path)


def upsert_proposals(
    upserts: Iterable[tuple],
    now: datetime,
    ttl_sec: int = DEFAULT_TTL_SEC,
    path: str = PENDING_PATH,
) -> int:
    """Jednorazowy (per tick) atomowy load→sweep→merge→save POD LOCK_EX (kanon L7.5).

    upserts: iterowalne (order_id, shadow_record) — TYLKO PROPOSE (caller filtruje).
    Zwraca liczbę wpisanych propozycji. Fail-soft: błąd → 0 (NIE wywala tick'a)."""
    ups = [(str(o), r) for (o, r) in upserts if o is not None and r is not None]
    if not ups:
        return 0
    try:
        with _locked(path):
            pending = sweep_expired(load(path), now)
            for oid, rec in ups:
                pending[oid] = build_entry(rec, now, ttl_sec)
            save(pending, path)
        return len(ups)
    except Exception:
        return 0


# Alias zgodny z rekomendacją audytu O1 ("locked_upsert").
locked_upsert = upsert_proposals
