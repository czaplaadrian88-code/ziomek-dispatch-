"""A-1 (2026-08-02) — KANONICZNY writer `gps_positions_pwa.json` (jeden owner
kontraktu, wspólny dla OBU procesów + GC).

DEFEKT ŹRÓDŁOWY (rank #1 audytu): dwaj writerzy w DWÓCH procesach
(`courier_api/gps_writer.py::update_pwa_position` w courier-api oraz
`dispatch_v2/gps_server.py::_update_gps` w dispatch-gps) robili read-modify-write
CAŁEGO pliku z `flock` TYLKO na pliku TYMCZASOWYM (mkstemp) + `threading.Lock`
(wyłącznie wewnątrz-procesowy). Między procesami NIGDY nie kolidowali → pozycja
kuriera cicho ginęła (lost update) → degradacja do no-GPS → zmiana feasibility i
scoringu. Trzeci writer (`tools/gps_positions_gc.py`, cron 04:50) ma ten sam
wyścig (read-modify-write całego pliku), udokumentowany w jego docstringu.

FIX U ŹRÓDŁA (wzorzec 1:1 z `courier_resolver._save_last_known_pos`, K03):
cały cykl load→merge→write pod `fcntl.LOCK_EX` na DEDYKOWANYM lockfile OBOK pliku
(`gps_positions_pwa.json.lock`) — lockfile przeżywa `os.replace` (inode pliku
docelowego się zmienia, lockfile nie). Ten sam lockfile trzymają WSZYSCY trzej
writerzy → serializacja cross-proces. Merge-by-timestamp per cid: nowszy
`timestamp` wygrywa, spóźniony batch NIE cofa świeższej pozycji.

FLAGA `ENABLE_GPS_MERGE_LOCK` (default OFF, hot-reload z `flags.json`, JEDNO
źródło wspólne dla wszystkich procesów — `dispatch_v2.common.flag`):
- OFF = LEGACY, BAJT-PARYTET: `threading.Lock` + load→set(cid)→atomic write
  (mkstemp+flock-na-temp+fsync+rename, `indent=2`), BEZ dedykowanego lockfile,
  BEZ merge, BEZ telemetrii — dokładnie zachowanie sprzed A-1.
- ON = dedykowany lockfile + reload-pod-lockiem + merge-by-ts + telemetria
  (`GPS_PWA_VANISHED_TOTAL`, `GPS_PWA_STALE_SKIPPED_TOTAL` + shadow jsonl obok).

Konsumenci (`courier_resolver._load_gps_positions`, `plan_recheck`, panel admin,
`monitoring/gps_feed_health`) czytają NIEZMIENIONY schemat `{cid: {lat, lon,
accuracy, timestamp, source, name}}` — merge zmienia tylko KTÓRE wpisy przeżyją
równoległy zapis, nie kształt rekordu. Zero zmian po stronie czytelników.
"""
import fcntl
import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

LOCK_SUFFIX = ".lock"
SHADOW_SUFFIX = ".merge_shadow.jsonl"

# Telemetria per-proces (trwała agregacja cross-proces = shadow jsonl obok pliku;
# licznik modułu ginie z procesem, ale wystarcza do asercji testowych i /metrics
# w obrębie jednego procesu). NIE resetować w hot-path.
GPS_PWA_VANISHED_TOTAL = 0
GPS_PWA_STALE_SKIPPED_TOTAL = 0

# Kanarek vanish: pamięć cid zapisanych PRZEZ TEN PROCES; jeśli świeżo zapisany
# cid znika z dysku albo cofa się do starszego ts między naszymi zapisami =
# nadpisał go writer POZA wspólnym lockiem (stary/niezaktualizowany bliźniak
# courier-api, albo flaga ON tylko po jednej stronie) → realny lost-update, który
# ten fix zamyka. Okno 15 min: >> tick GPS (30 s), << GC (>24 h) → zero fałszywek z GC.
VANISH_WATCH_SEC = 900.0
_last_written = {}   # cid -> datetime ostatniego udanego zapisu W TYM procesie

