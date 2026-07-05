#!/usr/bin/env python3
"""observability/data_alerts.py — monitor DANOWY Ziomka (nie procesowy).

KONTEKST (audyt 2.0, motyw #1 „pokrycie ciszy ≈ 0%", pas ALERTY / 2.B)
----------------------------------------------------------------------
Dotychczasowe alerty są PROCESOWE: systemd `OnFailure` łapie padnięcie procesu
(non-zero exit), ale NIE łapie trybów, w których proces żyje, a DANE są chore:
zamrożony ledger, burza sentineli w pozycjach, pusta pula feasible, martwy fetch
grafiku, stare pozycje GPS floty. Audyt zmierzył: 2046+14456 zdarzeń sentinelowych
= 0 alertów, 42% jednostek bez żadnego alertu, realne pokrycie ciszy ≈ 0%.

Ten moduł dokłada 5 sygnałów DANOWYCH, edge-triggered (transition-only — NIE
spam co tick), czytających ŻYWY stan read-only:

  1. sentinel-rate    — odsetek świeżych decyzji, w których pozycja WYBRANEGO
                        kuriera jest sentinelowa/fikcyjna: coord-poison (0,0),
                        `v328_fail_causes`, albo `pos_source` z zestawu sentinel
                        (None/""/unknown). ⚠ `no_gps`/`pre_shift` NIE są sentinelem
                        — to LEGALNA polityka równego traktowania (KANON, Adrian
                        29.06), alarmowanie na nich łamałoby regułę. Sygnał łapie
                        SKOK ponad bazowy szum (~0-4%), np. gdy feed GPS padł i
                        wszyscy spadli do fikcji.
  2. empty-pool       — odsetek decyzji z `pool_feasible_count == 0` w oknie.
                        Pusta pula bywa legalna (always-propose best-effort pod
                        scarcity), więc próg wysoki — łapiemy wyczerpanie floty /
                        awarię feasibility, nie normalny ogon.
  3. stale-grafik     — `schedule_today.json:fetched_at` starszy niż próg (feed
                        grafiku zamarł). Tylko w godzinach pracy.
  4. stale-pozycje    — odsetek floty w `courier_last_pos.json`, której pozycja
                        jest starsza niż próg (ingest GPS degraduje). Godz. pracy.
  5. ledger-stall     — brak NOWYCH rekordów w `shadow_decisions` przez próg minut
                        w oknie PEAK (11-14 / 17-20 Warsaw) — silnik shadow martwy.
  6. q1-missing-time  — zlecenie PRZYPISANE >= dwell min i wciąż BEZ czasu
                        odbioru (czas_kuriera) — apka/konsola bez czasu, R27
                        ślepe. Następca pionu Q1 wygasającego (10.07)
                        `ziomek_time_route_monitor` (SPRINT0 pas Q1, ACK 05.07).

KONSTRUKCJA (protokół #0 + C12/C13)
-----------------------------------
* Ledger czytany WYŁĄCZNIE kanonem rotation-aware `tools.ledger_io`
  (`iter_shadow_decisions`) — naiwny odczyt gubi ~29% okna po rotacji.
* Progi = stałe na górze modułu, env-overridable (`DATA_ALERTS_*`). Determinizm:
  ewaluatory to CZYSTE funkcje (dane wejściowe → `Signal`), zero I/O — testowalne
  behawioralnie + mutation (C13). Warstwa `collect()` ładuje dane z dysku.
* Flaga MASTER `ENABLE_DATA_ALERTS` (common.flag, default OFF w kodzie) — OFF =
  no-op exit 0. Telegram za DRUGĄ flagą `DATA_ALERTS_TELEGRAM` (default OFF);
  default (master ON, telegram OFF) = log do `scripts/logs/data_alerts.log` +
  stan edge-trigger w `dispatch_state/data_alerts_state.json` (atomowy zapis
  temp+fsync+rename).
* Czas: `zoneinfo.ZoneInfo("Europe/Warsaw")` — NIGDY fixed-offset (ratchet TZ
  kanonu, bomby DST 25-26.10). Bramki godzinowe liczone w Warszawie.

CLI
---
    python -m dispatch_v2.observability.data_alerts               # dry-run (default): raport, ZERO zapisu/telegramu
    python -m dispatch_v2.observability.data_alerts --run         # realny: edge-trigger, log, (telegram za flagą)
    python -m dispatch_v2.observability.data_alerts --json        # raport maszynowy (dry)

Cron-safe: exit 0 na każdej normalnej ścieżce (także master-flag OFF / brak
plików). Non-zero = realny błąd wykonania.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

# Rotation-aware READ-kanon ledgera (fala L1.2). Import odporny na uruchomienie
# spod tools/ jak i jako pakiet dispatch_v2.
try:
    from dispatch_v2.tools import ledger_io
except ImportError:  # pragma: no cover - fallback dla gołego uruchomienia
    _here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.dirname(_here) not in sys.path:
        sys.path.insert(0, os.path.dirname(_here))
    from dispatch_v2.tools import ledger_io  # type: ignore

WARSAW = ZoneInfo("Europe/Warsaw")
_log = logging.getLogger("observability.data_alerts")

# ── Ścieżki (READ-only źródła + zapis stanu/logu) ────────────────────────────
_STATE_DIR = Path("/root/.openclaw/workspace/dispatch_state")
_LOGS_DIR = Path("/root/.openclaw/workspace/scripts/logs")
DEFAULT_STATE_PATH = _STATE_DIR / "data_alerts_state.json"
DEFAULT_LOG_PATH = _LOGS_DIR / "data_alerts.log"
SCHEDULE_PATH = _STATE_DIR / "schedule_today.json"
COURIER_LAST_POS_PATH = _STATE_DIR / "courier_last_pos.json"
ORDERS_STATE_PATH = _STATE_DIR / "orders_state.json"

STATE_SCHEMA_VERSION = 1


# ── Progi (env-overridable; wartości = bazowy szum zmierzony 30.06-02.07 + margines)
def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


# Okno świeżości dla sygnałów rate'owych (ledger).
WINDOW_MIN = _env_int("DATA_ALERTS_WINDOW_MIN", 30)
# Minimalna liczba próbek, poniżej której rate jest szumem (nie alarmujemy).
MIN_SAMPLE = _env_int("DATA_ALERTS_MIN_SAMPLE", 20)

# 1. sentinel-rate: baseline ~0-4% (pos_source None). Próg 15% = wyraźny skok.
SENTINEL_RATE_PCT = _env_float("DATA_ALERTS_SENTINEL_RATE_PCT", 15.0)
# 2. empty-pool: baseline ~11% (legalny ogon scarcity). Próg 40% = wyczerpanie/awaria.
EMPTY_POOL_PCT = _env_float("DATA_ALERTS_EMPTY_POOL_PCT", 40.0)
# 3. stale-grafik: fetch grafiku raz-kilka razy/dobę; 6h ciszy w pracy = feed martwy.
GRAFIK_STALE_H = _env_float("DATA_ALERTS_GRAFIK_STALE_H", 6.0)
# 4. stale-pozycje: pozycja starsza niż 20 min = nieaktualna; alarm gdy >50% floty.
GPS_STALE_MIN = _env_float("DATA_ALERTS_GPS_STALE_MIN", 20.0)
GPS_STALE_FRAC_PCT = _env_float("DATA_ALERTS_GPS_STALE_FRAC_PCT", 50.0)
GPS_MIN_FLEET = _env_int("DATA_ALERTS_GPS_MIN_FLEET", 4)
# 5. ledger-stall: mediana odstępu decyzji w peaku ~2 min (pomiar 30.06-02.07),
# ale niski wolumen (~230 zam./d) daje LEGALNY ogon do ~35 min w cichym peaku →
# próg 30 min łapie martwy silnik (cisza godzinami), nie karze naturalnej ciszy.
STALL_MIN = _env_float("DATA_ALERTS_STALL_MIN", 30.0)

# 6. q1-missing-time (SPRINT0 pas Q1, 05.07 — następca pionu Q1 monitora
# ziomek_time_route_monitor, expiry 10.07): zlecenie PRZYPISANE od >= dwell min
# i WCIĄŻ bez czasu odbioru (czas_kuriera). Dwell odszumia świeżo-przypisane
# (koordynator/silnik ustawia czas chwilę po przypisaniu — monitor bez dwella
# pokazywał przejściowe 2->0 w 10 min). Delty vs monitor (świadome):
# (a) dwell 10 min zamiast migawki, (b) tylko status=assigned (po odbiorze
# brakujący deklarowany czas jest bezprzedmiotowy), (c) wykluczony wirtualny
# Koordynator cid=26 (holding czasówek — nie jest realnym przypisaniem).
Q1_DWELL_MIN = _env_float("DATA_ALERTS_Q1_DWELL_MIN", 10.0)
Q1_MIN_COUNT = _env_int("DATA_ALERTS_Q1_MIN_COUNT", 1)
Q1_EXCLUDED_CIDS = frozenset(
    c.strip() for c in os.environ.get("DATA_ALERTS_Q1_EXCLUDED_CIDS", "26").split(",")
    if c.strip())

# Cooldown re-emisji tego samego wciąż-aktywnego sygnału (edge = 1. strzał zawsze).
COOLDOWN_MIN = _env_float("DATA_ALERTS_COOLDOWN_MIN", 60.0)

# Godziny pracy / peak (Warszawa). Bramki dla sygnałów wrażliwych na porę.
WORK_HOUR_START = _env_int("DATA_ALERTS_WORK_HOUR_START", 9)
WORK_HOUR_END = _env_int("DATA_ALERTS_WORK_HOUR_END", 23)   # [start, end)
_PEAK_RANGES = ((11, 14), (17, 20))                         # [a, b)

# pos_source uznawane za SENTINEL (fikcja/brak). ⚠ NIE zawiera no_gps/pre_shift
# — to legalna polityka równego traktowania (KANON). None również = sentinel.
_SENTINEL_POS_SOURCES = {"", "none", "null", "unknown", "sentinel", "invalid"}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def in_working_hours(now: datetime) -> bool:
    """Czy `now` (dowolne tz-aware) mieści się w godzinach pracy w Warszawie."""
    h = now.astimezone(WARSAW).hour
    return WORK_HOUR_START <= h < WORK_HOUR_END


def in_peak(now: datetime) -> bool:
    """Czy `now` mieści się w oknie peak (11-14 / 17-20 Warsaw)."""
    h = now.astimezone(WARSAW).hour
    return any(a <= h < b for a, b in _PEAK_RANGES)


# ── Model sygnału ────────────────────────────────────────────────────────────
@dataclass
class Signal:
    name: str
    firing: bool          # próg przekroczony I bramka czasowa otwarta
    value: float          # zmierzona wartość (rate %, wiek min/h, itd.)
    threshold: float
    sample: int           # rozmiar próbki (0 = brak danych → nie firing)
    detail: str           # człowieko-czytelny opis (do logu/telegramu)
    window_open: bool = True   # bramka czasowa (praca/peak) otwarta?


# ── Ewaluatory (CZYSTE funkcje — dane in, Signal out; zero I/O) ───────────────
def _best(rec: dict) -> dict:
    b = rec.get("best")
    return b if isinstance(b, dict) else {}


def _is_sentinel_decision(rec: dict) -> bool:
    """True gdy pozycja WYBRANEGO kuriera jest sentinelowa/fikcyjna.

    Sentinel = coord-poison (0,0) [K5], klasyfikacja v328, albo pos_source z
    zestawu _SENTINEL_POS_SOURCES / None. no_gps/pre_shift ŚWIADOMIE pominięte.
    """
    b = _best(rec)
    if b.get("coord_poison_new_delivery") is True:
        return True
    if b.get("coord_poison_bag_oids"):
        return True
    if rec.get("v328_fail_causes"):
        return True
    ps = b.get("pos_source")
    if ps is None:
        return True
    if isinstance(ps, str) and ps.strip().lower() in _SENTINEL_POS_SOURCES:
        return True
    return False


def evaluate_sentinel_rate(records: List[dict], *,
                           threshold_pct: float = SENTINEL_RATE_PCT,
                           min_sample: int = MIN_SAMPLE) -> Signal:
    n = len(records)
    hits = sum(1 for r in records if _is_sentinel_decision(r))
    rate = (100.0 * hits / n) if n else 0.0
    firing = n >= min_sample and rate > threshold_pct
    detail = (f"sentinel {hits}/{n} = {rate:.1f}% (próg {threshold_pct:.0f}%, "
              f"okno {WINDOW_MIN}min)")
    return Signal("sentinel_rate", firing, round(rate, 2), threshold_pct, n, detail)


def evaluate_empty_pool(records: List[dict], *,
                        threshold_pct: float = EMPTY_POOL_PCT,
                        min_sample: int = MIN_SAMPLE,
                        now: Optional[datetime] = None) -> Signal:
    now = now or _now_utc()
    pools = [r.get("pool_feasible_count") for r in records
             if isinstance(r.get("pool_feasible_count"), int)]
    n = len(pools)
    zeros = sum(1 for p in pools if p == 0)
    rate = (100.0 * zeros / n) if n else 0.0
    window_open = in_working_hours(now)
    firing = window_open and n >= min_sample and rate > threshold_pct
    detail = (f"pusta pula {zeros}/{n} = {rate:.1f}% (próg {threshold_pct:.0f}%, "
              f"okno {WINDOW_MIN}min, praca={window_open})")
    return Signal("empty_pool", firing, round(rate, 2), threshold_pct, n, detail,
                  window_open=window_open)


def evaluate_stale_grafik(fetched_at: Optional[datetime], now: datetime, *,
                          threshold_h: float = GRAFIK_STALE_H) -> Signal:
    window_open = in_working_hours(now)
    if fetched_at is None:
        # Brak/niepersowalny znacznik = traktuj jako podejrzanie stary, ale tylko
        # w godzinach pracy (nocą grafik bywa nietknięty legalnie).
        firing = window_open
        detail = "grafik: brak/niepersowalny fetched_at w schedule_today.json"
        return Signal("stale_grafik", firing, float("inf"), threshold_h, 0, detail,
                      window_open=window_open)
    age_h = (now - fetched_at).total_seconds() / 3600.0
    firing = window_open and age_h > threshold_h
    detail = (f"grafik fetched_at wiek {age_h:.1f}h (próg {threshold_h:.0f}h, "
              f"praca={window_open})")
    return Signal("stale_grafik", firing, round(age_h, 2), threshold_h, 1, detail,
                  window_open=window_open)


def evaluate_stale_gps(positions: Dict[str, dict], now: datetime, *,
                       stale_min: float = GPS_STALE_MIN,
                       frac_pct: float = GPS_STALE_FRAC_PCT,
                       min_fleet: int = GPS_MIN_FLEET) -> Signal:
    window_open = in_working_hours(now)
    ages: List[float] = []
    for entry in positions.values():
        if not isinstance(entry, dict):
            continue
        ts = ledger_io._parse_ts(entry.get("ts"))
        if ts is None:
            continue
        ages.append((now - ts).total_seconds() / 60.0)
    n = len(ages)
    stale = sum(1 for a in ages if a > stale_min)
    frac = (100.0 * stale / n) if n else 0.0
    firing = window_open and n >= min_fleet and frac > frac_pct
    detail = (f"pozycje GPS: {stale}/{n} starsze niż {stale_min:.0f}min = "
              f"{frac:.1f}% (próg {frac_pct:.0f}%, praca={window_open})")
    return Signal("stale_gps", firing, round(frac, 2), frac_pct, n, detail,
                  window_open=window_open)


def evaluate_ledger_stall(latest_ts: Optional[datetime], now: datetime, *,
                          threshold_min: float = STALL_MIN) -> Signal:
    window_open = in_peak(now)
    if latest_ts is None:
        firing = window_open
        detail = "ledger-stall: brak jakiegokolwiek rekordu w ogonie shadow_decisions"
        return Signal("ledger_stall", firing, float("inf"), threshold_min, 0, detail,
                      window_open=window_open)
    gap_min = (now - latest_ts).total_seconds() / 60.0
    firing = window_open and gap_min > threshold_min
    detail = (f"ledger-stall: ostatnia decyzja {gap_min:.1f}min temu "
              f"(próg {threshold_min:.0f}min, peak={window_open})")
    return Signal("ledger_stall", firing, round(gap_min, 2), threshold_min, 1, detail,
                  window_open=window_open)


# ── Warstwa ładowania danych z dysku (I/O) ───────────────────────────────────
def _load_shadow_window(now: datetime, window_min: int) -> List[dict]:
    cutoff = now - timedelta(minutes=window_min)
    try:
        # max_bytes ogranicza odczyt do ogona żywego pliku (semantyka identyczna
        # z pełną ścieżką dla świeżego okna) — tick-strażnik ma być tani.
        return list(ledger_io.iter_shadow_decisions(cutoff, max_bytes=8_000_000))
    except Exception as e:  # noqa: BLE001 — brak danych nie może wywalić monitora
        _log.warning("data_alerts: odczyt shadow_decisions padł: %s", e)
        return []


def _latest_shadow_ts(now: datetime) -> Optional[datetime]:
    """Najświeższy ts w ogonie shadow_decisions (niezależnie od okna rate'ów)."""
    latest: Optional[datetime] = None
    try:
        for rec in ledger_io.iter_shadow_decisions(None, max_bytes=2_000_000):
            ts = ledger_io._rec_ts(rec, ledger_io._TS_FIELDS["shadow"])
            if ts is not None and (latest is None or ts > latest):
                latest = ts
    except Exception as e:  # noqa: BLE001
        _log.warning("data_alerts: odczyt ogona shadow padł: %s", e)
    return latest


def _load_json(path: Path) -> Optional[Any]:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
        _log.warning("data_alerts: nie wczytano %s: %s", path.name, e)
        return None


def _grafik_fetched_at(schedule: Optional[dict]) -> Optional[datetime]:
    if not isinstance(schedule, dict):
        return None
    return ledger_io._parse_ts(schedule.get("fetched_at"))


def _order_assigned_at(o: dict) -> Optional[datetime]:
    """assigned_at zlecenia jako aware dt (ISO z offsetem; naive -> UTC)."""
    s = o.get("assigned_at")
    if not s or not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def evaluate_q1_missing_time(orders: Dict[str, dict], now: datetime, *,
                             dwell_min: float = Q1_DWELL_MIN,
                             min_count: int = Q1_MIN_COUNT,
                             excluded_cids: frozenset = Q1_EXCLUDED_CIDS) -> Signal:
    """Sygnał 6 (Q1): przypisane >= dwell min i wciąż BEZ czasu odbioru.

    Czysta funkcja (orders_state dict -> Signal). Warunek per zlecenie:
    status == "assigned" AND assigned_at starsze niż dwell AND courier_id poza
    excluded (wirtualny Koordynator) AND brak czas_kuriera (hhmm i warsaw).
    Bramka: godziny pracy. Firing: count >= min_count (default 1 — po dwellu
    każdy taki przypadek to realna anomalia: apka/konsola bez czasu, R27 slepe).
    """
    hits: List[str] = []
    considered = 0
    for oid, o in orders.items():
        if not isinstance(o, dict) or o.get("status") != "assigned":
            continue
        cid = str(o.get("courier_id") or "")
        if cid in excluded_cids:
            continue
        assigned = _order_assigned_at(o)
        if assigned is None:
            continue
        considered += 1
        if (now - assigned).total_seconds() / 60.0 < dwell_min:
            continue
        ck = o.get("czas_kuriera_hhmm") or o.get("czas_kuriera_warsaw")
        if ck:
            continue
        hits.append(str(oid))
    window_open = in_working_hours(now)
    firing = window_open and len(hits) >= min_count
    shown = ",".join(sorted(hits)[:5]) + ("..." if len(hits) > 5 else "")
    detail = (f"przypisane bez czasu odbioru >= {dwell_min:.0f}min: {len(hits)} "
              f"z {considered} assigned (oid: {shown or '-'}; prog {min_count}, "
              f"praca={window_open})")
    return Signal("q1_missing_time", firing, float(len(hits)), float(min_count),
                  considered, detail, window_open=window_open)


def _orders_state_dict(raw: Optional[Any]) -> Dict[str, dict]:
    """orders_state.json bywa plaskim dictem {oid: {...}} lub z wrapperem
    {"orders": {...}} (jak konsumuje generator golden) — znormalizuj."""
    if isinstance(raw, dict):
        inner = raw.get("orders", raw)
        if isinstance(inner, dict):
            return {k: v for k, v in inner.items() if isinstance(v, dict)}
    return {}


def collect(now: Optional[datetime] = None, *,
            window_min: int = WINDOW_MIN) -> List[Signal]:
    """Ładuje żywy stan read-only i zwraca listę wszystkich sygnałów."""
    now = now or _now_utc()
    records = _load_shadow_window(now, window_min)
    latest_ts = _latest_shadow_ts(now)
    schedule = _load_json(SCHEDULE_PATH)
    positions = _load_json(COURIER_LAST_POS_PATH) or {}
    if not isinstance(positions, dict):
        positions = {}

    orders = _orders_state_dict(_load_json(ORDERS_STATE_PATH))

    return [
        evaluate_sentinel_rate(records),
        evaluate_empty_pool(records, now=now),
        evaluate_stale_grafik(_grafik_fetched_at(schedule), now),
        evaluate_stale_gps(positions, now),
        evaluate_ledger_stall(latest_ts, now),
        evaluate_q1_missing_time(orders, now),
    ]


# ── Stan edge-trigger (atomowy zapis) ────────────────────────────────────────
def _load_state(path: Path) -> dict:
    data = _load_json(path)
    if not isinstance(data, dict) or "signals" not in data:
        return {"signals": {}, "_meta": {"schema_version": STATE_SCHEMA_VERSION}}
    return data


def _atomic_write_json(path: Path, data: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def decide_emissions(signals: List[Signal], state: dict, now: datetime, *,
                     cooldown_min: float = COOLDOWN_MIN) -> tuple[List[Signal], dict]:
    """Edge-trigger: zwraca (do_wyemitowania, nowy_stan).

    Emituj gdy:
      * przejście not-firing → firing (krawędź), LUB
      * wciąż firing, ale minął cooldown od ostatniej emisji.
    Recovery (firing → not-firing) logujemy jako zdarzenie osobno w run(), tu
    tylko czyścimy stan.
    """
    sig_state: dict = dict(state.get("signals", {}))
    to_emit: List[Signal] = []
    now_ts = now.timestamp()
    for s in signals:
        prev = sig_state.get(s.name, {})
        prev_firing = bool(prev.get("firing"))
        last_alert = prev.get("last_alert_ts")
        emit = False
        if s.firing:
            if not prev_firing:
                emit = True                      # krawędź
            elif last_alert is None or (now_ts - float(last_alert)) >= cooldown_min * 60.0:
                emit = True                      # re-emisja po cooldownie
        new_entry = {
            "firing": bool(s.firing),
            "value": s.value,
            "threshold": s.threshold,
            "sample": s.sample,
            "window_open": s.window_open,
            "last_seen_ts": now_ts,
            "last_alert_ts": (now_ts if emit else last_alert),
            "recovered": (prev_firing and not s.firing),
        }
        sig_state[s.name] = new_entry
        if emit:
            to_emit.append(s)
    new_state = {
        "signals": sig_state,
        "_meta": {"schema_version": STATE_SCHEMA_VERSION, "last_run_ts": now.isoformat()},
    }
    return to_emit, new_state


# ── Flagi (common.flag; NIE os.environ module-level) ─────────────────────────
def _flag(name: str, default: bool) -> bool:
    """Odczyt flagi z hot-reload flags.json (kanon). Fallback = default gdy
    common niedostępny (np. izolowany test środowiska)."""
    try:
        from dispatch_v2.common import flag as _cflag
        return bool(_cflag(name, default))
    except Exception:  # noqa: BLE001
        return default


def _emit_log(signals: List[Signal], to_emit: List[Signal],
              recovered: List[Signal], log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    ts = _now_utc().isoformat()
    lines = []
    for s in to_emit:
        lines.append(f"{ts} ALERT [{s.name}] {s.detail}")
    for s in recovered:
        lines.append(f"{ts} RECOVER [{s.name}] {s.detail}")
    if not lines:
        return
    try:
        with open(log_path, "a") as f:
            for ln in lines:
                f.write(ln + "\n")
    except OSError as e:
        _log.error("data_alerts: zapis logu %s padł: %s", log_path, e)
    for ln in lines:
        _log.warning(ln)


def _emit_telegram(to_emit: List[Signal]) -> None:
    if not to_emit:
        return
    try:
        from dispatch_v2.telegram_utils import send_admin_alert
    except Exception as e:  # noqa: BLE001
        _log.error("data_alerts: import telegram_utils padł: %s", e)
        return
    body = "\n".join(f"• [{s.name}] {s.detail}" for s in to_emit)
    text = f"🔴 Ziomek DATA-ALERT ({len(to_emit)}):\n{body}"
    try:
        send_admin_alert(text, source="data_alerts", priority="high")
    except Exception as e:  # noqa: BLE001
        _log.error("data_alerts: wysyłka telegramu padła: %s", e)


def run(now: Optional[datetime] = None, *,
        enabled: Optional[bool] = None,
        telegram: Optional[bool] = None,
        state_path: Path = DEFAULT_STATE_PATH,
        log_path: Path = DEFAULT_LOG_PATH,
        write_state: bool = True,
        signals: Optional[List[Signal]] = None) -> Dict[str, Any]:
    """Jeden bieg monitora. Domyślnie flagi rozstrzyga common.flag.

    enabled/telegram = None → odczyt z flags.json. Podanie jawne (test) omija
    flagi. `write_state=False` = tryb dry (raport bez mutacji stanu/logu/telegramu).
    """
    now = now or _now_utc()
    if enabled is None:
        enabled = _flag("ENABLE_DATA_ALERTS", False)
    if telegram is None:
        telegram = _flag("DATA_ALERTS_TELEGRAM", False)

    if not enabled:
        return {"enabled": False, "signals": [], "emitted": [], "recovered": [],
                "note": "master flag ENABLE_DATA_ALERTS OFF — no-op"}

    if signals is None:
        signals = collect(now)

    state = _load_state(state_path)
    to_emit, new_state = decide_emissions(signals, state, now)
    recovered = [s for s in signals
                 if new_state["signals"].get(s.name, {}).get("recovered")]

    if write_state:
        _emit_log(signals, to_emit, recovered, log_path)
        if telegram:
            _emit_telegram(to_emit)
        try:
            _atomic_write_json(state_path, new_state)
        except Exception as e:  # noqa: BLE001
            _log.error("data_alerts: zapis stanu %s padł: %s", state_path, e)

    return {
        "enabled": True,
        "telegram": bool(telegram),
        "signals": [asdict(s) for s in signals],
        "emitted": [s.name for s in to_emit],
        "recovered": [s.name for s in recovered],
        "firing": [s.name for s in signals if s.firing],
    }


# ── CLI ──────────────────────────────────────────────────────────────────────
def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Monitor DANOWY Ziomka (sentinel/empty-pool/stale-grafik/"
                    "stale-GPS/ledger-stall). Domyślnie DRY-RUN.")
    p.add_argument("--run", action="store_true",
                   help="realny bieg: edge-trigger + log + (telegram za flagą). "
                        "Domyślnie dry-run (raport, zero zapisu).")
    p.add_argument("--json", action="store_true", help="raport maszynowy (JSON)")
    p.add_argument("--force-enabled", action="store_true",
                   help="wymuś master-flag ON (diagnostyka; omija flags.json)")
    args = p.parse_args(argv)

    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    now = _now_utc()
    try:
        if args.run:
            enabled = True if args.force_enabled else None
            res = run(now=now, enabled=enabled, write_state=True)
        else:
            # DRY: policz sygnały, pokaż, ZERO zapisu/telegramu/edge-mutacji.
            sigs = collect(now)
            res = {
                "enabled": None,
                "dry_run": True,
                "signals": [asdict(s) for s in sigs],
                "firing": [s.name for s in sigs if s.firing],
                "window_hint": {
                    "working_hours": in_working_hours(now),
                    "peak": in_peak(now),
                    "warsaw_hour": now.astimezone(WARSAW).hour,
                },
            }
    except Exception as e:  # noqa: BLE001 — cron-safe
        _log.error("data_alerts: nieoczekiwany błąd: %s: %s", type(e).__name__, e)
        return 1

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
    else:
        firing = res.get("firing", [])
        print(f"data_alerts @ {now.isoformat()} | Warsaw hour="
              f"{now.astimezone(WARSAW).hour} work={in_working_hours(now)} "
              f"peak={in_peak(now)}")
        for s in res.get("signals", []):
            mark = "🔴" if s["firing"] else ("· " if s["window_open"] else "○ ")
            print(f"  {mark} {s['name']:14} val={s['value']} thr={s['threshold']} "
                  f"n={s['sample']} | {s['detail']}")
        if "emitted" in res:
            print(f"  emitted={res['emitted']} recovered={res['recovered']}")
        elif firing:
            print(f"  FIRING (dry): {firing}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
