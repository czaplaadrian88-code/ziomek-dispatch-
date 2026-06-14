"""SP-B2-PLN (2026-06-11) — funkcja celu w PLN (shadow obok score).

Raport BARTEK_2.0 §6 / agent_econ/REPORT.md §3 (kalibracja 52,9k dostaw):

    V(kurier ← zlecenie) =
          6,33                                  # marża pokrycia
        − koszt_km(vehicle) · Δkm               # 0,90 firmowe / 0 własne
        − 14 · P(breach | Δkm, worek+1, load)   # σ(−5,746 + 0,297·km
                                                #   + 0,649·worek + 0,090·load)
        − 0,20 · max(0, dojazd − gotowość)      # leżenie jedzenia [min]
        − opp(t) · (blokada + czekanie)         # koszt opcji [PLN/min]
    opp(t) = 0,07 w peaku 13-20 Warsaw (81,6% wolumenu), 0,32 przy load>3,5,
             0,01 poza peakiem.

Score nie przewiduje wyniku (§4.1: breach płaski ~8% dla score -100..+90,
przy >90 ROŚNIE); funkcja PLN flipuje 50% decyzji z med +1,82 PLN (§6.3).

SHADOW ONLY: czysta telemetria za flagą ENABLE_PLN_OBJECTIVE_SHADOW (ON).
Jakiekolwiek użycie w decyzjach = 🛑 ACK Adriana (docelowo: PLN selektorem,
score debugiem). Pure math — zero I/O poza mtime-cache courier_vehicle.json.

vehicle_owner: opcjonalny plik dispatch_state/courier_vehicle.json
{"<cid>": "wlasne"|"firmowe"}; brak pliku/wpisu → "firmowe" (73% dostaw,
koszt km 0,90 — konserwatywnie pełny koszt).
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:
    from zoneinfo import ZoneInfo
    _WARSAW = ZoneInfo("Europe/Warsaw")
except Exception:  # pragma: no cover
    _WARSAW = None

# ── Stałe kalibracji (agent_econ, maj 2026) — env-overridable ──
PLN_MARGIN = float(os.environ.get("PLN_MARGIN", "6.33"))
PLN_KM_COST_FIRMOWE = float(os.environ.get("PLN_KM_COST_FIRMOWE", "0.90"))
PLN_KM_COST_WLASNE = float(os.environ.get("PLN_KM_COST_WLASNE", "0.0"))
PLN_BREACH_COST = float(os.environ.get("PLN_BREACH_COST", "14.0"))
PLN_FRESH_COST_PER_MIN = float(os.environ.get("PLN_FRESH_COST_PER_MIN", "0.20"))
PLN_OPP_PEAK = float(os.environ.get("PLN_OPP_PEAK", "0.07"))
PLN_OPP_OVERLOAD = float(os.environ.get("PLN_OPP_OVERLOAD", "0.32"))
PLN_OPP_OFF = float(os.environ.get("PLN_OPP_OFF", "0.01"))
PLN_OPP_OVERLOAD_AT = float(os.environ.get("PLN_OPP_OVERLOAD_AT", "3.5"))
PLN_OPP_PEAK_START_H = int(os.environ.get("PLN_OPP_PEAK_START_H", "13"))
PLN_OPP_PEAK_END_H = int(os.environ.get("PLN_OPP_PEAK_END_H", "20"))
# Logit P(breach) — kalibracja 52,9k dostaw (agent_econ).
PLN_LOGIT_INTERCEPT = float(os.environ.get("PLN_LOGIT_INTERCEPT", "-5.746"))
PLN_LOGIT_KM = float(os.environ.get("PLN_LOGIT_KM", "0.297"))
PLN_LOGIT_BAG = float(os.environ.get("PLN_LOGIT_BAG", "0.649"))
PLN_LOGIT_LOAD = float(os.environ.get("PLN_LOGIT_LOAD", "0.090"))

COURIER_VEHICLE_PATH = os.environ.get(
    "COURIER_VEHICLE_PATH",
    "/root/.openclaw/workspace/dispatch_state/courier_vehicle.json",
)
_vehicle_cache: Dict[str, Any] = {"mtime": None, "data": {}}

# Płaca kuriera per-osoba (mirror panel finance.courier_cost_components). Sync z
# panelu → courier_pay.json {cid:{mode,tariff_base,tariff_per_km,hourly_rate,active}}.
# Term PLN = −(realna_płaca − PLN_AVG_COURIER_PAY); średnia floty=0 → neutralny.
PLN_AVG_COURIER_PAY = float(os.environ.get("PLN_AVG_COURIER_PAY", "0.0"))
COURIER_PAY_PATH = os.environ.get(
    "COURIER_PAY_PATH",
    "/root/.openclaw/workspace/dispatch_state/courier_pay.json",
)
_pay_cache: Dict[str, Any] = {"mtime": None, "data": {}}


def _vehicle_for(cid) -> str:
    """'wlasne' | 'firmowe' z courier_vehicle.json; fail-soft → 'firmowe'."""
    try:
        mt = os.path.getmtime(COURIER_VEHICLE_PATH)
    except OSError:
        return "firmowe"
    try:
        if _vehicle_cache["mtime"] != mt:
            with open(COURIER_VEHICLE_PATH, encoding="utf-8") as fh:
                d = json.load(fh)
            _vehicle_cache["data"] = d if isinstance(d, dict) else {}
            _vehicle_cache["mtime"] = mt
        v = str(_vehicle_cache["data"].get(str(cid), "firmowe")).lower()
        return "wlasne" if v in ("wlasne", "własne", "own") else "firmowe"
    except Exception:
        return "firmowe"


def _pay_for(cid) -> Optional[Dict[str, Any]]:
    """Profil płac kuriera z courier_pay.json (mtime-cache); None gdy brak/nieaktywny."""
    try:
        mt = os.path.getmtime(COURIER_PAY_PATH)
    except OSError:
        return None
    try:
        if _pay_cache["mtime"] != mt:
            with open(COURIER_PAY_PATH, encoding="utf-8") as fh:
                d = json.load(fh)
            _pay_cache["data"] = d if isinstance(d, dict) else {}
            _pay_cache["mtime"] = mt
        p = _pay_cache["data"].get(str(cid))
        return p if isinstance(p, dict) and p.get("active", True) else None
    except Exception:
        return None


def courier_labor_cost(profile: Optional[Dict[str, Any]], *, duration_min: float) -> Optional[float]:
    """Realny koszt PŁACY tej dostawy [zł] wg profilu (mirror panel finance.courier_cost_components):
    tariff/both → tariff_base; hourly/both → +(duration/60)×hourly_rate. None gdy brak profilu.
    (Składnik per_km pomijamy w v1 — pokrywa się z km_cost; refinement.)"""
    if not profile:
        return None
    mode = str(profile.get("mode", "tariff"))
    labor = 0.0
    if mode in ("tariff", "both"):
        labor += float(profile.get("tariff_base") or 0.0)
    if mode in ("hourly", "both"):
        labor += (max(0.0, float(duration_min)) / 60.0) * float(profile.get("hourly_rate") or 0.0)
    return labor


def p_breach(delta_km: float, bag_after: int, load: float) -> float:
    """P(breach) = σ(−5,746 + 0,297·km + 0,649·worek + 0,090·load)."""
    z = (PLN_LOGIT_INTERCEPT
         + PLN_LOGIT_KM * max(0.0, float(delta_km))
         + PLN_LOGIT_BAG * max(0, int(bag_after))
         + PLN_LOGIT_LOAD * max(0.0, float(load)))
    try:
        return 1.0 / (1.0 + math.exp(-z))
    except OverflowError:
        return 1.0 if z > 0 else 0.0


def opp_rate(now: Optional[datetime], load: Optional[float]) -> float:
    """Koszt opcji PLN/min: 0,32 przy load>3,5; 0,07 w peaku 13-20 Warsaw; 0,01 poza."""
    if load is not None and float(load) > PLN_OPP_OVERLOAD_AT:
        return PLN_OPP_OVERLOAD
    try:
        if now is None:
            now = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        h = now.astimezone(_WARSAW).hour if _WARSAW is not None else now.hour
        if PLN_OPP_PEAK_START_H <= h < PLN_OPP_PEAK_END_H:
            return PLN_OPP_PEAK
    except Exception:
        pass
    return PLN_OPP_OFF


def compute_pln_value(
    *,
    cid,
    delta_km: Optional[float],
    bag_before: Optional[int],
    load: Optional[float],
    travel_min: Optional[float],
    time_to_ready_min: Optional[float],
    blokada_min: Optional[float] = None,
    now: Optional[datetime] = None,
    apply_courier_pay: bool = False,
) -> Optional[Dict[str, Any]]:
    """V [PLN] dla kandydata. None gdy brak Δkm/travel (nie zgadujemy).

    delta_km — przyrost km trasy (repo/dojazd + nowa noga pickup→drop);
    bag_before — worek PRZED dodaniem (funkcja używa worek+1);
    load — fleet load EWMA (None → 0 w logicie, opp wg pory);
    travel_min / time_to_ready_min — dojazd vs gotowość (leżenie/czekanie);
    blokada_min — czas zablokowania kuriera (default: travel + leg z Δkm).
    """
    if delta_km is None or travel_min is None:
        return None
    try:
        dkm = max(0.0, float(delta_km))
        bag_after = max(0, int(bag_before or 0)) + 1
        load_v = float(load) if load is not None else 0.0
        ready_min = max(0.0, float(time_to_ready_min)) if time_to_ready_min is not None else 0.0
        trav = max(0.0, float(travel_min))

        vehicle = _vehicle_for(cid)
        km_cost = PLN_KM_COST_WLASNE if vehicle == "wlasne" else PLN_KM_COST_FIRMOWE
        pb = p_breach(dkm, bag_after, load_v)
        lezenie_min = max(0.0, trav - ready_min)
        czekanie_min = max(0.0, ready_min - trav)
        if blokada_min is None:
            blokada_min = trav
        rate = opp_rate(now, load if load is not None else None)

        v = (PLN_MARGIN
             - km_cost * dkm
             - PLN_BREACH_COST * pb
             - PLN_FRESH_COST_PER_MIN * lezenie_min
             - rate * (max(0.0, float(blokada_min)) + czekanie_min))
        # Term płacy kuriera per-osoba (mirror panel). Liczony ZAWSZE (telemetria
        # cienia pln_v_payaware); APLIKOWANY do pln_v tylko gdy apply_courier_pay.
        _pay = _pay_for(cid)
        courier_labor = courier_labor_cost(_pay, duration_min=float(blokada_min))
        pay_mode = str(_pay.get("mode")) if _pay else None
        pay_delta = (courier_labor - PLN_AVG_COURIER_PAY) if courier_labor is not None else 0.0
        v_payaware = round(v - pay_delta, 2)
        if apply_courier_pay and courier_labor is not None:
            v = v - pay_delta
        return {
            "pln_v": round(v, 2),
            "pln_p_breach": round(pb, 4),
            "pln_delta_km": round(dkm, 2),
            "pln_vehicle": vehicle,
            "pln_lezenie_min": round(lezenie_min, 1),
            "pln_czekanie_min": round(czekanie_min, 1),
            "pln_opp_rate": rate,
            "pln_courier_labor": round(courier_labor, 2) if courier_labor is not None else None,
            "pln_pay_mode": pay_mode,
            "pln_v_payaware": v_payaware,
        }
    except Exception:
        return None


def reset_caches() -> None:
    _vehicle_cache["mtime"] = None
    _vehicle_cache["data"] = {}