_DT_MIN = datetime(1970, 1, 1, tzinfo=timezone.utc)
# Wewnątrz-procesowa serializacja — zachowana z legacy (_write_lock/_pwa_write_lock),
# NIEZALEŻNIE od flagi (chroni wątki uvicorn/ThreadingTCPServer jak dotąd).
_thread_lock = threading.Lock()


def merge_lock_enabled() -> bool:
    """Odczyt `ENABLE_GPS_MERGE_LOCK` z `flags.json` (hot-reload, wspólne źródło
    cross-proces). Fail-soft → OFF (legacy) gdy flags nieczytelne / import padł
    (np. courier-api bez dispatch_v2 na ścieżce w skrajnym wypadku)."""
    try:
        from dispatch_v2.common import flag
        # Nazwa LITERALNIE (nie przez stałą) — wymóg walidatora rejestru flag
        # (flag_lifecycle_seed: literal_flag_calls). Wartość identyczna.
        return bool(flag("ENABLE_GPS_MERGE_LOCK", False))
    except Exception:
        return False


def _scan_vanished(disk: dict, now: datetime) -> list:
    """Kanarek lost-update (REALNIE osiągalny, nie [] „z definicji"): cid zapisane
    przez TEN proces w ostatnich VANISH_WATCH_SEC, których na dysku już NIE MA albo
    mają STARSZY ts niż nasz zapis. To znaczy: między naszym zapisem a teraz ktoś
    nadpisał plik POZA wspólnym lockiem (legacy/niezaktualizowany bliźniak) i
    zgubił naszą pozycję — dokładnie defekt, który ten fix zamyka. Prune wpisów
    poza oknem (GC >24 h ich nie dotyczy → zero fałszywek). Zwraca posortowaną listę.

    Nowszy zapis tego cid przez DRUGI proces pod tym samym lockiem ma ts >= nasz →
    NIE jest anomalią (to poprawny merge, nie utrata)."""
    out = []
    for wcid in list(_last_written.keys()):
        wts = _last_written[wcid]
        if (now - wts).total_seconds() > VANISH_WATCH_SEC:
            del _last_written[wcid]
            continue
        dcur = disk.get(wcid)
        if dcur is None or _entry_ts(dcur) < wts:
            out.append(wcid)
    return sorted(out)


def _entry_ts(rec) -> datetime:
    """`timestamp` rekordu → aware-UTC datetime (DT_MIN gdy brak/niepars.)."""
    if not isinstance(rec, dict):
        return _DT_MIN
    try:
        t = datetime.fromisoformat(str(rec.get("timestamp", "")).replace("Z", "+00:00"))
        return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t
    except Exception:
        return _DT_MIN


