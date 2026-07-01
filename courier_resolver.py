"""Courier resolver - fleet snapshot z GPS + fallback last-click.

Priorytet zrodel pozycji kuriera (V3.1 P0.3):
1. Traccar GPS (swieze < 5 min)
2. Aktywny bag (picked_up > assigned, najnowszy timestamp)
3. Last delivered (TYLKO gdy bag pusty)
4. None = skip w dispatchu

Pure dataclass-based, lazy-load GPS aby nie blokowac dispatchu gdy Traccar offline.
"""
import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dispatch_v2.common import (
    setup_logger, now_iso, parse_panel_timestamp, DT_MIN_UTC, flag,
    coords_in_bialystok_bbox, decision_flag,
    ENABLE_F4_COURIER_POS_PICKUP_PROXY,
    ENABLE_F4_COURIER_POS_INTERP,
    ENABLE_CHECKPOINT_TS_WARSAW_PARSE,
)
from dispatch_v2 import state_machine
from dispatch_v2 import osrm_client
from dispatch_v2 import gps_quality

_log = setup_logger("courier_resolver", "/root/.openclaw/workspace/scripts/logs/courier_resolver.log")


def _f4_flag(name: str) -> bool:
    """ETAP 4 (2026-06-10, Z-04): flaga F4 wspólna cross-proces.

    flags.json (hot-reload) → atrybut TEGO modułu (importowany env-default;
    testy patchują courier_resolver.ENABLE_F4_*) → False.
    """
    return bool(flag(name, globals().get(name, False)))


def _parse_checkpoint_ts(raw) -> Optional[datetime]:
    """Parsuje state'owy timestamp checkpointu odbioru/doręczenia
    (`picked_up_at`/`delivered_at` = NAIWNY czas Warsaw z panelu Rutcom) → aware UTC.

    Flaga ENABLE_CHECKPOINT_TS_WARSAW_PARSE (default OFF, czytana _f4_flag):
      OFF — legacy: fromisoformat + tzinfo=UTC (naive traktowany jak UTC). Dla
            świeżego odbioru elapsed/age UJEMNE → interp + recent-activity martwe,
            ZOMBIE-guard zaniża wiek o offset Warszawy. BAJT-IDENTYCZNE ze stanem
            sprzed fixu (mirror granicy OrderSim sprzed parse_panel_timestamp).
      ON  — kanoniczny parse_panel_timestamp (naive→Warszawa; 'T'/offset→UTC) — jak
            granica OrderSim w dispatch_pipeline → poprawne elapsed/age → predykcja
            pozycji no-GPS ożywa (interp odpala, świeże checkpointy używane, ghost
            łapany od realnego progu).
    Zwraca aware-UTC datetime albo None (fail-soft — caller decyduje, jak dotąd).
    """
    if raw is None:
        return None
    if _f4_flag("ENABLE_CHECKPOINT_TS_WARSAW_PARSE"):
        return parse_panel_timestamp(raw)
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


KURIER_PINY_PATH = "/root/.openclaw/workspace/dispatch_state/kurier_piny.json"
COURIER_NAMES_PATH = "/root/.openclaw/workspace/dispatch_state/courier_names.json"
KURIER_IDS_PATH = "/root/.openclaw/workspace/dispatch_state/kurier_ids.json"
# PANEL-CANON (2026-06-10): {pełne imię: cid} — autorytatywne wiązanie cid↔nazwisko,
# współdzielone z panelem admin (schedule_grid/fleet_state). Najwyższy priorytet w
# _load_courier_names → eliminuje dwuznaczne skróty u źródła.
GRAFIK_FULL_NAMES_PATH = "/root/.openclaw/workspace/dispatch_state/grafik_full_names.json"
GPS_POSITIONS_PATH = "/root/.openclaw/workspace/dispatch_state/gps_positions.json"
GPS_POSITIONS_PWA_PATH = "/root/.openclaw/workspace/dispatch_state/gps_positions_pwa.json"
GPS_FRESHNESS_MIN = 5  # GPS nowszy niz 5 min = aktualny

# V3.28 P3 (B) — panel_packs cache od panel_watcher (per-tick atomic write).
# Source of truth dla "kto faktycznie wozi" niezależnie od orders_state.cid lag.
PANEL_PACKS_CACHE_PATH = "/root/.openclaw/workspace/dispatch_state/panel_packs_cache.json"
PANEL_PACKS_CACHE_MAX_AGE_S = 120.0  # >2 min = cache stale, nie używaj

def _load_panel_packs_cache() -> Tuple[Optional[datetime], Dict[str, list], Optional[float]]:
    """V3.28 P3 (B) — read panel_packs cache pisany przez panel_watcher.

    Returns: (ts_utc, packs_dict {nick→[oid_str]}, age_seconds) or (None, {}, None)
    gdy cache missing/corrupt/stale.
    """
    try:
        with open(PANEL_PACKS_CACHE_PATH, "r", encoding="utf-8") as _f:
            _data = json.load(_f)
        _ts_str = _data.get("ts") or ""
        if not _ts_str:
            return (None, {}, None)
        _ts = datetime.fromisoformat(_ts_str.replace("Z", "+00:00"))
        if _ts.tzinfo is None:
            _ts = _ts.replace(tzinfo=timezone.utc)
        _age_s = (datetime.now(timezone.utc) - _ts).total_seconds()
        return (_ts, _data.get("packs") or {}, _age_s)
    except Exception:
        return (None, {}, None)


# Synthetic pos dla kuriera bez GPS i bez historii zleceń (no_gps fallback).
# Nie wykluczamy go z dispatchu; dispatch_pipeline normalizuje km_to_pickup
# do średniej floty, a travel_min do max(prep, 15 min).
BIALYSTOK_CENTER = (53.1325, 23.1688)

# ── Persistent last-known-position store (FIX 2026-06-08) ────────────────────
# Problem (case Piotr Zaw 470, order 479289): kurier który chwilę wcześniej był
# aktywny (dostawa ~10 min temu) traci pozycję do BIALYSTOK_CENTER fiction, bo
# jego order zniknął z orders_state (prune terminalnych LUB cid=None unlink z
# V3.15 lag) ZANIM 30-min recent-activity fallback zdążył go użyć → pos_source=
# no_gps → kara +offset + _demote_blind_empty → mniej zleceń. Store jest
# courier-keyed i NIEZALEŻNY od orders_state, więc przeżywa oba zdarzenia.
# W luce GPS odtwarza OSTATNIE ŻYWE źródło pozycji (last_delivered/last_picked_up_*/
# last_assigned_pickup) — istniejący enum, więc ZERO nowych interakcji w
# scoringu/feasibility/telegramie. Bounded TTL (≤25 min) → zachowuje guard
# FAIL-02 (kurier dłużej ciemny niż TTL nadal spada do no_gps i jest demote'owany,
# brak >25-min phantom). Pozycje odtworzone ze store NIE są re-persystowane
# (laundering guard) — starzeją się i wygasają. Flag-gated, fail-soft, atomic.
COURIER_LAST_POS_PATH = "/root/.openclaw/workspace/dispatch_state/courier_last_pos.json"
LAST_KNOWN_POS_TTL_MIN = 25.0      # wpis starszy niż TTL → ignoruj (fall do no_gps)
LAST_KNOWN_POS_PRUNE_MIN = 360.0   # wpisy starsze niż 6h usuwane przy zapisie
# Źródła uznane za "żywą" pozycję — TYLKO te lądują w store. no_gps/pre_shift/none
# (brak realnej pozycji) NIGDY nie są zapisywane.
_LAST_POS_GOOD_SOURCES = frozenset({
    "gps", "last_picked_up_interp", "last_picked_up_pickup",
    "last_picked_up_delivery", "last_assigned_pickup",
    "last_delivered", "last_picked_up_recent",
})


# Zamrożona kopia domyślnej ścieżki — do wykrycia "test patchnął ścieżkę na tmp".
_DEFAULT_COURIER_LAST_POS_PATH = COURIER_LAST_POS_PATH


def _store_blocked_under_test() -> bool:
    """Guard: pytest na PROD boxie nie może czytać/pisać PRODUKCYJNEGO store —
    inaczej testowe cid (999 Widmo, 520, 888) lądują w dispatch state → ryzyko
    widma. Testy które JAWNIE patchują COURIER_LAST_POS_PATH na tmp (round-trip)
    NIE są blokowane (ścieżka != domyślna)."""
    return ("PYTEST_CURRENT_TEST" in os.environ
            and COURIER_LAST_POS_PATH == _DEFAULT_COURIER_LAST_POS_PATH)


def _load_last_known_pos() -> Dict[str, dict]:
    """Read courier_last_pos.json → {cid: {lat,lon,ts,source}}. Fail-soft → {}."""
    if _store_blocked_under_test():
        return {}
    try:
        with open(COURIER_LAST_POS_PATH, "r", encoding="utf-8") as _f:
            _d = json.load(_f)
        return _d if isinstance(_d, dict) else {}
    except Exception:
        return {}


def _lp_entry_ts(entry: dict) -> datetime:
    """ts wpisu store → aware UTC datetime (DT_MIN_UTC gdy brak/zły)."""
    try:
        _t = datetime.fromisoformat(str(entry.get("ts", "")).replace("Z", "+00:00"))
        return _t.replace(tzinfo=timezone.utc) if _t.tzinfo is None else _t
    except Exception:
        return DT_MIN_UTC


def _save_last_known_pos(store: Dict[str, dict]) -> None:
    """Atomic write z merge-by-ts (multi-proces safe) + prune. Fail-soft —
    NIGDY nie wywala hot path build_fleet_snapshot."""
    if _store_blocked_under_test():
        return
    try:
        disk = _load_last_known_pos()
        merged = dict(disk)
        for cid, ent in store.items():
            if not isinstance(ent, dict):
                continue
            cur = merged.get(cid)
            # newer ts wygrywa — proces ze stałą daną nie cofnie świeższej
            if cur is None or _lp_entry_ts(ent) >= _lp_entry_ts(cur):
                merged[cid] = ent
        now = datetime.now(timezone.utc)
        for cid in list(merged.keys()):
            if (now - _lp_entry_ts(merged[cid])).total_seconds() / 60.0 > LAST_KNOWN_POS_PRUNE_MIN:
                del merged[cid]
        _dir = os.path.dirname(COURIER_LAST_POS_PATH)
        _fd, _tmp = tempfile.mkstemp(dir=_dir, suffix=".tmp")
        with os.fdopen(_fd, "w", encoding="utf-8") as _f:
            json.dump(merged, _f, ensure_ascii=False)
            _f.flush()
            os.fsync(_f.fileno())
        os.replace(_tmp, COURIER_LAST_POS_PATH)
    except Exception as _e:
        _log.warning(f"_save_last_known_pos fail: {_e}")


