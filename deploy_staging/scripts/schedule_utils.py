# STAGING COPY (NIE żywy plik) — podmiana za ACK koordynatora, off-peak.
# STAGING: kopia /root/.openclaw/workspace/scripts/schedule_utils.py z fixem finding H (H1b).
# STAGING: diff vs żywy = TYLKO usunięcie fixed-offset fallbacku strefy (except
# STAGING: ImportError → stały offset +2h = bomba TZ) + użycie _now_warsaw()
# STAGING: dla `today` w load_schedule (H1b). Reszta bajt-identyczna.
# STAGING: sekwencja deployu + rollback = eod_drafts/2026-07-02/grafik-h_raport.md.
import json
import logging
import os
import subprocess
import time
import unicodedata

# Polskie diakrytyki → ASCII, do PREFIKS-porównania nazwisk (#195, 2026-06-19).
# Skrót planszy gastro jest ASCII ("Paweł SC"), nazwisko w grafiku z diakrytykiem
# ("Ściepko") — bez normalizacji "sc".startswith vs "ściepko" gubi trafienie.
# ł/Ł nie rozkłada się przez NFKD → mapowane jawnie.
def _ascii_fold(s: str) -> str:
    s = s.replace("ł", "l").replace("Ł", "L")
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    ).lower()
from datetime import datetime, timezone, timedelta
# H1 (audyt 2.0 finding H): ZoneInfo=stdlib, fail-loud — BEZ fixed-offset fallbacku.
# Stary except-ImportError na stały offset +2h był bombą TZ: po końcu DST (25-26.10)
# stały +2 kłamie o godzinę (zimą CET=+1), a ImportError i tak nie padnie (zoneinfo
# w stdlib od 3.9). Jedno źródło strefy dla schedule_utils.
from zoneinfo import ZoneInfo
_TZ = ZoneInfo("Europe/Warsaw")
def _now_warsaw():
    return datetime.now(_TZ).replace(tzinfo=None)

from pathlib import Path
from typing import Optional
SCHEDULE_FILE = Path('/root/.openclaw/workspace/dispatch_state/schedule_today.json')
LEARNING_LOG_PATH = Path('/root/.openclaw/workspace/dispatch_state/learning_log.jsonl')
# ETAP 3 krok 2 (2026-06-10, Z-03): MATCH_AMBIGUOUS/MATCH_NOT_FOUND to debug
# matchowania nazw (90% LICZBY wpisów learning_log) — idą do osobnego pliku,
# żeby learning_log zostało strumieniem decyzji propozycji (TAK/NIE/AGREE/
# OVERRIDE/TIMEOUT). Stary learning_log nietknięty (append-only historia).
MATCH_DEBUG_LOG_PATH = Path('/root/.openclaw/workspace/dispatch_state/courier_match_debug.jsonl')

# T3 (2026-05-01): hot-refresh TTL — eliminuje 8h freeze schedule daily.
# Pre-T3: cron 06:00+08:00 Warsaw (2x dziennie) → mid-day Sheet edits NIE propagate
# do shadow do następnego dnia. Post-T3: load_schedule() lazy-refreshes co 10 min
# via subprocess fetch_schedule.py call. Defense-in-depth: fail-open (continue z
# stale cache gdy fetch fails). Configurable via SCHEDULE_TTL_MIN env.
SCHEDULE_TTL_MIN = float(os.environ.get("SCHEDULE_TTL_MIN", "10"))
FETCH_SCHEDULE_SCRIPT = "/root/.openclaw/workspace/scripts/fetch_schedule.py"
FETCH_TIMEOUT_SEC = float(os.environ.get("SCHEDULE_FETCH_TIMEOUT_SEC", "30"))

# In-memory cache (mtime-keyed)
_cached_couriers = None
_cached_mtime = 0.0
_last_fetch_attempt_ts = 0.0  # debounce (don't trigger fetch >1×/30s na fail loop)
_FETCH_DEBOUNCE_SEC = 30.0

_log = logging.getLogger("schedule_utils")