def _load_json(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _atomic_write_json(path: str, data: dict) -> None:
    """temp (mkstemp UNIKALNY) + flock LOCK_EX na temp + fsync + rename — wzorzec
    P0.5b. BAJT-IDENTYCZNY z legacy gps_writer/gps_server (`indent=2`,
    `ensure_ascii=False`) → zachowanie OFF-parytetu i czytelności pliku."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=f".{p.name}.tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


def _shadow_log(path: str, event: str, cid: str, extra: Optional[dict] = None) -> None:
    """Append-only telemetria obok pliku PWA (fail-soft — NIGDY nie wywala zapisu
    pozycji). Trwała, agregowalna cross-proces (per-proces licznik ginie)."""
    try:
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event": event,
            "cid": str(cid),
        }
        if extra:
            rec.update(extra)
        with open(str(path) + SHADOW_SUFFIX, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def write_pwa_position(
    path: str,
    cid: str,
    record: dict,
    *,
    use_lock: Optional[bool] = None,
    _after_load: Optional[Callable[[], None]] = None,
) -> str:
    """Kanoniczny zapis JEDNEGO cid do `gps_positions_pwa.json`.

    Parametry:
      use_lock: None → czytaj flagę `ENABLE_GPS_MERGE_LOCK`; True/False → wymuś
        (deterministyczne testy ON/OFF; produkcja woła bez tego argumentu).
      _after_load: SEAM WYŁĄCZNIE TESTOWY — callable wołany PO load, PRZED zapisem.
        Domyślnie None (no-op, zero kosztu i zero wpływu na bajt-parytet OFF).
        UWAGA: w trybie ON jest wołany POD dedykowanym lockiem — NIE przekazuj tu
        callable blokującego cross-proces (barrier) przy use_lock=True (deadlock).

    Zwraca: 'written' | 'stale_skipped'.
    """
    global GPS_PWA_VANISHED_TOTAL, GPS_PWA_STALE_SKIPPED_TOTAL
    cid = str(cid)
    record_ts = _entry_ts(record)
    on = merge_lock_enabled() if use_lock is None else bool(use_lock)

    if not on:
        # LEGACY / BAJT-PARYTET: threading.Lock (jak dotąd) + load→set→atomic write.
        with _thread_lock:
            data = _load_json(path)
            if _after_load is not None:
                _after_load()
            data[cid] = record
            _atomic_write_json(path, data)
        return "written"

    # ON: cały cykl load→merge→write pod DEDYKOWANYM lockfile (cross-proces).
    _dir = os.path.dirname(path) or "."
    os.makedirs(_dir, exist_ok=True)
    with open(path + LOCK_SUFFIX, "w") as lk:
        fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
        try:
            disk = _load_json(path)
            if _after_load is not None:
                _after_load()
            now = datetime.now(timezone.utc)
            # KANAREK (przed merge): nasze świeże cid, które zniknęły/cofnęły się na
            # dysku = ktoś nadpisał plik POZA tym lockiem (stary bliźniak / flaga ON
            # tylko po jednej stronie). To realny lost-update — sygnał, nie [] z definicji.
            vanished = _scan_vanished(disk, now)
            if vanished:
                GPS_PWA_VANISHED_TOTAL += len(vanished)
                for v in vanished:
                    _shadow_log(path, "vanished", v, {"during_cid": cid})
            cur = disk.get(cid)
            if cur is None or record_ts >= _entry_ts(cur):
                disk[cid] = record
                result = "written"
            else:
                # Spóźniony batch: świeższa pozycja tego cid już na dysku → NIE cofaj.
                GPS_PWA_STALE_SKIPPED_TOTAL += 1
                _shadow_log(path, "stale_skipped", cid, {
                    "incoming_ts": record.get("timestamp") if isinstance(record, dict) else None,
                    "disk_ts": cur.get("timestamp") if isinstance(cur, dict) else None,
                })
                result = "stale_skipped"
            # Zapamiętaj NAJŚWIEŻSZY znany ts tego cid (nasz zapis albo już-nowszy z
            # dysku) — baza dla kanarka przy kolejnych zapisach tego procesu.
            _last_written[cid] = _entry_ts(disk.get(cid))
            _atomic_write_json(path, disk)
            return result
        finally:
            fcntl.flock(lk.fileno(), fcntl.LOCK_UN)


class pwa_lock:
    """Context manager dla operacji WIELO-cid na pliku PWA (GC prune) — trzyma ten
    sam dedykowany lockfile co `write_pwa_position`, więc GC i żywi writerzy się
    serializują. OFF (flaga) → no-op (legacy: GC bez lockfile = bajt-parytet).

    Użycie:  with pwa_lock(GPS_PWA_PATH): ...load→prune→atomic_write...
    """

    def __init__(self, path: str, use_lock: Optional[bool] = None):
        self.path = path
        self.on = merge_lock_enabled() if use_lock is None else bool(use_lock)
        self._lk = None

    def __enter__(self):
        if self.on:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            self._lk = open(self.path + LOCK_SUFFIX, "w")
            fcntl.flock(self._lk.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        if self._lk is not None:
            try:
                fcntl.flock(self._lk.fileno(), fcntl.LOCK_UN)
            finally:
                self._lk.close()
                self._lk = None
        return False
