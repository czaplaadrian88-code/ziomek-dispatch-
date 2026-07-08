#!/usr/bin/env python3
"""eta_calibration.features — budowa feature-store per zlecenie, per noga (L1 odbiór, L3 dostawa).

Źródła (READ-ONLY): sla_log (rzeczywisty odbiór/dostawa), Rutcom CSV (obietnica czas_kuriera),
ziomek_pred_calibration (predykcja silnika = baseline), geocode_cache + geocoding (coords),
restaurant_meta (prep-variance), OSRM :5001 (free-flow per noga).

Kluczowe cechy (znane w momencie predykcji — bez wycieku z przyszłości):
  - kontekst floty: OBCIĄŻENIE (rekonstruowane z interwałów picked_up..delivered per kurier)
  - czas: godzina, slot (peak_lunch/high_risk/peak_dinner/off), weekday
  - zlecenie: OSRM dystans/free-flow (restauracja→dostawa), restauracja (prep-variance), solo/worek
  - kurier: courier_id (agregaty historyczne = model-side, leakage-safe w models.py)
TARGETY (rzeczywiste): odbiór (picked_up vs czas_kuriera / pred silnika), dostawa (delivered-picked_up).

Pisze WYŁĄCZNIE do eta_calib.db (tabela eta_calib_features). Zero mutacji obiektów Ziomka.
Pseudonimizacja: courier_id to identyfikator systemowy (nie imię); adresy → tylko coords (bez tekstu).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import os
import sqlite3
import sys
import urllib.request
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from zoneinfo import ZoneInfo

# import geocodera repo (cache-first) — dual-path (moduł/standalone)
sys.path.insert(0, "/root/.openclaw/workspace/scripts")
try:
    from dispatch_v2 import geocoding as _GEO  # type: ignore
except Exception:  # pragma: no cover
    _GEO = None

WARSAW = ZoneInfo("Europe/Warsaw")
log = logging.getLogger("eta_calib.features")

# ── slot dayparts (spójne z calib_maps.time_slot_warsaw) ──
def slot_of(hour: int) -> str:
    if 11 <= hour < 14:
        return "peak_lunch"
    if 14 <= hour < 17:
        return "high_risk"
    if 17 <= hour < 20:
        return "peak_dinner"
    return "off"


def pseudonymize(courier_id: str, salt: str = "eta_calib") -> str:
    """Stabilny pseudonim do RAPORTÓW (KURIER_xxxx). Store używa realnego id."""
    h = hashlib.sha1(f"{salt}:{courier_id}".encode()).hexdigest()[:6]
    return f"KURIER_{h}"


# ── parsery czasu ──
def parse_naive_warsaw(s) -> Optional[datetime]:
    """picked_up_at/delivered_at = naiwny czas lokalny Warszawy → aware UTC."""
    if not s or not isinstance(s, str):
        return None
    try:
        d = datetime.fromisoformat(s.strip())
        return (d.replace(tzinfo=WARSAW) if d.tzinfo is None else d).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def parse_iso(s) -> Optional[datetime]:
    if not s or not isinstance(s, str):
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def czas_kuriera_dt(hhmm, ref_utc: Optional[datetime]) -> Optional[datetime]:
    """czas_kuriera HH:MM (Warsaw) tego samego dnia co ref → aware UTC."""
    if not hhmm or ref_utc is None:
        return None
    try:
        hh, mm = str(hhmm).split(":")[:2]
        ref_w = ref_utc.astimezone(WARSAW)
        cand = ref_w.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        return cand.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def read_jsonl(path: str):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue


# ── OSRM free-flow z cache w eta_calib.db ──
class OSRM:
    def __init__(self, base_url: str, timeout_s: int, cache: Dict[str, Tuple[float, float]],
                 max_calls: int):
        self.base = base_url.rstrip("/")
        self.timeout = timeout_s
        self.cache = cache
        self.max_calls = max_calls
        self.calls = 0

    def freeflow(self, a: Tuple[float, float], b: Tuple[float, float]) -> Optional[Tuple[float, float]]:
        """(dist_km, dur_min) free-flow. Cache po zaokrąglonych coords. None gdy limit/err."""
        if a is None or b is None:
            return None
        key = f"{a[0]:.4f},{a[1]:.4f};{b[0]:.4f},{b[1]:.4f}"
        if key in self.cache:
            return self.cache[key]
        if self.calls >= self.max_calls:
            return None
        # OSRM oczekuje lon,lat
        url = f"{self.base}/route/v1/driving/{a[1]:.5f},{a[0]:.5f};{b[1]:.5f},{b[0]:.5f}?overview=false"
        try:
            self.calls += 1
            with urllib.request.urlopen(url, timeout=self.timeout) as r:
                data = json.loads(r.read())
            rt = data["routes"][0]
            val = (rt["distance"] / 1000.0, rt["duration"] / 60.0)
            self.cache[key] = val
            return val
        except Exception as e:  # noqa: BLE001 — fail-soft
            log.debug("OSRM err %s: %s", key, e)
            return None


def _geo_cache_direct(path: str) -> dict:
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return {}


import re as _re

# gminy okoliczne Białegostoku (delivery_address bywa spoza miasta)
_CITY_TOKENS = ("Białystok", "Kleosin", "Wasilków", "Grabówka", "Zaścianki", "Choroszcz",
                "Sobolewo", "Księżyno", "Ignatki", "Porosły", "Karakule", "Olmonty")


def _addr_variants(address: str) -> List[str]:
    """Warianty klucza cache: strip miasta inline + 'lokal/piętro/klatka', wiele form
    normalizacji × miasto. Podnosi trafienia cache (miasto inline bez przecinka)."""
    a = str(address).strip()
    bases = {a}
    # odetnij sufiks lokal/piętro/klatka
    a2 = _re.split(r'\s+(lokal|piętro|klatka|m\.)\b', a, flags=_re.I)[0].strip().rstrip(',').strip()
    bases.add(a2)
    # odetnij miasto z końca
    for b in list(bases):
        stripped = _re.sub(r'\s+(' + '|'.join(_CITY_TOKENS) + r')\s*$', '', b, flags=_re.I).strip().rstrip(',').strip()
        bases.add(stripped)
    keys = []
    for base in bases:
        if not base:
            continue
        low = base.lower()
        keys.append(low)
        keys.append(low + ", białystok")
        if _GEO is not None:
            for city in ("Białystok", "Kleosin", "Wasilków", "Grabówka"):
                try:
                    keys.append(_GEO._normalize(base, city))
                except Exception:
                    pass
    return keys


def geocode_cached(address: str, geocache: dict, city: str = "Białystok",
                   budget: List[int] = None) -> Optional[Tuple[float, float]]:
    """CACHE-ONLY geokod (zero zapisu do cache Ziomka). Próbuje wiele wariantów klucza
    (miasto inline, sufiksy lokal/piętro) → wysokie pokrycie bez sieci. None gdy brak."""
    if not address:
        return None
    for key in _addr_variants(address):
        v = geocache.get(key)
        if isinstance(v, dict) and v.get("lat") and v.get("lon"):
            return (float(v["lat"]), float(v["lon"]))
    return None


def reconstruct_load(sla_rows: List[dict]) -> Dict[str, int]:
    """OBCIĄŻENIE per order = ile zleceń kurier miał odebranych-ale-niedostarczonych
    w momencie ODBIORU tego zlecenia (rekonstrukcja z interwałów picked_up..delivered)."""
    by_courier: Dict[str, List[Tuple[datetime, datetime, str]]] = {}
    for r in sla_rows:
        cid = str(r.get("courier_id"))
        pu = parse_naive_warsaw(r.get("picked_up_at"))
        dl = parse_naive_warsaw(r.get("delivered_at"))
        if cid and pu and dl and dl >= pu:
            by_courier.setdefault(cid, []).append((pu, dl, str(r.get("order_id"))))
    load: Dict[str, int] = {}
    for cid, ivs in by_courier.items():
        for pu, dl, oid in ivs:
            # ile innych interwałów tego kuriera obejmuje moment pu
            c = sum(1 for (p2, d2, o2) in ivs if o2 != oid and p2 <= pu <= d2)
            load[oid] = c + 1  # +1 = to zlecenie
    return load


DDL = """
CREATE TABLE IF NOT EXISTS eta_calib_features (
    order_id TEXT PRIMARY KEY, courier_id TEXT, day TEXT,
    ts_pickup TEXT, ts_deliver TEXT,
    rest_lat REAL, rest_lon REAL, deliv_lat REAL, deliv_lon REAL,
    osrm_deliv_km REAL, osrm_deliv_ff_min REAL,
    actual_deliver_min REAL,
    czas_kuriera TEXT, pickup_slip_koord_min REAL,
    eng_pickup_slip_min REAL, eng_deliver_pred_min REAL,
    load INTEGER, hour INTEGER, slot TEXT, weekday INTEGER,
    is_bundle INTEGER, was_czasowka INTEGER,
    prep_var_med REAL, pace_deliv REAL
);
CREATE TABLE IF NOT EXISTS eta_calib_osrm_cache (key TEXT PRIMARY KEY, dist_km REAL, dur_min REAL);
"""


def load_osrm_cache(con) -> Dict[str, Tuple[float, float]]:
    cur = con.execute("SELECT key, dist_km, dur_min FROM eta_calib_osrm_cache")
    return {k: (d, t) for k, d, t in cur.fetchall()}


def save_osrm_cache(con, cache: Dict[str, Tuple[float, float]]):
    con.executemany("INSERT OR REPLACE INTO eta_calib_osrm_cache VALUES (?,?,?)",
                    [(k, v[0], v[1]) for k, v in cache.items()])
    con.commit()


def build(cfg: dict) -> dict:
    """Buduje feature-store. Zwraca statystyki pokrycia. Idempotentne (INSERT OR REPLACE)."""
    p = cfg["paths"]
    con = sqlite3.connect(p["db"])
    con.executescript(DDL)

    # ── ground truth: sla_log ──
    sla_rows = list(read_jsonl(p["sla_log"]))
    sla_by_oid = {str(r.get("order_id")): r for r in sla_rows if r.get("order_id")}
    log.info("sla_log: %d rekordów", len(sla_rows))

    # ── obietnica koordynatora: Rutcom ──
    rutcom: Dict[str, str] = {}
    if os.path.exists(p["rutcom_csv"]):
        for r in csv.DictReader(open(p["rutcom_csv"], encoding="utf-8")):
            rutcom[str(r.get("nr zlecenia"))] = r.get("czas kuriera")
    log.info("Rutcom: %d rekordów (czas_kuriera)", len(rutcom))

    # ── baseline silnika + ŻYWY czas_kuriera: ziomek_pred_calibration (co 3 min) ──
    eng: Dict[str, dict] = {}
    ck_live: Dict[str, str] = {}   # czas_kuriera ze źródła ŻYWEGO (dla zleceń spoza CSV Rutcom)
    for r in read_jsonl(p["ziomek_pred"]):
        oid = str(r.get("oid"))
        pa = parse_iso(r.get("pickup_pred_assign"))
        da = parse_iso(r.get("delivery_pred_assign"))
        eng[oid] = {
            "rozjazd_odbior_assign": r.get("rozjazd_odbior_assign"),
            "eng_deliver_pred_min": ((da - pa).total_seconds() / 60.0
                                     if pa and da else None),
        }
        if r.get("czas_kuriera_hhmm"):
            ck_live[oid] = r.get("czas_kuriera_hhmm")
    # merge: Rutcom CSV (historia) PRIORYTET, żywy ziomek_pred fallback (nowe zlecenia).
    # Dzięki temu cień dzienny widzi NOWE zlecenia (CSV Rutcom jest statyczny).
    for oid, ck in ck_live.items():
        rutcom.setdefault(oid, ck)
    log.info("czas_kuriera: Rutcom+żywy = %d zleceń", len(rutcom))

    # ── prep-variance per restauracja ──
    prep: Dict[str, float] = {}
    try:
        rm = json.load(open(p["geocode_cache"].replace("geocode_cache.json", "restaurant_meta.json")))
        for name, meta in (rm.get("restaurants") or {}).items():
            pv = (meta or {}).get("prep_variance_min") or {}
            if isinstance(pv, dict) and pv.get("median") is not None:
                prep[str(name).strip().lower()] = float(pv["median"])
    except Exception as e:
        log.warning("prep-variance load: %s", e)

    # ── obciążenie ──
    load_map = reconstruct_load(sla_rows)
    log.info("obciążenie zrekonstruowane dla %d zleceń", len(load_map))

    # ── geokod + OSRM ──
    geocache = _geo_cache_direct(p["geocode_cache"])
    # precompute coords restauracji RAZ (nie per-order) — budżet-bezpieczne
    rest_coords: Dict[str, Optional[Tuple[float, float]]] = {}
    rnet = [int(cfg["osrm"].get("external_budget_per_day", 100))]
    for name in {str(s.get("restaurant", "")) for s in sla_by_oid.values() if s.get("restaurant")}:
        c = geocode_cached(name, geocache, budget=rnet)
        if c is None and _GEO is not None and rnet[0] > 0:
            try:
                rnet[0] -= 1
                c = _GEO.geocode_restaurant(name)
                c = (float(c[0]), float(c[1])) if c else None
            except Exception:
                c = None
        rest_coords[str(name).strip().lower()] = c
    log.info("coords restauracji: %d/%d rozwiązanych",
             sum(1 for v in rest_coords.values() if v), len(rest_coords))
    osrm_cache = load_osrm_cache(con)
    osrm = OSRM(cfg["osrm"]["base_url"], cfg["osrm"]["timeout_s"], osrm_cache,
                cfg["osrm"]["max_calls_per_run"])
    net_budget = [int(cfg["osrm"].get("external_budget_per_day", 100))]

    w = cfg["window"]
    rows_out = []
    n_seen = n_geo_ok = n_osrm_ok = n_target_ok = 0
    for oid, rutcom_ck in rutcom.items():
        s = sla_by_oid.get(oid)
        if not s:
            continue
        n_seen += 1
        pu = parse_naive_warsaw(s.get("picked_up_at"))
        dl = parse_naive_warsaw(s.get("delivered_at"))
        if not pu or not dl:
            continue
        actual_deliver = (dl - pu).total_seconds() / 60.0
        if not (w["min_deliver_min"] <= actual_deliver <= w["max_deliver_min"]):
            continue
        n_target_ok += 1
        # coords (restauracja z precompute; dostawa cache-first)
        rcoord = rest_coords.get(str(s.get("restaurant", "")).strip().lower())
        dcoord = geocode_cached(s.get("delivery_address", ""), geocache, budget=net_budget)
        osrm_km = osrm_ff = None
        if rcoord and dcoord:
            n_geo_ok += 1
            ff = osrm.freeflow(tuple(rcoord[:2]), tuple(dcoord[:2]))
            if ff:
                osrm_km, osrm_ff = ff
                n_osrm_ok += 1
        # slip odbioru
        ck = czas_kuriera_dt(rutcom_ck, pu)
        slip = (pu - ck).total_seconds() / 60.0 if ck else None
        if slip is not None and abs(slip) > w["min_slip_abs_min"]:
            slip = None
        hour = pu.astimezone(WARSAW).hour
        e = eng.get(oid, {})
        rest_norm = str(s.get("restaurant", "")).strip().lower()
        pace = (actual_deliver / osrm_ff) if (osrm_ff and osrm_ff > 0.5) else None
        rows_out.append((
            oid, str(s.get("courier_id")), pu.astimezone(WARSAW).date().isoformat(),
            pu.isoformat(), dl.isoformat(),
            round(rcoord[0], 5) if rcoord else None, round(rcoord[1], 5) if rcoord else None,
            round(dcoord[0], 5) if dcoord else None, round(dcoord[1], 5) if dcoord else None,
            round(osrm_km, 3) if osrm_km else None, round(osrm_ff, 2) if osrm_ff else None,
            round(actual_deliver, 2),
            rutcom_ck, round(slip, 2) if slip is not None else None,
            e.get("rozjazd_odbior_assign"), e.get("eng_deliver_pred_min"),
            load_map.get(oid), hour, slot_of(hour), pu.astimezone(WARSAW).weekday(),
            1 if (load_map.get(oid, 1) or 1) >= 2 else 0,
            1 if s.get("was_czasowka") else 0,
            prep.get(rest_norm), round(pace, 3) if pace else None,
        ))

    ncol = 24  # liczba kolumn eta_calib_features (musi == długość krotki)
    assert all(len(r) == ncol for r in rows_out[:1]) if rows_out else True, "kolumny != krotka"
    con.executemany(
        "INSERT OR REPLACE INTO eta_calib_features VALUES (%s)" % ",".join(["?"] * ncol),
        rows_out)
    con.commit()
    save_osrm_cache(con, osrm.cache)
    con.close()

    stats = dict(rutcom=len(rutcom), matched_sla=n_seen, target_ok=n_target_ok,
                 geo_ok=n_geo_ok, osrm_ok=n_osrm_ok, written=len(rows_out),
                 osrm_calls=osrm.calls, net_budget_left=net_budget[0])
    log.info("FEATURE STORE: %s", json.dumps(stats, ensure_ascii=False))
    return stats


def load_config(path: Optional[str] = None) -> dict:
    import yaml
    path = path or os.path.join(os.path.dirname(__file__), "config.yaml")
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    args = ap.parse_args()
    cfg = load_config(args.config)
    stats = build(cfg)
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