def _rescue_from_last_pos(entry, now_utc: datetime):
    """Pure (testowalne, zero I/O): waliduj wpis store i zwróć
    ((lat,lon), source, age_min) gdy świeży (<TTL) i w bboxie Białegostoku.
    None gdy brak / za stary / skażony. source spoza dozwolonych → last_delivered."""
    if not isinstance(entry, dict):
        return None
    try:
        lat = float(entry["lat"])
        lon = float(entry["lon"])
    except (KeyError, ValueError, TypeError):
        return None
    age = (now_utc - _lp_entry_ts(entry)).total_seconds() / 60.0
    if age < 0 or age >= LAST_KNOWN_POS_TTL_MIN:
        return None
    if not coords_in_bialystok_bbox((lat, lon)):
        return None
    src = entry.get("source")
    if src not in _LAST_POS_GOOD_SOURCES:
        src = "last_delivered"
    return ((lat, lon), src, age)


# ── GPS-02 (audyt 2026-06-10): filtr jakości fixu GPS — SHADOW-first ─────────
# Compute-zawsze (telemetria gps_quality logowana niezależnie od flagi); efekt
# na flotę TYLKO gdy ENABLE_GPS_ACCURACY_TELEPORT_FILTER=True (domyślnie OFF).
# Cel: odrzucać ZŁY fix (słaba dokładność / teleport), NIGDY brak GPS (korekta
# Adriana 13.06 — brak GPS = celowa polityka treningowa). Logika w gps_quality.py
# (czyste funkcje); tu tylko I/O kotwicy (poprzedni fix GPS ze store) + shadow log.
GPS_QUALITY_SHADOW_LOG_PATH = "/root/.openclaw/workspace/dispatch_state/gps_quality_shadow.jsonl"
# Zamrożona kopia domyślnej ścieżki — wykrycie "test patchnął na tmp" (jak store).
_DEFAULT_GPS_QUALITY_SHADOW_LOG_PATH = GPS_QUALITY_SHADOW_LOG_PATH
# Kotwica teleport-detekcji = ostatnia wiarygodna pozycja GPS z last-known-pos
# store (zapisywana z source="gps" przy poprzednim ticku). Współgra ze store
# (nie duplikujemy historii pozycji), nie wymaga nowego pliku stanu.
_GPS_QUALITY_ANCHOR_SOURCES = frozenset({"gps"})


def _gps_quality_anchor(store_entry, new_age_min: float, now_utc: datetime):
    """Zwróć (anchor_pos, dt_seconds, anchor_age_min) dla teleport-detekcji
    z wpisu last-known-pos store, albo (None, None, None).

    Kotwicą jest TYLKO poprzedni fix GPS (source=="gps") — kotwice z bagu/
    historii (delivery_coords itp.) są geometrycznie grubsze i dałyby
    fałszywe teleporty. dt = wiek_kotwicy − wiek_nowego_fixu (oba od now).
    Pure (zero I/O), testowalne. Brak/zły wpis/za stara kotwica → None.
    """
    if not isinstance(store_entry, dict):
        return (None, None, None)
    if store_entry.get("source") not in _GPS_QUALITY_ANCHOR_SOURCES:
        return (None, None, None)
    try:
        a_lat = float(store_entry["lat"])
        a_lon = float(store_entry["lon"])
    except (KeyError, ValueError, TypeError):
        return (None, None, None)
    anchor_age_min = (now_utc - _lp_entry_ts(store_entry)).total_seconds() / 60.0
    if anchor_age_min <= 0:
        return (None, None, None)
    # dt między fixami = ile czasu minęło od kotwicy do nowego fixu.
    dt_seconds = (anchor_age_min - new_age_min) * 60.0
    return ((a_lat, a_lon), dt_seconds, anchor_age_min)


def _log_gps_quality_shadow(kid: str, verdict, now_iso_str: str, flag_on: bool,
                            new_pos, new_age_min: float) -> None:
    """Append-only shadow log werdyktu jakości GPS. Fail-soft (NIGDY nie wywala
    hot path). Pisze ZAWSZE (compute-shadow), niezależnie od flagi — pole
    `filter_active` mówi czy werdykt miałby efekt na flotę po flipie.

    Guard (lekcja #176): pytest na PROD boxie NIE może pisać do PRODUKCYJNEGO
    shadow logu (testowe cid 888/520/470 → zatruwają plik kalibracyjny). Test
    który JAWNIE patchuje ścieżkę na tmp (≠ domyślna) NIE jest blokowany."""
    if ("PYTEST_CURRENT_TEST" in os.environ
            and GPS_QUALITY_SHADOW_LOG_PATH == _DEFAULT_GPS_QUALITY_SHADOW_LOG_PATH):
        return
    try:
        rec = {
            "ts": now_iso_str,
            "kid": str(kid),
            "filter_active": bool(flag_on),
            "pos": [round(new_pos[0], 6), round(new_pos[1], 6)] if new_pos else None,
            "fix_age_min": round(new_age_min, 2),
        }
        rec.update(verdict.to_log_dict())
        _dir = os.path.dirname(GPS_QUALITY_SHADOW_LOG_PATH)
        _fd, _tmp = tempfile.mkstemp(dir=_dir, suffix=".tmp")
        # append-safe: czytamy istniejący? Nie — JSONL append przez open('a').
        os.close(_fd)
        try:
            os.unlink(_tmp)
        except OSError:
            pass
        with open(GPS_QUALITY_SHADOW_LOG_PATH, "a", encoding="utf-8") as _f:
            _f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as _e:
        _log.warning(f"_log_gps_quality_shadow fail: {_e}")


# Pre-shift: kurier którego zmiana zaczyna się w ciągu N min może już dostać
# propozycję — czas deklarowany uwzględnia jego shift_start.
PRE_SHIFT_WINDOW_MIN = 50

# Priorytet źródeł pozycji — niższe = lepsze. Używane do dedupliacji
# kurierów występujących pod kilkoma courier_id (legacy + panel).
POS_SOURCE_PRIORITY = {
    "gps": 0,
    "last_picked_up_interp": 1,    # F4 Krok 2 — interpolacja na nodze pickup→delivery
    "last_picked_up_pickup": 1,    # F4 Krok 1 — punkt realnie odwiedzony
    "last_picked_up_delivery": 1,
    "last_assigned_pickup": 2,
    "last_delivered": 3,
    "last_picked_up_recent": 3,  # tier z last_delivered (świeże <30 min)
    "no_gps": 4,
    "pre_shift": 5,
    "none": 6,
    None: 6,
}
TRACCAR_URL = os.environ.get("TRACCAR_URL", "http://localhost:8082")
TRACCAR_USER = os.environ.get("TRACCAR_USER", "")
TRACCAR_PASS = os.environ.get("TRACCAR_PASS", "")


@dataclass
class CourierState:
    courier_id: str
    pos: Optional[Tuple[float, float]] = None       # aktualna lokalizacja (lat, lon)
    pos_source: str = "none"                         # gps | last_delivered | last_picked_up | last_assigned | pin | none
    pos_age_min: Optional[float] = None              # sekund/60 od pomiaru
    bag: List[Dict] = field(default_factory=list)    # ordery w bagu (jako dict z state)
    shift_end: Optional[datetime] = None             # koniec zmiany (None = nieznane)
    shift_start: Optional[datetime] = None           # V3.25: początek zmiany (Warsaw aware) dla R-01 PRE-CHECK
    shift_start_min: Optional[float] = None          # minuty od now do startu zmiany (pre_shift)
    name: Optional[str] = None                       # czytelna nazwa z kurier_piny
    # V3.19h BUG-4: tier info z courier_tiers.json (None gdy cid nieznany).
    tier_bag: Optional[str] = None                   # gold | std+ | std | slow | new (V3.25)
    tier_cap_override: Optional[Dict] = None         # per-pora override np. Gabriel {peak:4, ...}
    tier_label: Optional[str] = None                 # V3.25: 'new' dla nowych kurierów (R-04 NEW-COURIER-CAP)
    # V3.28 P3 (B) — panel_packs ground-truth signal (Adrian doktryna 2026-05-10).
    # Gdy panel widzi nick→[oids] ALE state.bag pusty (cid=None lag) — divergence
    # signal dla shadow_dispatcher score penalty (Adrian's 472242 Baanko case).
    panel_packs_oids_signal: List[str] = field(default_factory=list)
    panel_packs_cache_age_s: Optional[float] = None
    # V3.28 P4 — coordinator role flag (Adrian doktryna 2026-05-10 wieczór).
    # Bartek O. (cid=123) hybrid: peak jeździ, off-peak dispatchuje. Pipeline nie
    # wie o tej roli → 100% propozycji do niego gdy bag=0+gold tier. Activation:
    # auto na pierwszym COURIER_ASSIGNED dnia LUB manual TG `<nick> start/stop`.
    is_coordinator: bool = False
    coordinator_active: bool = False  # True = jeździ aktywnie dziś
    # Faza 4 (D5, 2026-05-18): True gdy cs.bag został odbudowany z panel_packs
    # (orders_state miał bag pusty mimo że panel widzi kuriera z bagiem).
    bag_from_panel_packs: bool = False
    # D2 (audyt 2026-05-28): True gdy grafik wykryty jako STALE (is_schedule_stale)
    # w momencie budowy floty — feasibility soft-degraduje zamiast hard-reject
    # NO_ACTIVE_SHIFT gdy shift_end None z powodu awarii pliku grafiku.
    schedule_source_stale: bool = False
    # FIX 2026-06-08: True gdy pozycja odtworzona z persistent last-known-pos
    # store (luka GPS). Save-block NIE re-persystuje takich (laundering guard).
    pos_from_store: bool = False

    def to_dict(self):
        return {
            "courier_id": self.courier_id,
            "pos": list(self.pos) if self.pos else None,
            "pos_source": self.pos_source,
            "pos_age_min": round(self.pos_age_min, 1) if self.pos_age_min is not None else None,
            "bag_size": len(self.bag),
            "bag_oids": [o.get("order_id") or o.get("id") for o in self.bag],
            "name": self.name,
            "tier_bag": self.tier_bag,
            "bag_from_panel_packs": self.bag_from_panel_packs,
        }


# V3.19h BUG-4: cached loader dla courier_tiers.json z mtime invalidation.
_COURIER_TIERS_CACHE: Optional[Dict] = None
_COURIER_TIERS_MTIME: Optional[float] = None
COURIER_TIERS_PATH = "/root/.openclaw/workspace/dispatch_state/courier_tiers.json"


