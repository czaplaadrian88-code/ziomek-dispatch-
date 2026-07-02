#!/usr/bin/env python3
"""observability/log_rotation.py — retencja dziennych logów observability (GC).

KONTEKST (audyt 2.0 pas L13, 2026-07-02)
----------------------------------------
Ten moduł był USUNIĘTY 2026-06-11 (commit b39e928) z założeniem „rotacja =
systemowy logrotate". Założenie było BŁĘDNE: logrotate rotuje pliki o STAŁEJ
nazwie wg rozmiaru/copytruncate — NIE potrafi rotować plików DATOWANYCH
(`candidate_decisions_YYYYMMDD.jsonl` / `fleet_filter_YYYYMMDD.jsonl`), bo każda
doba to NOWA nazwa. Efekt zmierzony 02.07: 326 MB / 120 plików narastało bez
końca (88 starszych niż 14 dni, sięgają 2026-05-04, ~18 MB/dobę).

Komentarz w `/etc/logrotate.d/dispatch-v2` (l.113-116) JAWNIE deleguje te pliki
do tego modułu: „NIE rotate logrotate-em — są już daily rolled przez
log_rotation.py (retention 14d via cron run)". Ten plik przywraca ten kontrakt.

BEZPIECZEŃSTWO — DWIE WARSTWY, DENYLIST ZAWSZE WYGRYWA
-----------------------------------------------------
1. DENYLIST (sprawdzany PIERWSZY, wygrywa nad allowlistą): ledgery / prawda /
   stan / źródła replayów. NIGDY nie tykać — mają własną rotację.
2. ALLOWLIST (jawne wzorce): TYLKO datowane dzienniki observability.
Plik nietrafiony przez żaden wzorzec = NIETYKANY (klasa UNMATCHED).

Domyślnie `--dry-run` (nic nie kasuje — tylko raport). Realne kasowanie wymaga
jawnego `--apply`. `--max-delete N` = bezpiecznik przed runaway. Wiek liczony z
mtime (dzienniki są dopisywane w swojej dobie, potem nietknięte → mtime = dzień
logu). Każda decyzja logowana.

CLI
---
    python -m dispatch_v2.observability.log_rotation                 # dry-run (default)
    python -m dispatch_v2.observability.log_rotation --apply         # realne (off-peak/cron)
    python -m dispatch_v2.observability.log_rotation --retention-days 14 --dir /ścieżka
    python -m dispatch_v2.observability.log_rotation --max-delete 500

Cron-safe: exit 0 na każdej normalnej ścieżce (także brak katalogu). Non-zero =
realny błąd wykonania.
"""
from __future__ import annotations

import argparse
import fnmatch
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── Domyślne parametry ──────────────────────────────────────────────────────
# Ścieżka = ta sama co CandidateLogger.DEFAULT_LOG_DIR (candidate_logger.py:26).
DEFAULT_LOG_DIR = Path("/root/.openclaw/workspace/dispatch_state/observability")
DEFAULT_RETENTION_DAYS = 14            # kontrakt z logrotate l.115 + __init__.py:16
DEFAULT_MAX_DELETE = 500              # bezpiecznik; realnie ~88 plików do skasowania

_log = logging.getLogger("observability.log_rotation")

# ── ALLOWLIST — TYLKO datowane dzienniki observability ──────────────────────
# Prefiksy pochodzą wprost z candidate_logger.py:
#   CANDIDATE_LOG_PREFIX     = "candidate_decisions"  → candidate_decisions_YYYYMMDD.jsonl
#   FLEET_FILTER_LOG_PREFIX  = "fleet_filter"         → fleet_filter_YYYYMMDD.jsonl
# Recon L13 (02.07): to JEDYNE dwa wzorce w katalogu (60 + 60 plików).
# Wymagana 8-cyfrowa data w nazwie — plik bez daty NIGDY nie pasuje (bezpieczne).
# Sufiks rotacji (.1/.gz) tolerowany defensywnie (te pliki nie są logrotowane, ale
# gdyby kiedyś ktoś je objął — retencja i tak działa spójnie).
ALLOWLIST_REGEXES: List[re.Pattern] = [
    re.compile(r"^candidate_decisions_\d{8}\.jsonl(\.\d+)?(\.gz)?$"),
    re.compile(r"^fleet_filter_\d{8}\.jsonl(\.\d+)?(\.gz)?$"),
]