# Ręczne mapowania: nazwa w panelu → pełna nazwa w grafiku
# Potrzebne gdy automatyczne dopasowanie jest niejednoznaczne lub niemożliwe.
# V3.25 dodane: 'Jakub OL', 'Szymon Sa', 'Grzegorz', 'Grzegorz R' explicit mapping;
# 'Mykyta K' i 'Krystian' explicit None (ex-kurierzy, wycisza warning spam).
PANEL_TO_SCHEDULE = {
    'Gabriel':       'Gabriel Ostapczuk',
    'Gabriel J':     'Gabriel Januszko',
    'Gabriel Je':    'Gabriel Jedynak',
    'Michał Ro':     'Michał Rogucki',
    'Michał Rom':    'Michał Romańczuk',
    'Grzegorz':      'Grzegorz Rogowski',  # V3.25: cid=500 (Adrian Q1)
    'Grzegorz R':    'Grzegorz Rogowski',  # V3.25 alias
    'Jakub OL':      'Kuba Olchowik',      # V3.25: cid=370 (Adrian Q2 minimum invasive)
    'Szymon Sa':     'Szymon Sadowski',    # V3.25: cid=522 (Adrian Q3 alias-pair)
    'Mykyta K':      None,                  # V3.25: ex-kurier (Adrian 23.04)
    'Krystian':      None,                  # V3.25: ex-kurier (Adrian 23.04)
    'Koordynator':   None,                  # nie jest kurierem
    'Rutcom kurier': None,                  # nie jest kurierem
}

def _trigger_fetch():
    """T3: subprocess fetch_schedule.py z hard timeout. Defense-in-depth:
    nie raise w callerze, zwraca True/False oznaczając success/fail.
    """
    global _last_fetch_attempt_ts
    now = time.time()
    # Debounce — don't trigger fetch >1×/30s na fail loop (rate limit Sheet API)
    if now - _last_fetch_attempt_ts < _FETCH_DEBOUNCE_SEC:
        return False
    _last_fetch_attempt_ts = now
    try:
        result = subprocess.run(
            ["python3", FETCH_SCHEDULE_SCRIPT],
            timeout=FETCH_TIMEOUT_SEC,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            _log.info(f"T3 schedule fetch SUCCESS (returncode 0)")
            return True
        _log.warning(
            f"T3 schedule fetch FAIL returncode={result.returncode} "
            f"stderr_tail={(result.stderr or '')[-200:]}"
        )
        return False
    except subprocess.TimeoutExpired:
        _log.error(f"T3 schedule fetch TIMEOUT after {FETCH_TIMEOUT_SEC}s")
        return False
    except FileNotFoundError as e:
        _log.error(f"T3 schedule fetch script missing: {e}")
        return False
    except Exception as e:
        _log.error(f"T3 schedule fetch unexpected fail: {type(e).__name__}: {e}", exc_info=True)
        return False


def load_schedule():
    """T3 (2026-05-01) — lazy hot-refresh z TTL.

    Behavior:
    1. File mtime check vs SCHEDULE_TTL_MIN (default 10 min)
    2. If stale → trigger fetch_schedule.py subprocess (timeout 30s)
    3. Reload from disk if mtime changed (memory cache)
    4. Fail-open: stale cache continues if fetch fails (Lekcja #31)

    Backward-compatible: returns same dict {courier_name: entry_or_None}.
    """
    global _cached_couriers, _cached_mtime
    try:
        # Check current file mtime
        try:
            current_mtime = SCHEDULE_FILE.stat().st_mtime
        except FileNotFoundError:
            _log.warning(f"T3 SCHEDULE_FILE not found at {SCHEDULE_FILE}, triggering fetch")
            _trigger_fetch()
            try:
                current_mtime = SCHEDULE_FILE.stat().st_mtime
            except FileNotFoundError:
                _log.error(f"T3 fetch failed AND file still missing — return empty schedule")
                return {}

        # Age check
        age_sec = time.time() - current_mtime
        age_min = age_sec / 60.0
        if age_min > SCHEDULE_TTL_MIN:
            _log.info(
                f"T3 schedule stale ({age_min:.1f} min > {SCHEDULE_TTL_MIN} min TTL), "
                f"refreshing via fetch_schedule.py"
            )
            fetch_ok = _trigger_fetch()
            if fetch_ok:
                try:
                    current_mtime = SCHEDULE_FILE.stat().st_mtime
                except Exception as e:
                    _log.warning(f"T3 stat post-fetch fail: {e}")
            # If fetch failed → continue z stale current_mtime (fail-open)

        # Reload from disk only if mtime changed (memory cache speedup)
        if current_mtime != _cached_mtime or _cached_couriers is None:
            try:
                data = json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))
                today = _now_warsaw().strftime("%d-%m-%y")  # H1b: Warsaw, nie naive-UTC
                if data.get("date") != today:
                    _log.warning(
                        f"T3 schedule date={data.get('date')} != today={today} "
                        f"(refresh did NOT update date — possibly Sheet not updated jutro)"
                    )
                _cached_couriers = data.get("couriers", {})
                _cached_mtime = current_mtime
                _log.info(f"T3 schedule cache reloaded ({len(_cached_couriers)} entries, mtime={current_mtime})")
            except json.JSONDecodeError as e:
                _log.error(f"T3 schedule JSON parse fail: {e} — return previous cache")
                if _cached_couriers is not None:
                    return _cached_couriers
                return {}
            except Exception as e:
                _log.error(f"T3 schedule read fail: {type(e).__name__}: {e}", exc_info=True)
                if _cached_couriers is not None:
                    return _cached_couriers
                return {}
        return _cached_couriers
    except Exception as e:
        _log.error(f"T3 load_schedule unexpected fail: {type(e).__name__}: {e}", exc_info=True)
        return _cached_couriers if _cached_couriers is not None else {}