def _load_courier_tiers() -> Dict:
    """V3.19h: load courier_tiers.json z cache + mtime invalidation.
    Returns {} gdy plik nie istnieje (tier='std' default dla wszystkich).
    """
    global _COURIER_TIERS_CACHE, _COURIER_TIERS_MTIME
    import os
    try:
        mt = os.path.getmtime(COURIER_TIERS_PATH)
    except (FileNotFoundError, OSError):
        _COURIER_TIERS_CACHE = {}
        _COURIER_TIERS_MTIME = None
        return _COURIER_TIERS_CACHE
    if _COURIER_TIERS_CACHE is None or mt != _COURIER_TIERS_MTIME:
        try:
            with open(COURIER_TIERS_PATH) as f:
                _COURIER_TIERS_CACHE = json.load(f)
            _COURIER_TIERS_MTIME = mt
        except (json.JSONDecodeError, OSError) as e:
            _log.warning(f"_load_courier_tiers fail: {e}")
            _COURIER_TIERS_CACHE = {}
            _COURIER_TIERS_MTIME = None
    return _COURIER_TIERS_CACHE


def _load_kurier_piny() -> Dict:
    """kurier_piny.json = {PIN_4digit: name} (legacy, ID space różny od courier_id).

    UWAGA: keys to PIN-y, nie courier_id. Większość `piny.get(courier_id)`
    zwraca None. Zachowane jako fallback dla backwards compat.
    """
    try:
        with open(KURIER_PINY_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        _log.warning(f"_load_kurier_piny fail: {e}")
        return {}


def _load_courier_names() -> Dict:
    """courier_names.json = {courier_id_str: name} (P0.5b F1.1 fix).

    V3.25 (STEP A.2): MERGE inverse(kurier_ids.json) lower-priority +
    courier_names.json higher-priority. Defensywny fallback gdy
    courier_names jest stale wobec kurier_ids (np. Albert Dec cid=414
    dodany 21.04 do kurier_ids ale nie do courier_names → cs.name=None
    → silent bypass schedule check H1).

    Conflict policy: courier_names.json wins (assumed manually curated).
    """
    merged: Dict[str, str] = {}
    try:
        with open(KURIER_IDS_PATH) as f:
            ids = json.load(f)
        for name, cid in ids.items():
            cid_str = str(cid)
            if cid_str not in merged:
                merged[cid_str] = name
    except Exception as e:
        _log.warning(f"_load_courier_names: kurier_ids fallback fail: {e}")
    try:
        with open(COURIER_NAMES_PATH) as f:
            names = json.load(f)
        for cid_str, name in names.items():
            merged[cid_str] = name
    except FileNotFoundError:
        pass
    except Exception as e:
        _log.warning(f"_load_courier_names: courier_names fail: {e}")
    # PANEL-CANON (2026-06-10): grafik_full_names.json = {pełne imię: cid} —
    # autorytatywne, JEDNOZNACZNE wiązanie cid↔pełne nazwisko, którego używa też
    # panel admin (schedule_grid/fleet_state) do związania grafiku z kurierami.
    # NAJWYŻSZY priorytet: nadpisuje skróty z kurier_ids/courier_names ("Rafał Ja"
    # → Jankowski|Jabłoński AMBIGUOUS) pełnym imieniem trafiającym WPROST w klucz
    # grafiku → match_courier resolwuje, eliminuje całą klasę kolizji skrótów u
    # źródła. Flag-gated (hot-reload kill-switch) + fail-soft (brak/zły plik →
    # zachowanie sprzed zmiany, courier_names.json zostaje backstopem).
    if flag("ENABLE_GRAFIK_FULL_NAMES_SOURCE", default=True):
        try:
            with open(GRAFIK_FULL_NAMES_PATH, encoding="utf-8") as f:
                gfn = json.load(f)
            for full_name, cid in gfn.items():
                if isinstance(full_name, str) and full_name.strip():
                    merged[str(cid)] = full_name
        except FileNotFoundError:
            pass
        except Exception as e:
            _log.warning(f"_load_courier_names: grafik_full_names fail: {e}")
    return merged


def _load_gps_positions() -> Dict:
    """Merge GPS positions — PWA primary, legacy Traccar fallback (F1.5).

    Returns: {courier_id_str: {lat, lon, accuracy, timestamp, source, name?}}

    Źródła:
    - gps_positions_pwa.json: {courier_id: {...}} — PWA server (F1.5, fresh)
    - gps_positions.json: {name: {...}} — legacy Traccar (imiona jako key)

    Merge strategy:
    1. Load PWA — klucze już są courier_id (direct)
    2. Load legacy — mapuj name → courier_id via kurier_ids.json
    3. PWA wygrywa przy konflikcie (newer data, clean format)
    """
    merged: Dict = {}

    # 1. PWA primary (courier_id keys)
    try:
        with open(GPS_POSITIONS_PWA_PATH) as f:
            pwa = json.load(f)
        for cid, rec in pwa.items():
            merged[str(cid)] = rec
    except FileNotFoundError:
        pass
    except Exception as e:
        _log.warning(f"_load_gps_positions PWA fail: {e}")

    # 2. Legacy fallback (name keys → courier_id via kurier_ids)
    # A1 (audit ARCHITECTURE 2026-05-07): kurier_ids.json fail = silent → all
    # name-keyed couriers fallback'ują do empty dict (mass dispatch failure).
    # HIGH severity. Dedup once-per-process żeby nie spammować przy stałym
    # corrupt/missing file (Lekcja #32, MP-#10 pattern).
    try:
        with open(KURIER_IDS_PATH) as f:
            name_to_id = json.load(f)
    except Exception as _e:
        name_to_id = {}
        if not getattr(_load_gps_positions, "_kurier_ids_warned", False):
            _log.error(
                f"kurier_ids.json load fail ({type(_e).__name__}: {_e}) — "
                f"name-keyed fallback EMPTY, dispatch może gubić kurierów"
            )
            _load_gps_positions._kurier_ids_warned = True

    try:
        with open(GPS_POSITIONS_PATH) as f:
            legacy = json.load(f)
        for name, rec in legacy.items():
            cid = name_to_id.get(name)
            if cid is None:
                continue
            cid_str = str(cid)
            if cid_str in merged:
                continue  # PWA primary wins
            merged[cid_str] = rec
    except FileNotFoundError:
        pass
    except Exception as e:
        _log.warning(f"_load_gps_positions legacy fail: {e}")

    # L2.1 sentinel-ingest (2026-07-01, K5a read-side): wpisy z pozycją-sentinelem
    # ((0,0)/NaN/poza-bbox) NIE wchodzą do floty jako „dane" — kurier bez wpisu
    # = no_gps = polityka równego traktowania, nie zatruta geometria. Chroni po
    # STARYCH plikach store'a i legacy Traccar (ingest gps_server łapie tylko
    # nowe POSTy). Warning dedup: tylko gdy zbiór odrzuconych się ZMIENIA.
    if decision_flag("ENABLE_COORD_SENTINEL_INGEST_GUARD"):
        _bad = [
            cid for cid, rec in merged.items()
            if not coords_in_bialystok_bbox(
                (rec.get("lat"), rec.get("lon")) if isinstance(rec, dict) else None)
        ]
        if _bad:
            for cid in _bad:
                del merged[cid]
            _bad_key = tuple(sorted(_bad))
            if getattr(_load_gps_positions, "_coord_guard_last", None) != _bad_key:
                _log.warning(
                    f"COORD_INGEST_GUARD gps-load: odrzucone pozycje-sentinele "
                    f"cids={sorted(_bad)}"
                )
                _load_gps_positions._coord_guard_last = _bad_key
        else:
            _load_gps_positions._coord_guard_last = None

    return merged


def _latest_order_by_event(orders: List[Dict], event_field: str) -> Optional[Dict]:
    """Zwraca order z najpozniejszym event_field (delivered_at/picked_up_at/assigned_at)."""
    filtered = [o for o in orders if o.get(event_field)]
    if not filtered:
        return None
    return max(filtered, key=lambda o: o.get(event_field, ""))


def _bag_sort_key(o: dict) -> tuple:
    """Klucz sortowania orderow w aktywnym bagu: picked_up > assigned, nowszy > starszy.

    Zwraca tuple (status_priority, parsed_datetime) dla stabilnego sortowania.
    Module-level: alokacja raz, wolany N razy bez GC pressure.
    """
    is_picked = 1 if o.get("status") == "picked_up" else 0
    ts_raw = o.get("picked_up_at") if is_picked else o.get("assigned_at")
    ts_dt = parse_panel_timestamp(ts_raw) or DT_MIN_UTC
    return (is_picked, ts_dt)


def _bag_not_stale(order: Dict, now_utc: datetime) -> bool:
    """TTL check na active_bag entries (V3.14 bag integrity fix).

    Zwraca False jeśli order prawdopodobnie został już delivered w panelu,
    ale panel_watcher reconcile jeszcze nie dogonił (lag 15-90 min przez
    MAX_RECONCILE_PER_CYCLE=25/tick + FIFO closed_ids queue).

    Reguły:
    - Czasówka z `pickup_at_warsaw` w przyszłości → zachowaj (legitymnie assigned
      przed pickupem, brak lag issue)
    - status=assigned + updated_at/assigned_at > BAG_STALE_THRESHOLD_MIN ago
      → STALE (filter out)
    - status=picked_up + picked_up_at > BAG_STALE_THRESHOLD_MIN ago
      → STALE (prawdopodobnie delivered)
    - Brak timestampu lub parse fail → defensywnie zachowaj (lepiej false positive
      niż zgubić legitymny bag entry)
    """
    try:
        from dispatch_v2.common import (
            BAG_STALE_THRESHOLD_MIN as _threshold,
            STRICT_BAG_RECONCILIATION as _strict,
        )
    except Exception:
        return True  # import fail = defensywnie zachowaj
    if not _strict:
        return True  # legacy mode — bez TTL

    status = order.get("status")

    # Czasówka: pickup_at w przyszłości → legitymnie assigned (nie stale)
    # A1: parse fail dawniej silent → log warn dedup-by-class cap=50 (Lekcja #32).
    pu_str = order.get("pickup_at_warsaw")
    if pu_str:
        try:
            pu_dt = datetime.fromisoformat(pu_str)
            if pu_dt.tzinfo is None:
                from zoneinfo import ZoneInfo
                pu_dt = pu_dt.replace(tzinfo=ZoneInfo("Europe/Warsaw"))
            if pu_dt > now_utc:
                return True  # still waiting for pickup, not stale
        except Exception as _e:
            seen = getattr(_bag_not_stale, "_warned_pu", set())
            key = (type(_e).__name__, str(pu_str)[:40])
            if key not in seen and len(seen) < 50:
                _log.warning(f"pickup_at_warsaw parse fail ({type(_e).__name__}: {_e}) input={pu_str!r}")
                seen.add(key)
                _bag_not_stale._warned_pu = seen

    # ZOMBIE-01 (audyt 2026-06-03): order z `picked_up_at` starszym niż próg = ghost
    # NIEZALEŻNIE od statusu. Luka strukturalna: status=assigned z zachowanym (starym)
    # picked_up_at przechodził filtr (gałąź assigned niżej używa updated_at, świeży),
    # ale route_simulator/feasibility anchorują elapsed na picked_up_at
    # (is_picked = picked_up_at is not None) → absurd carry (oid=476621: 1463min/24h)
    # zatruwa r6_max_bag_time (→ scoring carry penalty) + C2 shadow + per-order projekcje
    # (NIE hard-reject: is_picked→tracked, ale truje metrykę). Filtr PRZY ŹRÓDLE (bag) >
    # TTL w stanie; ten sam próg co reszta (delivery >threshold od pickupu = patologia
    # daleko poza R6=35). picked_up_at w przyszłości / parse-fail → NIE filtruj tu
    # (gałąź per-status oceni). Kill-switch flags.json hot-reload.
    if flag("ENABLE_ZOMBIE_PICKUP_AT_GUARD", default=True):
        _pu_ghost = order.get("picked_up_at")
        if _pu_ghost:
            _pu_dt = _parse_checkpoint_ts(_pu_ghost)  # parse fail → None → gałąź per-status oceni
            if _pu_dt is not None and (now_utc - _pu_dt).total_seconds() / 60.0 > _threshold:
                _zseen = getattr(_bag_not_stale, "_warned_zombie", set())
                _zoid = str(order.get("order_id") or "?")
                if _zoid not in _zseen and len(_zseen) < 50:
                    _log.warning(
                        f"ZOMBIE_PICKUP_GUARD oid={_zoid} status={status} picked_up_at "
                        f">{_threshold}min → STALE (ghost: odebrane dawno, nigdy nie "
                        f"domknięte — nie zatruwa carry/R6)")
                    _zseen.add(_zoid)
                    _bag_not_stale._warned_zombie = _zseen
                return False

    # Timestamp wyboru per status
    if status == "assigned":
        ts_str = order.get("updated_at") or order.get("assigned_at")
    elif status == "picked_up":
        ts_str = order.get("picked_up_at") or order.get("updated_at")
    else:
        return True  # non-active statuses upstream filtered

    if not ts_str:
        return True  # brak timestampu = defensywnie zachowaj

    # picked_up_at = Warsaw-naive (helper poprawia przy fl: ON); updated_at/assigned_at
    # = aware-UTC (parse_panel_timestamp i legacy dają to samo). Bliźniak ZOMBIE-guard.
    ts = _parse_checkpoint_ts(ts_str)
    if ts is None:
        return True  # parse fail = defensywnie zachowaj
    age_min = (now_utc - ts).total_seconds() / 60.0
    return age_min < _threshold


def _compute_interp_pos(
    order: Dict, now_utc: datetime
) -> Optional[Tuple[Tuple[float, float], str]]:
    """F4 Krok 2 (Opcja C): pozycja kuriera = interpolacja liniowa
    pickup_coords → delivery_coords po f = clamp(elapsed/eta_leg, 0, 1).

    elapsed = now − picked_up_at; eta_leg = OSRM duration_min pickup→delivery.
    Zwraca ((lat, lon), "last_picked_up_interp") albo None gdy fail-soft
    (caller pada na Krok 1 → legacy). Fail-soft pokrywa:
      • brak pickup_coords / delivery_coords / picked_up_at
      • picked_up_at parsuje się błędnie
      • elapsed < 0 (sanity: ts w przyszłości)
      • OSRM exception lub duration_min ≤ 0 (degenerat)
    """
    pickup = order.get("pickup_coords")
    delivery = order.get("delivery_coords")
    ts_str = order.get("picked_up_at")
    if not pickup or not delivery or not ts_str:
        return None
    ts = _parse_checkpoint_ts(ts_str)
    if ts is None:
        return None
    elapsed_min = (now_utc - ts).total_seconds() / 60.0
    if elapsed_min < 0:
        return None
    try:
        leg = osrm_client.route(tuple(pickup), tuple(delivery), use_cache=True)
        eta_min = float(leg.get("duration_min") or 0.0)
    except Exception:
        return None
    if eta_min <= 0:
        return None
    f = max(0.0, min(1.0, elapsed_min / eta_min))
    lat = float(pickup[0]) + f * (float(delivery[0]) - float(pickup[0]))
    lon = float(pickup[1]) + f * (float(delivery[1]) - float(pickup[1]))
    return ((lat, lon), "last_picked_up_interp")


def _reconstruct_bag_from_panel_packs(
    cs: "CourierState",
    candidate_oids: List,
    state: Dict[str, Dict],
    now_utc: datetime,
) -> None:
    """Faza 4 (D5, 2026-05-18): odbuduj cs.bag z panel_packs ground-truth.

    Gdy panel_packs widzi kuriera z bagiem, a build_fleet_snapshot zbudował
    cs.bag pusty (grupowanie po courier_id gubi zlecenia z cid=None — lag
    V3.15 reconcile). Zlecenie JEST w orders_state z pełnymi danymi (coords,
    status) — tylko nie podlinkowane do kuriera. Lookup po order_id w już
    wczytanym `state` → zero I/O.

    Mutuje cs in-place: ustawia cs.bag, cs.bag_from_panel_packs i re-resolve
    cs.pos/cs.pos_source (kurier był no_gps). No-op gdy żadnego oid nie da się
    zrekonstruować (brak w state / status terminalny / stale) — zostaje
    panel_packs_oids_signal + kara score jako fallback."""
    rebuilt: List[Dict] = []
    for oid in candidate_oids:
        oid_s = str(oid)
        o = state.get(oid_s)
        if not isinstance(o, dict):
            continue  # brak rekordu — bez coords nie da się odbudować
        if o.get("status") not in ("assigned", "picked_up"):
            continue  # terminalny / planned — nie należy do aktywnego bagu
        if not _bag_not_stale(o, now_utc):
            continue  # TTL — prawdopodobnie już delivered, reconcile lag
        # Entry spójne wewnętrznie: courier_id = ten kurier (state ma None/stary).
        rebuilt.append(dict(o, order_id=oid_s, courier_id=cs.courier_id))
    if not rebuilt:
        return

    cs.bag = rebuilt
    cs.bag_from_panel_packs = True
    # Re-resolve pozycję z odbudowanego bagu (cs miał pos_source=no_gps).
    # Mirror kroku 2 build_fleet_snapshot: picked_up>assigned, najnowszy wygrywa.
    for order in sorted(rebuilt, key=_bag_sort_key, reverse=True):
        st = order.get("status")
        if st == "picked_up":
            # F4 Krok 2 (Opcja C): interpolacja pickup→delivery po elapsed/eta_leg.
            # Ma pierwszeństwo nad pickup_proxy (Krok 1) gdy obie flagi ON;
            # fail-soft (None) → caller pada na pickup_proxy → legacy delivery.
            if _f4_flag("ENABLE_F4_COURIER_POS_INTERP"):
                _interp = _compute_interp_pos(order, now_utc)
                if _interp is not None:
                    cs.pos, cs.pos_source = _interp
                    break
            # F4 Krok 1: pickup_coords (punkt realny) > delivery_coords (proxy).
            if _f4_flag("ENABLE_F4_COURIER_POS_PICKUP_PROXY") and order.get("pickup_coords"):
                cs.pos = tuple(order["pickup_coords"])
                cs.pos_source = "last_picked_up_pickup"
                break
            if order.get("delivery_coords"):
                cs.pos = tuple(order["delivery_coords"])
                cs.pos_source = "last_picked_up_delivery"
                break
        if st == "assigned" and order.get("pickup_coords"):
            cs.pos = tuple(order["pickup_coords"])
            cs.pos_source = "last_assigned_pickup"
            break
    _log.info(
        f"Faza4 panel_packs bag reconstructed cid={cs.courier_id} "
        f"nick={cs.name!r} oids={[o['order_id'] for o in rebuilt]} "
        f"pos_source={cs.pos_source}"
    )


def build_fleet_snapshot(
    include_koordynator: bool = False,
) -> Dict[str, CourierState]:
    """Buduje snapshot wszystkich kurierow z ich aktualna pozycja i bagiem.

    Returns:
        dict courier_id -> CourierState
    """
    state = state_machine.get_all()
    piny = _load_kurier_piny()
    names = _load_courier_names()
    gps = _load_gps_positions()
    now_utc = datetime.now(timezone.utc)

    # FIX 2026-06-08: persistent last-known-pos store (flag-gated, fail-soft).
    # OFF → pusty dict → zero zmiany zachowania (step 4 idzie wprost do no_gps).
    _lp_on = flag("ENABLE_COURIER_LAST_KNOWN_POS", default=False)
    _last_pos_store = _load_last_known_pos() if _lp_on else {}

    # GPS-02 (audyt 2026-06-10): filtr jakości fixu (accuracy + teleport).
    # `_gpsq_compute` = czy w ogóle liczyć/logować shadow (compute-zawsze, default
    # ON — czysta telemetria). `_gpsq_active` = czy werdykt "reject" ma WPŁYWAĆ na
    # flotę (default OFF — flip dopiero po kalibracji + ACK). Kotwicę teleportu
    # bierzemy z last-known-pos store (poprzedni fix GPS); gdy store nie jest
    # załadowany (last-known-pos OFF) — ładujemy go read-only TYLKO dla kotwicy.
    _gpsq_compute = flag("ENABLE_GPS_QUALITY_SHADOW", default=True)
    _gpsq_active = flag("ENABLE_GPS_ACCURACY_TELEPORT_FILTER", default=False)
    _gpsq_store = _last_pos_store if _last_pos_store else (
        _load_last_known_pos() if _gpsq_compute else {})

    # Grupuj ordery per kurier
    per_courier: Dict[str, List[Dict]] = {}
    for oid, o in state.items():
        kid = o.get("courier_id")
        if not kid:
            continue
        if str(kid) == "26" and not include_koordynator:
            continue
        o = dict(o, order_id=oid)
        per_courier.setdefault(str(kid), []).append(o)

    fleet: Dict[str, CourierState] = {}

    # Source of courier_id: orders_state + courier_names.json (właściwy cid z panelu).
    # Pin space (kurier_piny.json) NIE jest źródłem cid — PIN to 4-cyfrowy kod
    # logowania w Courier App, NIE courier_id z panel API. Wcześniej dodawanie
    # piny.keys() tworzyło phantom kurierów (np. Michał Ro cid=5333-PIN obok
    # cid=518-real) z pustym bagiem → fałszywa propozycja "wolnego" kuriera
    # (bug 2026-04-19 14:00-14:08, propozycje #467070-#467077).
    # PIN pozostaje name-lookup fallback (L227-231).
    try:
        from dispatch_v2.common import STRICT_COURIER_ID_SPACE as _strict_cid
    except Exception:
        _strict_cid = True
    _pin_strs = {str(k) for k in piny.keys()}
    if _strict_cid:
        raw_kids = set(per_courier.keys()) | set(names.keys())
        # V3.25 hotfix: aktywnie wyklucz PIN-y z cid space (np. 9279 zaleakowany
        # do courier_names.json 14.04 manualną edycją). V3.13 STRICT zablokował
        # tylko piny.keys() w all_kids — names.keys() przepuszczał phantom dalej.
        _phantom = _pin_strs & raw_kids
        if _phantom:
            _log.warning(
                f"PIN leaked into courier_id space: {_phantom} — FILTERED OUT "
                f"(check orders_state.json and courier_names.json)"
            )
        all_kids = raw_kids - _pin_strs
    else:
        all_kids = set(per_courier.keys()) | set(names.keys()) | _pin_strs

    for kid in all_kids:
        orders = per_courier.get(kid, [])
        # TTL filter (V3.14): wyklucza orderly assigned >BAG_STALE_THRESHOLD_MIN
        # bez picked_up — panel_watcher reconcile lag do 90 min, pipeline nie
        # może ufać bezwzględnie orders_state.status=assigned dla starych entries.
        active_bag = [
            o for o in orders
            if o.get("status") in ("assigned", "picked_up")
            and _bag_not_stale(o, now_utc)
        ]

        cs = CourierState(courier_id=kid)
        cs.bag = active_bag
        # V3.19h BUG-4: attach tier info z courier_tiers.json (consumed by feasibility_v2).
        _tiers = _load_courier_tiers()
        _tinfo = _tiers.get(kid) if isinstance(_tiers, dict) else None
        if isinstance(_tinfo, dict):
            # TIER-01 (audyt 2026-06-03, conf=high): flaga `inactive` (ex-kurier,
            # np. cid=61/426 od 04-23) była czytana TYLKO w telegram_approver (UI),
            # NIGDY w dispatchu → ex-kurier ręcznie wpisany do grafiku dostałby gold
            # priorytet. Defense-in-depth OBOK grafiku/manual_overrides: inactive ⇒
            # NIE wchodzi do floty (jak nie-na-zmianie). Warn raz/kid = sygnał stale
            # roster (Z2 never-silent), nie cisza. Kill-switch flags.json hot-reload.
            if _tinfo.get("inactive") and flag("ENABLE_INACTIVE_COURIER_GUARD", default=True):
                _seen_inact = getattr(build_fleet_snapshot, "_warned_inactive", set())
                if kid not in _seen_inact and len(_seen_inact) < 50:
                    _log.warning(
                        f"INACTIVE_COURIER_GUARD kid={kid} "
                        f"({_tinfo.get('inactive_reason') or 'ex-courier'}) w rosterze/grafiku "
                        f"→ wykluczony z floty (sprawdź grafik/manual_overrides)")
                    _seen_inact.add(kid)
                    build_fleet_snapshot._warned_inactive = _seen_inact
                continue
            _bag_info = _tinfo.get("bag") or {}
            cs.tier_bag = _bag_info.get("tier")
            cs.tier_cap_override = _bag_info.get("cap_override")
            cs.tier_label = _tinfo.get("tier_label")  # V3.25: 'new' dla R-04
        # Name lookup: courier_names.json (primary, correct ID space) → kurier_piny (legacy fallback)
        name = names.get(kid)
        if name is None and kid.isdigit():
            name = names.get(str(int(kid)))  # normalize leading zeros etc.
        if name is None:
            pin_name = piny.get(kid)
            if pin_name is None and kid.isdigit():
                pin_name = piny.get(int(kid))
            if isinstance(pin_name, str):
                name = pin_name
        if isinstance(name, str):
            cs.name = name

        # 1. GPS fresh
        gps_entry = gps.get(kid)
        if gps_entry:
            gps_ts = gps_entry.get("timestamp")
            try:
                gps_dt = datetime.fromisoformat(gps_ts.replace("Z", "+00:00")) if gps_ts else None
            except Exception as _e:
                gps_dt = None
                # A1: GPS ts parse fail dawniej silent → kurier traktowany no_gps
                # bez sygnału. Schema GPS sensor zmiana = mass false-no_gps.
                seen = getattr(build_fleet_snapshot, "_warned_gps_ts", set())
                key = (type(_e).__name__, str(gps_ts)[:40])
                if key not in seen and len(seen) < 50:
                    _log.warning(f"gps timestamp parse fail kid={kid} ({type(_e).__name__}: {_e}) input={gps_ts!r}")
                    seen.add(key)
                    build_fleet_snapshot._warned_gps_ts = seen
            if gps_dt:
                age_min = (now_utc - gps_dt).total_seconds() / 60.0
                if age_min < GPS_FRESHNESS_MIN:
                    try:
                        _glat = float(gps_entry["lat"])
                        _glon = float(gps_entry["lon"])
                    except (TypeError, ValueError, KeyError):
                        _glat = _glon = None
                    # FAIL-05 (audyt 2026-06-03): sanity-bbox świeżego GPS PRZED zaufaniem
                    # mu jako pos_source="gps" (najwyższy priorytet pozycji). Skażony fix
                    # ((0,0), NaN, spike poza region) wchodził z najwyższym zaufaniem →
                    # zatruwał fleet_avg/scoring/OSRM (Lekcja #140: OSRM snapuje (0,0) na
                    # krawędź ekstraktu i zwraca code:Ok z ~117 min legiem → fałszywa
                    # geometria). Poza bboxem → NIE ufaj GPS; fall-through do bag/recent/
                    # no_gps (krok 2-4), NIGDY sentinel (0,0). Bbox HOJNY (±55km,
                    # coords_in_bialystok_bbox) → odrzuca śmieci, NIE krawędzie miasta
                    # (Wasilków/Supraśl/Łapy w środku). Parse-guard (try) zawsze ON —
                    # ortogonalna ochrona przed crashem na złym lat/lon. Kill-switch bbox:
                    # ENABLE_GPS_BBOX_GUARD=false (flags.json hot-reload).
                    _bbox_on = flag("ENABLE_GPS_BBOX_GUARD", default=True)
                    _gps_ok = (_glat is not None) and (
                        not _bbox_on or coords_in_bialystok_bbox((_glat, _glon)))
                    if _gps_ok:
                        # GPS-02: filtr jakości (accuracy + teleport) — compute-zawsze,
                        # efekt za flagą. Werdykt liczony PO bbox (mamy realny fix w
                        # regionie) i logowany do shadow. Kotwica teleportu = poprzedni
                        # fix GPS ze store. Reject TYLKO gdy filtr aktywny → fall-through
                        # (jak GPS_BBOX_REJECT): last-known-pos store/no_gps przejmuje.
                        _gpsq_reject = False
                        if _gpsq_compute:
                            try:
                                _anchor_pos, _anchor_dt, _anchor_age = _gps_quality_anchor(
                                    _gpsq_store.get(kid), age_min, now_utc)
                                _gpsq_verdict = gps_quality.assess_gps_quality(
                                    (_glat, _glon),
                                    gps_entry.get("accuracy"),
                                    anchor_pos=_anchor_pos,
                                    dt_seconds=_anchor_dt,
                                    anchor_age_min=_anchor_age,
                                )
                                _log_gps_quality_shadow(
                                    kid, _gpsq_verdict, now_iso(), _gpsq_active,
                                    (_glat, _glon), age_min)
                                if _gpsq_active and not _gpsq_verdict.accept:
                                    _gpsq_reject = True
                            except Exception as _qe:
                                # fail-soft: błąd filtra NIGDY nie blokuje floty
                                _log.warning(f"gps_quality assess fail kid={kid}: {_qe}")
                        if not _gpsq_reject:
                            cs.pos = (_glat, _glon)
                            cs.pos_source = "gps"
                            cs.pos_age_min = age_min
                            fleet[kid] = cs
                            continue
                        # filtr aktywny + werdykt reject → log raz/kid, fall-through
                        _seenq = getattr(build_fleet_snapshot, "_warned_gps_quality", set())
                        if kid not in _seenq and len(_seenq) < 50:
                            _log.warning(
                                f"GPS_QUALITY_REJECT kid={kid} fix=({_glat},{_glon}) "
                                f"age={age_min:.1f}min reasons={_gpsq_verdict.reasons} "
                                f"→ fall-through (filtr aktywny)")
                            _seenq.add(kid)
                            build_fleet_snapshot._warned_gps_quality = _seenq
                        # NIE 'continue' — pozwól na fall-through do bag/recent/store/no_gps
                    # świeży GPS odrzucony (poza bbox / nieparsowalny) → log raz/kid,
                    # fall-through do bag/recent/no_gps (NIGDY (0,0)).
                    _seen = getattr(build_fleet_snapshot, "_warned_gps_bbox", set())
                    if kid not in _seen and len(_seen) < 50:
                        _log.warning(
                            f"GPS_BBOX_REJECT kid={kid} fix=({_glat},{_glon}) "
                            f"age={age_min:.1f}min → fall-through (nie ufam GPS jako pos)")
                        _seen.add(kid)
                        build_fleet_snapshot._warned_gps_bbox = _seen

        # 2. AKTYWNY BAG priorytet (picked_up > assigned, najnowszy wygrywa)
        #    picked_up -> F4 Krok 1: pickup_coords (kurier BYL przy restauracji
        #      o picked_up_at — punkt realny); fail-soft delivery_coords gdy brak.
        #      Flaga OFF -> delivery_coords (zachowanie sprzed F4).
        #    assigned -> pickup_coords (kurier jedzie odebrac)
        #    Iteracja malejaco: jesli najnowszy broken -> probuj kolejny
        # FAIL-02 fix (audyt 2026-06-03): pozycja MUSI używać tego samego filtra
        # stale co bag (linie 537-541). Bez tego porzucony kurier (picked_up
        # >BAG_STALE_THRESHOLD_MIN, brak świeżego GPS) miał cs.bag=[] (widziany jako
        # WOLNY) ale cs.pos = ZAMROŻONE coords porzuconego zlecenia → fałszywa
        # bliskość → wysoki score → dostawał NOWE zlecenia (kurier-widmo).
        # Po filtrze: brak aktywnego ordera → fall-through do no_gps fallback (krok 4,
        # BIALYSTOK_CENTER) → _demote_blind_empty degraduje go pod aktywnych kurierów.
        active_bag_orders = [
            o for o in orders
            if o.get("status") in ("picked_up", "assigned")
            and _bag_not_stale(o, now_utc)
        ]
        if active_bag_orders:
            sorted_bag = sorted(active_bag_orders, key=_bag_sort_key, reverse=True)
            resolved = False
            for order in sorted_bag:
                if order.get("status") == "picked_up":
                    # F4 Krok 2 (Opcja C): interpolacja pickup→delivery
                    # po elapsed/eta_leg. Pierwszeństwo nad pickup_proxy;
                    # fail-soft (None) → ścieżka Krok 1 / legacy poniżej.
                    if _f4_flag("ENABLE_F4_COURIER_POS_INTERP"):
                        _interp = _compute_interp_pos(order, now_utc)
                        if _interp is not None:
                            cs.pos, cs.pos_source = _interp
                            resolved = True
                            break
                    # F4 Krok 1 (Opcja A): proxy = pickup_coords zamiast
                    # delivery_coords — eliminuje bias frozen-window (474266).
                    if _f4_flag("ENABLE_F4_COURIER_POS_PICKUP_PROXY") and order.get("pickup_coords"):
                        cs.pos = tuple(order["pickup_coords"])
                        cs.pos_source = "last_picked_up_pickup"
                        resolved = True
                        break
                    if order.get("delivery_coords"):
                        cs.pos = tuple(order["delivery_coords"])
                        cs.pos_source = "last_picked_up_delivery"
                        resolved = True
                        break
                    _log.warning(
                        f"courier {kid} picked_up order {order.get('order_id')} "
                        f"bez delivery_coords - data quality alert (P0.4)"
                    )
                else:  # assigned
                    if order.get("pickup_coords"):
                        cs.pos = tuple(order["pickup_coords"])
                        cs.pos_source = "last_assigned_pickup"
                        resolved = True
                        break
                    _log.warning(
                        f"courier {kid} assigned order {order.get('order_id')} "
                        f"bez pickup_coords - data quality alert (P0.4)"
                    )
            if resolved:
                fleet[kid] = cs
                continue

        # 3. Recent activity (delivered_at lub picked_up_at < 30 min temu).
        #    Wymagamy ŚWIEŻEGO eventu — stary delivered (np. sprzed 6 dni jak
        #    Bartek bez aktualnego GPS) NIE jest dobrym estymatem pozycji.
        RECENT_MAX_MIN = 30
        best_age = float("inf")
        best_pos = None
        best_source = None
        for o in orders:
            delivery_c = o.get("delivery_coords")
            if not delivery_c:
                continue
            for ts_key, source_label in [
                ("delivered_at", "last_delivered"),
                ("picked_up_at", "last_picked_up_recent"),
            ]:
                ts_str = o.get(ts_key)
                if not ts_str:
                    continue
                ts = _parse_checkpoint_ts(ts_str)
                if ts is None:
                    # A1: order ts parse fail dawniej silent → bag aggregation
                    # może gubić ordery (Lekcja #32 + #80 tracone pole pattern).
                    seen = getattr(build_fleet_snapshot, "_warned_order_ts", set())
                    key = (ts_key, str(ts_str)[:40])
                    if key not in seen and len(seen) < 50:
                        _log.warning(f"order {ts_key} parse fail input={ts_str!r}")
                        seen.add(key)
                        build_fleet_snapshot._warned_order_ts = seen
                    continue
                age = (now_utc - ts).total_seconds() / 60.0
                if age < 0 or age >= RECENT_MAX_MIN:
                    continue
                if age < best_age:
                    best_age = age
                    best_pos = tuple(delivery_c)
                    best_source = source_label

        if best_pos is not None:
            cs.pos = best_pos
            cs.pos_source = best_source
            cs.pos_age_min = best_age
            fleet[kid] = cs
            continue

        # 4a. FIX 2026-06-08: ZANIM fikcja centrum miasta — persistent store.
        #     Jeśli widzieliśmy tego kuriera na ŻYWEJ pozycji ≤TTL min temu
        #     (zanim order zniknął ze stanu), odtwórz TĘ pozycję+źródło. To jest
        #     dokładnie "Ziomek obejdzie się bez GPS, bo wie z planu/historii
        #     gdzie kurier był". Kurier ciemny >TTL → fall-through do no_gps.
        if _lp_on:
            _rescued = _rescue_from_last_pos(_last_pos_store.get(kid), now_utc)
            if _rescued is not None:
                cs.pos, cs.pos_source, cs.pos_age_min = _rescued
                cs.pos_from_store = True
                fleet[kid] = cs
                _log.info(
                    f"LAST_KNOWN_POS_USED kid={kid} src={cs.pos_source} "
                    f"age={cs.pos_age_min:.1f}min → uratowany z BIALYSTOK_CENTER")
                continue

        # 4. no_gps fallback: kurier wolny, brak GPS i brak historii.
        #    Dajemy syntetyczną pozycję = centrum miasta. Dispatch_pipeline
        #    nadpisze km_to_pickup średnią floty i travel_min = max(prep, 15).
        cs.pos = BIALYSTOK_CENTER
        cs.pos_source = "no_gps"
        fleet[kid] = cs

    # Dedup: gdy 2+ courier_id mają to samo imię (np. legacy 4657 + panel 123
    # dla "Bartek O."), zostaje wpis z lepszym pos_source.
    by_name: Dict[str, str] = {}
    for kid in list(fleet.keys()):
        cs = fleet[kid]
        if not cs.name:
            continue
        existing_kid = by_name.get(cs.name)
        if existing_kid is None:
            by_name[cs.name] = kid
            continue
        existing = fleet[existing_kid]
        cur_p = POS_SOURCE_PRIORITY.get(cs.pos_source, 99)
        ex_p = POS_SOURCE_PRIORITY.get(existing.pos_source, 99)
        if cur_p < ex_p:
            del fleet[existing_kid]
            by_name[cs.name] = kid
        else:
            del fleet[kid]

    # V3.28 P4 — coordinator role + activation enrich (Adrian doktryna 2026-05-10).
    # is_coordinator = czy ma rolę hybrydową (z courier_tiers.json `coordinator` flag).
    # coordinator_active = czy dziś jeździ aktywnie (z coordinator_activations.json).
    # Pipeline scoring: jeśli is_coordinator AND NOT coordinator_active → strong demote.
    try:
        from dispatch_v2 import coordinator_activations as _coord_act
        _active_coord_cids = _coord_act.get_all_active()
    except Exception as _e:
        _log.warning(f"coordinator_activations load fail: {_e}")
        _active_coord_cids = set()
    _tiers = _load_courier_tiers()
    if isinstance(_tiers, dict):
        for kid, cs in fleet.items():
            _tinfo = _tiers.get(kid)
            if isinstance(_tinfo, dict) and _tinfo.get("coordinator") is True:
                cs.is_coordinator = True
                cs.coordinator_active = (kid in _active_coord_cids)

    # V3.28 P3 (B) — enrich panel_packs signal (Adrian doktryna 2026-05-10).
    # Read panel_packs_cache.json (per panel_watcher tick) i dla każdego cs:
    # jeśli cache fresh AND state.bag pusty AND panel widzi nick→[oids] → set
    # panel_packs_oids_signal jako "kurier faktycznie wozi". Score penalty
    # w dispatch_pipeline gdy state-vs-panel divergence (cid=None lag).
    _pks_ts, _pks_packs, _pks_age = _load_panel_packs_cache()
    if _pks_age is not None and _pks_age <= PANEL_PACKS_CACHE_MAX_AGE_S:
        # Pre-build normalized lookup: panel używa "Bartek O," (z przecinkiem) podczas
        # gdy kurier_ids ma "Bartek O" — `rstrip(".,;:")` jak telegram_approver._norm.
        # Lekcja #78: defense-in-depth na nick normalization across systems.
        _packs_normalized: Dict[str, list] = {}
        for _pn, _po in _pks_packs.items():
            if not isinstance(_pn, str):
                continue
            _key = _pn.strip().rstrip(".,;:").lower()
            if _key:
                _packs_normalized[_key] = _po
        # #2 (2026-06-10): po PANEL-CANON (#1) cs.name to PEŁNE imię grafiku
        # ("Michał Karpiuk"), a panel_packs nick to SKRÓT panelu ("Michał K.") →
        # samo cs.name już NIE pasuje, co rozbroiło V3.15/D5 anty-lag dla pełno-
        # imiennych kurierów (bag=0 w state-lag nie dostawał rekonstrukcji). Fix:
        # rozwiąż nick panelu → cid przez SUROWE aliasy (kurier_ids/courier_names
        # trzymają skróty panelu jako klucze), EXACT-normalized (nie fuzzy → zero
        # mis-resolve) i matchuj po cid. Union z dawnym cs.name → strictly >= dawne
        # pokrycie (zero regresji). Flag-gated kill-switch.
        _packs_by_cid: Dict[str, list] = {}
        if flag("ENABLE_PANEL_PACKS_CID_MATCH", True):
            _nick2cid: Dict[str, str] = {}
            for _ap in (KURIER_IDS_PATH, COURIER_NAMES_PATH):
                try:
                    with open(_ap) as _af:
                        _adata = json.load(_af)
                except Exception:
                    continue
                # kurier_ids: {imię: cid}; courier_names: {cid: imię}
                _pairs = (_adata.items() if _ap == KURIER_IDS_PATH
                          else ((_v, _k) for _k, _v in _adata.items()))
                for _nm, _c in _pairs:
                    if isinstance(_nm, str):
                        _nick2cid.setdefault(
                            _nm.strip().rstrip(".,;:").lower(), str(_c))
            for _pn, _po in _pks_packs.items():
                if not isinstance(_pn, str):
                    continue
                _c = _nick2cid.get(_pn.strip().rstrip(".,;:").lower())
                if _c:
                    _packs_by_cid[_c] = _po
        for kid, cs in fleet.items():
            cs.panel_packs_cache_age_s = round(_pks_age, 1)
            if len(cs.bag) > 0:
                continue
            # match po cid (alias-resolved, robust na pełne cs.name) LUB po cs.name
            # (dawne — backstop gdy cid nierozwiązany z aliasów). Union.
            _candidates = _packs_by_cid.get(str(kid))
            if not _candidates and cs.name:
                _candidates = _packs_normalized.get(
                    cs.name.strip().rstrip(".,;:").lower())
            _candidates = _candidates or []
            if _candidates:
                cs.panel_packs_oids_signal = [str(x) for x in _candidates]
                # Faza 4 (D5): odbuduj cs.bag z panel_packs ground-truth. Po
                # udanej rekonstrukcji cs.bag niepusty → bag_size/pos/trasa
                # poprawne, a kara bonus_state_panel_mismatch sama się wyłącza
                # (warunek _state_bag_size==0 przestaje być prawdą).
                if flag("ENABLE_PANEL_PACKS_BAG_RECONSTRUCTION", True):
                    _reconstruct_bag_from_panel_packs(
                        cs, _candidates, state, now_utc)
    else:
        # Cache stale lub missing — wszystkie kuriery dostają age info dla C gate
        for cs in fleet.values():
            cs.panel_packs_cache_age_s = _pks_age

    # FIX 2026-06-08: zapisz świeże ŻYWE pozycje do persistent store, by następne
    # wywołanie mogło uratować kuriera w luce GPS (step 4a). Pozycje odtworzone
    # ZE store (pos_from_store, pusty bag) NIE są odświeżane → starzeją się i
    # wygasają (laundering / immortal-phantom guard). Gdy panel_packs odbudował
    # worek (len(bag)>0), pozycja jest znów ŻYWA → odświeżamy ts.
    if _lp_on:
        try:
            _now_iso = now_iso()
            _updates: Dict[str, dict] = {}
            for kid, cs in fleet.items():
                if not cs.pos or cs.pos_source not in _LAST_POS_GOOD_SOURCES:
                    continue
                if cs.pos_from_store and len(cs.bag) == 0:
                    continue  # replay ze store — niech wygaśnie, nie odświeżaj
                try:
                    _lat = float(cs.pos[0])
                    _lon = float(cs.pos[1])
                except (TypeError, ValueError, IndexError):
                    continue
                _updates[kid] = {
                    "lat": _lat, "lon": _lon,
                    "ts": _now_iso, "source": cs.pos_source,
                }
            if _updates:
                _save_last_known_pos(_updates)
        except Exception as _e:
            _log.warning(f"last_known_pos save block fail: {_e}")

    return fleet


def _mins_to_shift_start(entry: Optional[dict]) -> Optional[float]:
    """Z entry grafiku → minuty od now (Warsaw) do startu zmiany.
    Dodatnie = jeszcze nie zaczął, ujemne = już po. None = brak danych."""
    start_str = (entry or {}).get("start")
    if not start_str or ":" not in start_str:
        return None
    try:
        from zoneinfo import ZoneInfo
        WAW = ZoneInfo("Europe/Warsaw")
        now_w = datetime.now(WAW)
        h, m = start_str.split(":")
        start_dt = now_w.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
        return (start_dt - now_w).total_seconds() / 60.0
    except Exception:
        return None


def _shift_start_dt(entry: Optional[dict]) -> Optional[datetime]:
    """V3.25: z entry grafiku → datetime startu zmiany (Warsaw aware).
    Mirror _shift_end_dt — używane w dispatchable_fleet do set cs.shift_start
    dla V3.25 R-01 SCHEDULE-HARDENING PRE-CHECK."""
    start_str = (entry or {}).get("start")
    if not start_str or ":" not in start_str:
        return None
    try:
        from zoneinfo import ZoneInfo
        WAW = ZoneInfo("Europe/Warsaw")
        now_w = datetime.now(WAW)
        h, m = start_str.split(":")
        return now_w.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
    except Exception:
        return None


def _shift_end_dt(entry: Optional[dict]) -> Optional[datetime]:
    """Z entry grafiku → datetime końca zmiany (Warsaw aware)."""
    end_str = (entry or {}).get("end")
    if not end_str or ":" not in end_str:
        return None
    try:
        from zoneinfo import ZoneInfo
        WAW = ZoneInfo("Europe/Warsaw")
        now_w = datetime.now(WAW)
        if end_str == "24:00":
            base = now_w.replace(hour=0, minute=0, second=0, microsecond=0)
            return base + timedelta(days=1)
        h, m = end_str.split(":")
        end_dt = now_w.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
        # Jeśli zmiana skończyła się "wczoraj" (np. now=01:00, end=23:00), nadal
        # interpretujemy jako today (przeszłość — feasibility wykluczy)
        return end_dt
    except Exception:
        return None


def _parse_added_at(entry: Optional[dict]) -> Optional[datetime]:
    """Working-override entry → aware datetime kiedy override dodano (UTC).
    None gdy brak pola / parse fail. Używane przez GRAFIK-CAP do rozróżnienia
    'pracuje' wpisanego W TRAKCIE grafiku (added_at <= grafik_end → przytnij)
    od 'pracuje' wpisanego PO grafiku (realna druga zmiana → nie przycinaj)."""
    s = (entry or {}).get("added_at")
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _effective_working_override_shift_end(
    wo_entry: Optional[dict],
    grafik_entry: Optional[dict],
    cap_enabled: bool,
) -> Optional[datetime]:
    """Efektywny shift_end dla working-override w gałęzi FALLBACK (kurier NIE jest na
    realnej zmianie teraz). GRAFIK-CAP (Adrian 2026-06-07): domyślny koniec override'a
    (24:00) wpisany w trakcie/przed realną zmianą NIE może wskrzeszać kuriera po realnym
    końcu grafiku → przycinamy do min(override_end, grafik_end). Pure function (testowalna).

    Zwraca Warsaw-aware datetime końca, lub None (None = brak ograniczenia, jak legacy).
    Przycięcie POMIJANE gdy którekolwiek:
      - cap_enabled == False (flaga ENABLE_WORKING_OVERRIDE_GRAFIK_CAP off),
      - wo_entry["end_explicit"] == True (operator podał jawny 'do HH:MM'),
      - brak grafik_entry (kurier spoza grafiku dziś — 24:00 słuszne),
      - grafik_end lub added_at nie do sparsowania,
      - added_at > grafik_end (override dodany PO zmianie = realna druga/wieczorna zmiana).
    """
    wo_end = _shift_end_dt(wo_entry)
    if not cap_enabled:
        return wo_end
    if bool((wo_entry or {}).get("end_explicit", False)):
        return wo_end
    if not grafik_entry:
        return wo_end
    grafik_end = _shift_end_dt(grafik_entry)
    added_at = _parse_added_at(wo_entry)
    if grafik_end is None or added_at is None:
        return wo_end
    if added_at <= grafik_end:
        return min(wo_end, grafik_end) if wo_end is not None else grafik_end
    return wo_end


def _post_shift_start_synthetic_eligible(
    cs: CourierState,
    now_utc: datetime,
    schedule: dict,
    match_courier_fn,
    shift_start_dt_fn,
    is_on_shift_fn,
    min_minutes: float = 5.0,
) -> bool:
    """Adrian decyzja 2026-05-06 (Faza 7-AUTO-PROXIMITY): kurier 5+ min po
    shift_start z brakiem GPS → synthetic position assumption ("już jest pod
    restauracją"). Pure check — caller mutuje cs.pos/pos_source jeśli True.

    Warunki:
    - cs.pos is None (brak GPS / fallback)
    - cs.name w grafiku, on_shift=True
    - now_utc >= shift_start + min_minutes
    """
    if cs.pos is not None:
        return False
    if not (schedule and cs.name and match_courier_fn and is_on_shift_fn):
        return False
    full_name = match_courier_fn(cs.name, schedule)
    if not full_name:
        return False
    entry = schedule.get(full_name)
    if not entry:
        return False
    on_shift, _reason = is_on_shift_fn(cs.name, schedule)
    if not on_shift:
        return False
    shift_start = shift_start_dt_fn(entry)
    if shift_start is None:
        return False
    if shift_start.tzinfo is None:
        shift_start = shift_start.replace(tzinfo=timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    mins_since = (now_utc - shift_start).total_seconds() / 60.0
    return mins_since >= min_minutes


def dispatchable_fleet(fleet: Optional[Dict[str, CourierState]] = None) -> List[CourierState]:
    """Zwraca tylko kurierow ktorych mozna scorowac (maja pozycje i sa na zmianie
    LUB zaczynają zmianę w ciągu PRE_SHIFT_WINDOW_MIN minut).

    TASK 3 (2026-05-04): collects fleet filter decisions dla observability layer.
    Flag-gated: OBSERVABILITY_FLEET_FILTER_LOGGING (default false, zero overhead).

    Faza 7-AUTO-PROXIMITY (2026-05-06): post-shift-start synthetic position
    enrichment — kurier 5+ min po shift_start z pos=None traktowany jako
    "pod restauracją" (BIALYSTOK_CENTER fallback). Flag-gated:
    ENABLE_AUTO_PROXIMITY_POST_SHIFT_5MIN (default False).
    """
    import sys as _sys
    _sys.path.insert(0, "/root/.openclaw/workspace/scripts")
    try:
        from schedule_utils import (load_schedule, is_on_shift, match_courier,
                                     is_schedule_stale)
        schedule = load_schedule()
        # D2 (audyt 2026-05-28): wykryj stale grafik RAZ (ten sam 30min próg co
        # shift_notifications.worker). Stempel per-courier niżej → feasibility
        # soft-degraduje zamiast hard-reject NO_ACTIVE_SHIFT przy awarii pliku.
        _sched_stale = bool(is_schedule_stale())
    except Exception as _e:
        _log.warning(f"schedule load failed: {_e} — skip filtrowania")
        schedule = {}
        match_courier = None
        is_on_shift = None
        _sched_stale = True  # load failed → traktuj jako stale (D2 soft-degrade)
    try:
        from dispatch_v2 import manual_overrides
        excluded = set(manual_overrides.get_excluded())
        working = manual_overrides.get_working()  # {cid_str: {"start","end",...}}
        # Opcja A (2026-06-10): egzekucja wykluczenia PO CID, nie tylko po nazwie.
        # Naprawia desync — flota od 06-10 nadaje cs.name pełne imię z grafiku
        # ('Mateusz Ostapczuk'), a override trzyma skrót panelowy ('Mateusz O') →
        # czysty match nazw gubił blokadę. get_excluded_cids() mapuje dowolną formę
        # nazwy → cid. Flag-gated (hot-reload kill-switch), fail-soft.
        excluded_cids = manual_overrides.get_excluded_cids()
    except Exception as _e:
        _log.warning(f"manual_overrides load failed: {_e}")
        excluded = set()
        working = {}
        excluded_cids = set()
    if fleet is None:
        fleet = build_fleet_snapshot()
    # Faza 7 flag — read once per dispatchable_fleet() call (no per-courier cost).
    try:
        from dispatch_v2 import common as _C7
        _post_shift_5min_enabled = bool(getattr(_C7, "ENABLE_AUTO_PROXIMITY_POST_SHIFT_5MIN", False))
        # Hot-reload kill-switch: flags.json wygrywa (instant disable bez restartu);
        # gdy klucza brak → env-latched default z common.py (ENABLE_WORKING_OVERRIDE).
        _working_override_enabled = bool(_C7.flag(
            "ENABLE_WORKING_OVERRIDE",
            default=bool(getattr(_C7, "ENABLE_WORKING_OVERRIDE", True))))
        # GRAFIK-CAP (2026-06-07): hot-reload kill-switch; flags.json wygrywa, fallback env-latched.
        _wo_grafik_cap_enabled = bool(_C7.flag(
            "ENABLE_WORKING_OVERRIDE_GRAFIK_CAP",
            default=bool(getattr(_C7, "ENABLE_WORKING_OVERRIDE_GRAFIK_CAP", True))))
        # EXCLUDE-BY-CID (2026-06-10): hot-reload kill-switch; default ON. OFF →
        # zachowanie sprzed fixu (match tylko po nazwie cs.name).
        _exclude_by_cid_enabled = bool(_C7.flag(
            "ENABLE_EXCLUDE_BY_CID",
            default=bool(getattr(_C7, "ENABLE_EXCLUDE_BY_CID", True))))
    except Exception:
        _post_shift_5min_enabled = False
        _working_override_enabled = True
        _wo_grafik_cap_enabled = True
        _exclude_by_cid_enabled = True
    _now_utc_fleet = datetime.now(timezone.utc)
    result = []
    # TASK 3: collect rejected dla observability logger (zero overhead gdy flag false)
    _rejected_for_log = []
    _passed_for_log = []
    for cs in fleet.values():
        # Faza 7-AUTO-PROXIMITY: post-shift-start synthetic pos (Adrian decyzja 2026-05-06).
        # Mutuje cs PRZED no-position check, żeby kurier 5+ min po starcie z brakiem
        # GPS NIE był wyrzucony jako "no_position".
        if _post_shift_5min_enabled and cs.pos is None and is_on_shift is not None:
            if _post_shift_start_synthetic_eligible(
                cs, _now_utc_fleet, schedule, match_courier, _shift_start_dt, is_on_shift
            ):
                cs.pos = BIALYSTOK_CENTER
                cs.pos_source = "post_shift_start_synthetic"
                _log.debug(f"post_shift_5min synthetic {cs.name} ({cs.courier_id})")
        # Working-override (Adrian 2026-06-01): operator wpisał "X pracuje" → cs.courier_id
        # w working set. Daj syntetyczną pozycję (BIALYSTOK_CENTER, jak pre_shift) gdy brak
        # GPS, żeby kurier spoza grafiku był dispatchowalny od razu. Realny GPS wygrywa
        # (granted tylko gdy cs.pos is None). Flag-gated ENABLE_WORKING_OVERRIDE.
        if (_working_override_enabled and cs.pos is None and working
                and str(cs.courier_id or "") in working):
            cs.pos = BIALYSTOK_CENTER
            cs.pos_source = "working_override_synthetic"
            _log.debug(f"working_override synthetic pos {cs.name} ({cs.courier_id})")
        if cs.pos is None:
            _rejected_for_log.append({"cid": str(cs.courier_id or ""), "panel_name": cs.name,
                                      "reason": "no_position", "pos_source": cs.pos_source})
            continue
        _cid_str_excl = str(cs.courier_id or "")
        _excl_by_name = bool(cs.name and cs.name in excluded)
        _excl_by_cid = bool(_exclude_by_cid_enabled and _cid_str_excl and _cid_str_excl in excluded_cids)
        if _excl_by_name or _excl_by_cid:
            _how = "name+cid" if (_excl_by_name and _excl_by_cid) else ("cid" if _excl_by_cid else "name")
            _log.debug(f"skip {cs.name} ({cs.courier_id}): manual override [{_how}]")
            _rejected_for_log.append({"cid": _cid_str_excl, "panel_name": cs.name,
                                      "reason": "manual_override", "match": _how})
            continue

        # ===== Working-override (Adrian 2026-06-01) — FALLBACK gałąź =====
        # Operator jawnie wpisał "X pracuje". Override cid-keyed (jednoznaczny — omija
        # fuzzy match_courier, więc zero ambiguity z V3.25 landmine "Jakub OL").
        # FALLBACK: bierze górę TYLKO gdy kurier NIE jest na realnej zmianie teraz —
        # pokrywa "brak w grafiku" (spoza), "zmiana skończona", "przed zmianą". Gdy kurier
        # JEST na realnej zmianie → realny grafik wygrywa (zachowuje realne godziny, by NIE
        # rozszerzać powracającemu po /stop zmiany do końca dnia). Flag ENABLE_WORKING_OVERRIDE.
        _wo_entry = (working.get(str(cs.courier_id or ""))
                     if (_working_override_enabled and working) else None)
        if _wo_entry is not None:
            _real_on_shift_now = False
            _real_grafik_entry = None
            if schedule and cs.name and match_courier is not None and is_on_shift is not None:
                _rfn = match_courier(cs.name, schedule)
                if _rfn is not None and schedule.get(_rfn) is not None:
                    _real_grafik_entry = schedule.get(_rfn)
                    _ros, _ = is_on_shift(cs.name, schedule)
                    _real_on_shift_now = bool(_ros)
            if not _real_on_shift_now:
                cs.shift_start = _shift_start_dt(_wo_entry)
                # GRAFIK-CAP (2026-06-07): domyślny koniec "pracuje" (24:00) wpisany w trakcie/
                # przed realnym grafikiem NIE wskrzesza kuriera po realnym końcu zmiany —
                # przycina shift_end do min(override_end, grafik_end). Patrz helper docstring.
                cs.shift_end = _effective_working_override_shift_end(
                    _wo_entry, _real_grafik_entry, _wo_grafik_cap_enabled)
                if cs.shift_end is not None and _now_utc_fleet >= cs.shift_end:
                    # override (po ew. cap'ie do grafiku) już minął → po zmianie, pomiń (off-shift).
                    _log.debug(f"skip {cs.name} ({cs.courier_id}): working_override po zmianie (grafik-cap)")
                    _rejected_for_log.append({"cid": str(cs.courier_id or ""), "panel_name": cs.name,
                                              "reason": "working_override_ended"})
                    continue
                _wo_mins = _mins_to_shift_start(_wo_entry)
                if _wo_mins is not None and _wo_mins > 0:
                    # MANUALNY working-override koordynatora — NIE capujemy oknem pre-shift
                    # (jawna decyzja człowieka ma pierwszeństwo, Lekcja #26). Cap 60 min
                    # dotyczy tylko automatycznego pre-shift grafikowego (V3.24-A niżej).
                    cs.pos_source = "pre_shift"
                    cs.pos = BIALYSTOK_CENTER
                    cs.shift_start_min = _wo_mins
                    _log.debug(f"working_override pre_shift {cs.name} ({cs.courier_id}): za {_wo_mins:.0f} min")
                # else: start <= now → na zmianie teraz, proceed.
                cs.schedule_source_stale = _sched_stale
                result.append(cs)
                _passed_for_log.append({"cid": str(cs.courier_id or ""), "panel_name": cs.name,
                                        "pos_source": cs.pos_source})
                continue
            # else: kurier na realnej zmianie → realny grafik wygrywa, ścieżka niżej.

        if schedule and cs.name:
            full_name = match_courier(cs.name, schedule)
            if full_name is None:
                _log.debug(f"skip {cs.name} ({cs.courier_id}): brak w grafiku")
                _rejected_for_log.append({"cid": str(cs.courier_id or ""), "panel_name": cs.name,
                                          "reason": "schedule_no_match"})
                continue
            entry = schedule.get(full_name)
            if entry is None:
                _log.debug(f"skip {cs.name} ({cs.courier_id}): nie pracuje dziś")
                _rejected_for_log.append({"cid": str(cs.courier_id or ""), "panel_name": cs.name,
                                          "reason": "not_working_today",
                                          "schedule_name": full_name})
                continue
            on_shift, reason = is_on_shift(cs.name, schedule)
            # Set shift_end + shift_start z grafiku (V3.25 R-01 R-NO-WASTE PRE-CHECK
            # potrzebuje obu — dropoff vs end, pickup vs start).
            cs.shift_end = _shift_end_dt(entry)
            cs.shift_start = _shift_start_dt(entry)
            if not on_shift:
                # Pre-shift: kurier z dzisiejszą zmianą — dopuszczamy z synthetic
                # pos. V3.24-A: brak 50min gate (extension_penalty + dropoff
                # hard reject w feasibility layer zapewniają sensowne scoring).
                # Legacy (flag=False): zachowane PRE_SHIFT_WINDOW_MIN=50 gate.
                mins = _mins_to_shift_start(entry)
                from dispatch_v2 import common as C_SCHED
                if C_SCHED.ENABLE_V324A_SCHEDULE_INTEGRATION:
                    if mins is not None and 0 < mins <= C_SCHED.PRE_SHIFT_WINDOW_MAX_MIN:
                        cs.pos_source = "pre_shift"
                        cs.pos = BIALYSTOK_CENTER
                        cs.shift_start_min = mins
                        _log.debug(f"pre_shift v324a {cs.name} ({cs.courier_id}): za {mins:.0f} min")
                    else:
                        _log.debug(f"skip {cs.name} ({cs.courier_id}): {reason} "
                                   f"(okno pre-shift {C_SCHED.PRE_SHIFT_WINDOW_MAX_MIN:.0f} min)")
                        _rejected_for_log.append({"cid": str(cs.courier_id or ""), "panel_name": cs.name,
                                                  "reason": f"off_shift_or_window: {reason}"})
                        continue
                else:
                    if mins is not None and 0 < mins <= PRE_SHIFT_WINDOW_MIN:
                        cs.pos_source = "pre_shift"
                        cs.pos = BIALYSTOK_CENTER
                        cs.shift_start_min = mins
                        _log.debug(f"pre_shift {cs.name} ({cs.courier_id}): za {mins:.0f} min")
                    else:
                        _log.debug(f"skip {cs.name} ({cs.courier_id}): {reason}")
                        _rejected_for_log.append({"cid": str(cs.courier_id or ""), "panel_name": cs.name,
                                                  "reason": f"pre_shift_window_miss: {reason}"})
                        continue
        cs.schedule_source_stale = _sched_stale  # D2: stempel stale-grafik dla feasibility
        result.append(cs)
        _passed_for_log.append({"cid": str(cs.courier_id or ""), "panel_name": cs.name,
                                "pos_source": cs.pos_source})

    # TASK 3 observability hook — NIGDY raise (flag-gated, isolated try/except)
    # A1: dawniej silent → audit trail lost cicho. Dedup-by-class.
    try:
        from dispatch_v2.observability.candidate_logger import get_logger
        get_logger().log_fleet_filter(
            source="courier_resolver.dispatchable_fleet",
            passed=_passed_for_log,
            rejected=_rejected_for_log,
            context={"fleet_total": len(fleet)},
        )
    except Exception as _e:
        seen = getattr(dispatchable_fleet, "_warned_audit", set())
        cls = type(_e).__name__
        if cls not in seen:
            _log.warning(f"fleet_filter audit log fail ({cls}: {_e}) — audit trail lost")
            seen.add(cls)
            dispatchable_fleet._warned_audit = seen

    return result