# ── DENYLIST — ledgery/prawda/stan/replaye; NIGDY nie kasować ────────────────
# Sprawdzany PRZED allowlistą i wygrywa zawsze (patrz classify()). Nawet gdyby plik
# pasował wiekiem i wzorcem allowlisty, denylist go ochrania. glob (fnmatch).
DENYLIST_GLOBS: Tuple[str, ...] = (
    "shadow_decisions*",     # główny ledger decyzji (źródło replayów/ML)
    "decision_outcomes*",    # ground-truth outcomes
    "gps_delivery_truth*",   # prawda o dostawach
    "sla_log*",              # ledger SLA / R6
    "orders_state*",         # stan zleceń
    "courier_plans*",        # zapisane plany tras
    "courier_last_pos*",     # last-known-pos store
    "pending_proposals*",    # kolejka propozycji
    "*.db",                  # jakakolwiek baza (events.db itd.) — własna rotacja
    "*.py",                  # kod
)


def _match_denylist(name: str) -> Optional[str]:
    """Zwraca dopasowany wzorzec denylisty albo None."""
    for pat in DENYLIST_GLOBS:
        if fnmatch.fnmatch(name, pat):
            return pat
    return None


def _match_allowlist(name: str) -> Optional[str]:
    """Zwraca dopasowany wzorzec allowlisty (repr) albo None."""
    for rx in ALLOWLIST_REGEXES:
        if rx.match(name):
            return rx.pattern
    return None


def classify(name: str) -> Tuple[str, Optional[str]]:
    """Klasyfikuje plik → ('DENY'|'ALLOW'|'UNMATCHED', dopasowany_wzorzec).

    DENYLIST sprawdzany PIERWSZY i WYGRYWA — plik chroniony denylistą nigdy nie
    trafi do kasowania, nawet gdy pasuje do allowlisty i jest stary.
    """
    denied = _match_denylist(name)
    if denied is not None:
        return "DENY", denied
    allowed = _match_allowlist(name)
    if allowed is not None:
        return "ALLOW", allowed
    return "UNMATCHED", None


def scan(
    log_dir: Path,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    now: Optional[float] = None,
) -> Dict[str, object]:
    """Skanuje katalog i klasyfikuje pliki. Czysta funkcja — ZERO kasowania.

    Wiek = (now - mtime). Do kasowania kwalifikuje się plik ALLOW starszy niż
    retention_days (age_days > retention_days; równo N dni = zachowany).

    Zwraca dict:
      to_delete : [(path, mtime, size)]  — ALLOW + starsze niż retention (oldest-first)
      kept      : [(path, mtime, size)]  — ALLOW w oknie retencji (zachowane)
      denied    : [(path, pattern)]      — chronione denylistą
      unmatched : [path]                 — nietrafione żadnym wzorcem (nietykane)
      cutoff_ts : float                  — próg mtime (poniżej = do kasowania)
    """
    if now is None:
        now = time.time()
    cutoff_ts = now - retention_days * 86400.0

    to_delete: List[Tuple[Path, float, int]] = []
    kept: List[Tuple[Path, float, int]] = []
    denied: List[Tuple[Path, str]] = []
    unmatched: List[Path] = []

    log_dir = Path(log_dir)
    if not log_dir.is_dir():
        return {
            "to_delete": to_delete, "kept": kept, "denied": denied,
            "unmatched": unmatched, "cutoff_ts": cutoff_ts,
            "dir_missing": True,
        }

    for entry in sorted(log_dir.iterdir()):
        if not entry.is_file():
            continue
        name = entry.name
        cls, pat = classify(name)
        if cls == "DENY":
            denied.append((entry, pat or ""))
            continue
        if cls == "UNMATCHED":
            unmatched.append(entry)
            continue
        # cls == "ALLOW"
        try:
            st = entry.stat()
        except OSError:
            unmatched.append(entry)
            continue
        mtime = st.st_mtime
        size = st.st_size
        is_old = mtime < cutoff_ts          # ← warunek wieku (cel mutation-check #1)
        if is_old:
            to_delete.append((entry, mtime, size))
        else:
            kept.append((entry, mtime, size))

    to_delete.sort(key=lambda t: t[1])       # oldest-first (deterministycznie)
    kept.sort(key=lambda t: t[1])
    return {
        "to_delete": to_delete, "kept": kept, "denied": denied,
        "unmatched": unmatched, "cutoff_ts": cutoff_ts, "dir_missing": False,
    }