def schedule_age_sec() -> Optional[float]:
    """MP-#15 (2026-05-08): seconds since last schedule file update.

    Returns None if file missing. Used by:
      - shift_notifications.worker — STALE_SCHEDULE_AGE alert gdy >30min
      - telegram_approver.format_status — wyświetla schedule_age_min w /status
      - LGBM training (future) — feature dla rozpoznania degraded refresh window

    Source of truth: SCHEDULE_FILE.stat().st_mtime. Cache-internal `_cached_mtime`
    może być stale relative to disk (jeśli inny proces zmodyfikował) — stat()
    zawsze zwraca current ground truth.
    """
    try:
        return time.time() - SCHEDULE_FILE.stat().st_mtime
    except FileNotFoundError:
        return None
    except Exception as e:
        _log.warning(f"schedule_age_sec stat fail: {type(e).__name__}: {e}")
        return None


def is_schedule_stale(threshold_sec: int = 30 * 60) -> bool:
    """MP-#15 (2026-05-08): True jeśli schedule_age > threshold (default 30min).

    None age (file missing) → True (treat missing as stale dla alerting).
    """
    age = schedule_age_sec()
    if age is None:
        return True
    return age > threshold_sec


def write_schedule_today_backup(target_path: Optional[str] = None) -> bool:
    """MP-#15 (2026-05-08): atomic snapshot bieżącego schedule.json do
    `dispatch_state/schedule_today_backup.json` (lub custom target_path).

    Fallback gdy Google Sheets API completely down i SCHEDULE_FILE też corrupted.
    Wywoływane raz dziennie z shift_notifications.worker (pierwszy tick po 06:00
    Warsaw) lub manualnie z scriptu.

    Atomic write via core/atomic_io pattern (tempfile + fsync + os.replace).

    Returns True on success, False on fail (logged).
    """
    if target_path is None:
        target_path = "/root/.openclaw/workspace/dispatch_state/schedule_today_backup.json"

    try:
        if not SCHEDULE_FILE.exists():
            _log.warning(f"write_schedule_today_backup: SCHEDULE_FILE {SCHEDULE_FILE} missing — skip")
            return False

        # Read current schedule (avoid using cache — fresh read for backup integrity)
        data = json.loads(SCHEDULE_FILE.read_text(encoding="utf-8"))

        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write via tempfile + os.replace
        import tempfile
        fd, tmp = tempfile.mkstemp(
            prefix=".schedule_backup_",
            suffix=".json.tmp",
            dir=str(target.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, target)
            _log.info(f"write_schedule_today_backup: snapshot OK {target} ({len(data.get('couriers', {}))} couriers)")
            return True
        except Exception:
            try:
                os.unlink(tmp)
            except Exception:
                pass
            raise
    except Exception as e:
        _log.error(f"write_schedule_today_backup fail: {type(e).__name__}: {e}", exc_info=True)
        return False


def _log_match_event(event, panel_name, **extra):
    """V3.25: append-only audit MATCH_AMBIGUOUS / MATCH_NOT_FOUND. Od 2026-06-10
    (ETAP 3 krok 2) pisze do courier_match_debug.jsonl zamiast learning_log
    (odszumienie strumienia decyzji). Failure tej funkcji NIGDY nie wpływa na
    resolver (pass on exception)."""
    try:
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "input": panel_name,
        }
        rec.update(extra)
        with open(MATCH_DEBUG_LOG_PATH, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def match_courier_strict(panel_name, schedule):
    """V3.25: deterministic match_courier with audit logging.

    Behaviour vs legacy match_courier:
    - PANEL_TO_SCHEDULE override → unchanged
    - Direct schedule key hit → unchanged
    - first_name + surname-PREFIX unique match → return (single result)
    - first_name + surname-PREFIX AMBIGUOUS (>1) → log MATCH_AMBIGUOUS + return None
      (legacy: silently picked first — caused wrong "Jakub Leoniuk" for "Jakub OL")
    - first_name only single match → unchanged
    - first_name only multi-match → log MATCH_AMBIGUOUS + return None
    - zero match → log MATCH_NOT_FOUND + return None

    Returns: full schedule key name, or None.

    FIX (2026-06-19, #195): dopasowanie nazwiska po PREFIKSIE całego podanego
    członu, nie po samym pierwszym inicjale. Inicjał gubił kolizje, gdy dwie
    osoby dzielą imię + pierwszą literę nazwiska, a w grafiku jest tylko jedna:
    "Marcin Bystrowski"→"Marcin Puszko" (B≠P), oraz "Dawid Krajewski"→"Dawid
    Kalinowski", "Gabriel Jedynak"→"Gabriel Januszko", "Michał Rogucki"→"Michał
    Romańczuk" (Kr/Ka, Je/Ja, Ro/Ro — ten sam inicjał, różne nazwiska). Prefiks
    jest ściśle bardziej dyskryminujący niż inicjał: skróty panelu ("Michał K",
    "Bartek O", "Mateusz Bro") nadal trafiają, a pełne nazwiska spoza grafiku
    → None (poprawne wykluczenie zamiast kradzieży cudzej tożsamości/zmiany).
    """
    if not panel_name or not schedule:
        return None
    if panel_name in PANEL_TO_SCHEDULE:
        return PANEL_TO_SCHEDULE[panel_name]
    if panel_name in schedule:
        return panel_name
    parts = panel_name.strip().split()
    if not parts:
        return None
    first_name = parts[0]
    # Pełny podany człon nazwiska (skrót "Kr"/"Je" lub pełne "Krajewski"), bez
    # interpunkcji skrótów panelu ("Bartosz Ch." → "ch"), ASCII-fold dla diakrytyków.
    last_token = _ascii_fold(parts[1].rstrip(".,;:")) if len(parts) > 1 else None
    fn_matches = [full for full in schedule if full.split()[0].lower() == first_name.lower()]

    if last_token:
        # Kandydaci, których nazwisko jest PREFIKS-zgodne z podanym członem (ASCII-fold
        # po obu stronach: "SC"→"sc" vs "Ściepko"→"sciepko"✓; "by"→"bystrowski"✓ vs "puszko"✗).
        # "Je"→"Jedynak"✓ / "Jedynak"→"Jedynak"✓ / "Januszko"→nie zaczyna się od "je".
        pref = [
            full for full in fn_matches
            if len(full.split()) > 1 and _ascii_fold(full.split()[1]).startswith(last_token)
        ]
        if len(pref) == 1:
            return pref[0]
        if len(pref) > 1:
            _log_match_event("MATCH_AMBIGUOUS", panel_name,
                             candidates=pref, rule="first_name+surname_prefix")
            return None
        # pref == 0: podane nazwisko nie pasuje do ŻADNEGO kandydata → NIE schodzimy
        # do dopasowania po samym imieniu wobec osób z nazwiskiem (to byłaby kradzież
        # tożsamości). Wyjątek: klucze jednoczłonowe (np. "Adrian") nie mają nazwiska
        # do sprzeczności → mogą zostać (zero regresji dla legalnych skrótów panelu).
        singletons = [full for full in fn_matches if len(full.split()) < 2]
        if len(singletons) == 1:
            return singletons[0]
        if len(singletons) > 1:
            _log_match_event("MATCH_AMBIGUOUS", panel_name,
                             candidates=singletons, rule="first_name_only_singleton")
            return None
        _log_match_event("MATCH_NOT_FOUND", panel_name, schedule_size=len(schedule))
        return None

    # Brak podanego nazwiska (samo imię) → dopasowanie po imieniu.
    if len(fn_matches) == 1:
        return fn_matches[0]
    if len(fn_matches) > 1:
        _log_match_event("MATCH_AMBIGUOUS", panel_name,
                         candidates=fn_matches, rule="first_name_only")
        return None
    _log_match_event("MATCH_NOT_FOUND", panel_name, schedule_size=len(schedule))
    return None


def match_courier(panel_name, schedule):
    """V3.25: backward-compat alias for match_courier_strict.

    Pre-V3.25 implementation had silent fail-soft picking first candidate
    when first_name was ambiguous (>1 first_name+last_initial matches).
    That silently returned wrong courier (e.g. "Jakub OL" → "Jakub Leoniuk").
    All callers now route through strict variant; legacy print noise replaced
    by structured learning_log alarms.
    """
    return match_courier_strict(panel_name, schedule)


# L2.3 (2026-07-02, wzór FAIL12 feasibility_v2): fail-open is_on_shift = GŁOŚNY.
# Gałęzie "return True" bez potwierdzenia w grafiku (brak grafiku / brak w grafiku /
# brak godzin / błąd parsowania) czyniły kuriera CICHO 24/7 — awaria grafiku
# wyglądała jak legalna dostępność. Log-only (zero zmiany decyzji); dedup
# per (kurier, powód) co _FAIL_OPEN_WARN_INTERVAL_S, żeby tick co kilka sekund
# nie zalał logu. Kill: SCHEDULE_FAIL_OPEN_WARN_INTERVAL_S=0 wyłącza dedup (nie logi).
_FAIL_OPEN_WARN_INTERVAL_S = float(os.environ.get("SCHEDULE_FAIL_OPEN_WARN_INTERVAL_S", "3600"))
_FAIL_OPEN_LAST_WARN = {}


def _warn_shift_fail_open(panel_name, reason):
    """log.warning przy fail-open is_on_shift, z dedupem per (kurier, powód)."""
    try:
        key = (str(panel_name), reason)
        now_ts = time.time()
        last = _FAIL_OPEN_LAST_WARN.get(key, 0.0)
        if _FAIL_OPEN_WARN_INTERVAL_S > 0 and now_ts - last < _FAIL_OPEN_WARN_INTERVAL_S:
            return
        _FAIL_OPEN_LAST_WARN[key] = now_ts
        _log.warning(
            "SHIFT_FAIL_OPEN: is_on_shift(%r) -> True bez potwierdzenia w grafiku "
            "(%s) — kurier traktowany jak dostępny 24/7. SPRAWDŹ GRAFIK "
            "(Google Sheet awaria/niepełny/parsowanie?).",
            panel_name, reason,
        )
    except Exception:  # log-only, nigdy nie psuje decyzji
        pass


def is_on_shift(panel_name, schedule, now=None):
    if not schedule:
        _warn_shift_fail_open(panel_name, "brak grafiku")
        return True, "brak grafiku"

    # Zawsze używaj czasu warszawskiego — serwer może być w UTC
    now = _now_warsaw()

    full_name = match_courier_strict(panel_name, schedule)
    if full_name is None:
        _warn_shift_fail_open(panel_name, "nie znaleziono w grafiku")
        return True, "nie znaleziono w grafiku"

    entry = schedule.get(full_name)
    if entry is None:
        return False, f"nie pracuje dziś ({full_name})"

    start_str = entry.get('start')
    end_str   = entry.get('end')
    if not start_str or not end_str:
        _warn_shift_fail_open(panel_name, "brak godzin w grafiku")
        return True, "brak godzin w grafiku"

    try:
        start = datetime.strptime(start_str, "%H:%M").replace(year=now.year, month=now.month, day=now.day)
        if end_str == "24:00":
            end = start.replace(hour=0, minute=0) + timedelta(days=1)
        else:
            end = datetime.strptime(end_str, "%H:%M").replace(year=now.year, month=now.month, day=now.day)
    except ValueError:
        _warn_shift_fail_open(panel_name, "błąd parsowania godzin")
        return True, "błąd parsowania godzin"

    if now < start:
        minutes_to_start = int((start - now).total_seconds() / 60)
        return False, f"zmiana od {start_str} (za {minutes_to_start} min)"
    if now >= end:
        return False, f"zmiana skończyła się o {end_str}"
    minutes_to_end = int((end - now).total_seconds() / 60)
    return True, f"zmiana {start_str}–{end_str} (jeszcze {minutes_to_end} min)"