def run(
    log_dir: Path = DEFAULT_LOG_DIR,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    apply: bool = False,
    max_delete: int = DEFAULT_MAX_DELETE,
    now: Optional[float] = None,
) -> Dict[str, object]:
    """Wykonuje retencję. Domyślnie apply=False → NIC nie kasuje (tylko raport).

    Zwraca dict z podsumowaniem (m.in. deleted_count, freed_bytes, capped,
    oldest_kept). W trybie --apply: NAJPIERW wypisuje plan, POTEM kasuje (unlink),
    z twardym limitem max_delete i logiem każdej decyzji.
    """
    res = scan(log_dir, retention_days=retention_days, now=now)
    to_delete: List[Tuple[Path, float, int]] = res["to_delete"]        # type: ignore
    kept: List[Tuple[Path, float, int]] = res["kept"]                  # type: ignore
    denied = res["denied"]                                             # type: ignore
    unmatched = res["unmatched"]                                       # type: ignore

    total_candidates = len(to_delete)
    freed_planned = sum(sz for _, _, sz in to_delete)

    _log.info(
        "log_rotation: dir=%s retention=%dd mode=%s | ALLOW to_delete=%d kept=%d "
        "DENY=%d UNMATCHED=%d cutoff_mtime=%.0f",
        log_dir, retention_days, ("APPLY" if apply else "DRY-RUN"),
        total_candidates, len(kept), len(denied), len(unmatched), res["cutoff_ts"],
    )
    if res.get("dir_missing"):
        _log.info("log_rotation: katalog nie istnieje — no-op (exit 0)")

    # PLAN (zawsze wypisywany PRZED ewentualnym kasowaniem)
    capped = False
    planned = to_delete
    if total_candidates > max_delete:
        capped = True
        planned = to_delete[:max_delete]
        _log.warning(
            "log_rotation: kandydatów=%d > max_delete=%d — LIMIT. Skasuję %d "
            "najstarszych, reszta w następnym biegu.",
            total_candidates, max_delete, max_delete,
        )
    for path, mtime, size in planned:
        _log.info("  PLAN delete %s (mtime=%s, %d B)", path.name,
                  time.strftime("%Y-%m-%d", time.gmtime(mtime)), size)

    deleted_count = 0
    freed_bytes = 0
    errors = 0
    if apply:
        for path, mtime, size in planned:
            try:
                path.unlink()
                deleted_count += 1
                freed_bytes += size
                _log.info("  DELETED %s (%d B)", path.name, size)
            except OSError as e:
                errors += 1
                _log.error("  DELETE-FAIL %s: %s", path.name, e)
    else:
        _log.info("log_rotation: DRY-RUN — nic nie skasowano (użyj --apply).")

    oldest_kept = None
    if kept:
        okpath, okmtime, _ = kept[0]
        oldest_kept = (okpath.name, time.strftime("%Y-%m-%d", time.gmtime(okmtime)))

    summary = {
        "mode": "APPLY" if apply else "DRY-RUN",
        "retention_days": retention_days,
        "candidates_total": total_candidates,
        "planned_count": len(planned),
        "deleted_count": deleted_count,
        "freed_bytes": freed_bytes,
        "freed_planned_bytes": freed_planned,
        "kept_count": len(kept),
        "denied_count": len(denied),
        "unmatched_count": len(unmatched),
        "capped": capped,
        "errors": errors,
        "oldest_kept": oldest_kept,
        "dir_missing": bool(res.get("dir_missing")),
    }
    _log.info(
        "log_rotation: SUMMARY mode=%s candidates=%d planned=%d deleted=%d "
        "freed=%.1fMB would_free=%.1fMB kept=%d denied=%d unmatched=%d capped=%s "
        "errors=%d oldest_kept=%s",
        summary["mode"], total_candidates, len(planned), deleted_count,
        freed_bytes / 1e6, freed_planned / 1e6, len(kept), len(denied),
        len(unmatched), capped, errors, oldest_kept,
    )
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Retencja dziennych logów observability (candidate_decisions/fleet_filter)."
    )
    parser.add_argument("--dir", default=str(DEFAULT_LOG_DIR),
                        help=f"katalog logów (default {DEFAULT_LOG_DIR})")
    parser.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS,
                        help=f"ile dni zachować (default {DEFAULT_RETENTION_DAYS})")
    parser.add_argument("--max-delete", type=int, default=DEFAULT_MAX_DELETE,
                        help=f"bezpiecznik: max plików na bieg (default {DEFAULT_MAX_DELETE})")
    parser.add_argument("--apply", action="store_true",
                        help="realne kasowanie (domyślnie DRY-RUN — tylko raport)")
    args = parser.parse_args(argv)

    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )

    if args.retention_days < 1:
        _log.error("retention-days musi być >= 1 (dostał %d) — abort", args.retention_days)
        return 2
    if args.max_delete < 0:
        _log.error("max-delete musi być >= 0 (dostał %d) — abort", args.max_delete)
        return 2

    try:
        run(
            log_dir=Path(args.dir),
            retention_days=args.retention_days,
            apply=args.apply,
            max_delete=args.max_delete,
        )
    except Exception as e:  # cron-safe: łap wszystko, ale zwróć non-zero → OnFailure
        _log.error("log_rotation: nieoczekiwany błąd: %s: %s", type(e).__name__, e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
