#!/usr/bin/env python3
"""sequential_replay — sekwencyjny harness „Ziomek sam prowadzi flotę”.

Pytanie: jak wyglądałby REALNY plan floty, gdyby Ziomek obsługiwał wszystkie
zlecenia z danego okna sam — z commitem każdej decyzji?

Różnica vs shadow log: shadow_dispatcher ocenia każde zlecenie w izolacji
(„opinia per order") — nie pamięta własnych wcześniejszych przydziałów. Tu
zlecenia idą po kolei w czasie napływu, KAŻDY przydział jest commitowany do
bagu kuriera, a kolejne zlecenie widzi flotę już zmienioną.

Tryb COLD-START: brak GPS-wiernej fotografii (events.db nie trzyma stanu
floty z przeszłości) → wszyscy kurierzy startują wolni z centrum Białegostoku.
Lekko optymistyczne na pierwszych 2-3 zleceniach per kurier; dla okna ~1h OK.

Kod: BIEŻĄCY pipeline (assess_order) — odpowiada „czy Ziomek poradziłby sobie
TERAZ", nie odtwarza bajt-wiernie wersji kodu z dnia okna.

ZERO dotknięcia produkcji: assess_order to czysty read (state files + OSRM).
Pre-proposal recheck (sieć/synth eventy) wyłączony monkeypatch'em na czas procesu.

Użycie:
  python3 -m dispatch_v2.tools.sequential_replay --date 2026-05-17 --from 12 --to 13
  python3 -m dispatch_v2.tools.sequential_replay --date 2026-05-17 --from 12 --to 13 --out /tmp/replay.json
"""
from __future__ import annotations

import os
import sys

# ── determinizm: stały PYTHONHASHSEED ──
# Hash str/bytes jest losowany per-proces (PYTHONHASHSEED random) → iteracja
# `set`'ów różni się MIĘDZY procesami. W pipeline przekłada się to na tie-break
# wśród bliskich remisów → `best_cid` skacze run-to-run (a w 397-zleceniowym
# rolling kumuluje się w best_effort ±~20). Wewnątrz procesu seed jest stały —
# dlatego mikro-test w jednym procesie wychodzi deterministyczny, a 2 osobne
# przebiegi nie. PYTHONHASHSEED czytany tylko przy starcie interpretera → re-exec.
if os.environ.get("PYTHONHASHSEED") != "0":
    os.environ["PYTHONHASHSEED"] = "0"
    os.execv(sys.executable, [sys.executable, *sys.argv])

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

_SCRIPTS_ROOT = "/root/.openclaw/workspace/scripts"
if _SCRIPTS_ROOT not in sys.path:
    sys.path.insert(0, _SCRIPTS_ROOT)

EVENTS_DB = "/root/.openclaw/workspace/dispatch_state/events.db"

# L1.2 (2026-07-02): learning_log ROTATION-AWARE przez kanon _rotated_logs.
# 2026-05-21 (audyt): czytaj AKTYWNY log + zrotowany `.1` (pełne pokrycie dat).
# Stary hardkod [żywy, .1] gubił .2.gz po rotacji (logrotate size 100M / daily +
# delaycompress). files_in_window daje pełny łańcuch (.N.gz→.1→żywy). Env
# `ZIOMEK_REPLAY_LEARNING_LOG` (':'-separowana lista) nadal nadpisuje wprost.
# learning_log NIE jest w ledger_io.LEDGER — iterujemy kanon na base path.
from dispatch_v2.tools import _rotated_logs  # noqa: E402

_LEARNING_BASE = "/root/.openclaw/workspace/dispatch_state/learning_log.jsonl"
_ENV_LEARNING_LOGS = [
    p for p in os.environ.get("ZIOMEK_REPLAY_LEARNING_LOG", "").split(":") if p
]
LEARNING_LOGS = _ENV_LEARNING_LOGS or _rotated_logs.files_in_window(_LEARNING_BASE)
# Wstecz-kompat: część kodu odwołuje się do LEARNING_LOG (pierwszy istniejący).
LEARNING_LOG = next((p for p in LEARNING_LOGS if os.path.exists(p)), LEARNING_LOGS[0])
WARSAW_OFFSET = "+02:00"  # maj 2026 — CEST

# ── monkeypatch PRZED importem pipeline: ubij sieciowe side-effecty ──
from dispatch_v2 import common as C  # noqa: E402

C.ENABLE_V327_PRE_PROPOSAL_RECHECK = False  # syntetyczny bag nie ma realnego oid w panelu

from dispatch_v2 import dispatch_pipeline as DP  # noqa: E402
from dispatch_v2.courier_resolver import (  # noqa: E402
    CourierState, _load_courier_names, _load_courier_tiers,
)

COLD_START_POS = DP._BIALYSTOK_CENTER_FALLBACK

# ── determinizm OR-Tools dla powtarzalnego replay (2026-05-18) ──
# Produkcja solvuje z `time_limit` 200 ms wall-clock → liczba iteracji GLS zależy
# od obciążenia CPU = wynik niedeterministyczny. W rolling każde zlecenie jest
# oceniane ~5-10×, a commity kumulują rozjazd (best_effort wahał się 60↔81
# między przebiegami). GUIDED_LOCAL_SEARCH sam w sobie jest deterministyczny —
# niedeterminizm wnosi WYŁĄCZNIE wall-clock cutoff. Zamiana na `solution_limit`
# (stała liczba rozwiązań) usuwa go: replay staje się bajt-powtarzalny.
# Patch żyje tylko w procesie harnessu — ZERO wpływu na produkcję.
_OR_SOLUTION_LIMIT = 120  # reprezentatywny strop optymalizacji, powtarzalny
try:
    from ortools.constraint_solver import pywrapcp as _pywrapcp  # noqa: E402

    _orig_solve_with_params = _pywrapcp.RoutingModel.SolveWithParameters

    def _deterministic_solve(self, params):
        params.solution_limit = _OR_SOLUTION_LIMIT
        params.time_limit.FromMilliseconds(30_000)  # luźny strop; solution_limit wiąże
        return _orig_solve_with_params(self, params)

    _pywrapcp.RoutingModel.SolveWithParameters = _deterministic_solve
except Exception as _e:  # ortools brak / SWIG blokuje patch → replay leci jak dotąd
    print(f"[uwaga] OR-Tools determinism patch nieaktywny: {_e}")

# Sekwencyjna ewaluacja kandydatów — drugi (i decydujący) front determinizmu.
# dispatch_pipeline ocenia ~10 kurierów równolegle przez ThreadPoolExecutor;
# współbieżne solve'y OR-Tools dzielą proces-globalny stan solvera → wynik
# zależy od przeplotu wątków (best_cid skacze wśród bliskich remisów mimo
# solution_limit). Pipeline robi `from concurrent.futures import
# ThreadPoolExecutor` LOKALNIE w funkcji — podmiana atrybutu modułu łapie każde
# wywołanie. Drugie użycie (pre-proposal recheck) i tak wyłączone flagą wyżej.
import concurrent.futures as _cf  # noqa: E402


class _SeqExecutor:
    """Drop-in ThreadPoolExecutor — `.map()` sekwencyjnie, zero wątków."""

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def map(self, fn, *iterables):
        return list(map(fn, *iterables))

    def submit(self, fn, *a, **k):
        fut = _cf.Future()
        try:
            fut.set_result(fn(*a, **k))
        except Exception as _ex:
            fut.set_exception(_ex)
        return fut

    def shutdown(self, *a, **k):
        pass


_cf.ThreadPoolExecutor = _SeqExecutor


def _dt(iso):
    if not iso:
        return None
    d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# ─── 1. wczytanie zleceń z events.db ─────────────────────────────────

def load_orders(date: str, hour_from: int, hour_to: int) -> list:
    """NEW_ORDER events z okna [hour_from, hour_to) UTC danego dnia.
    Dedup po order_id (najwcześniejszy created_at). Zwraca listę order_event
    dict gotowych dla assess_order, posortowaną po czasie napływu."""
    con = sqlite3.connect(EVENTS_DB)
    likes = " OR ".join(
        f"created_at LIKE '{date}T{h:02d}%'" for h in range(hour_from, hour_to)
    )
    rows = con.execute(
        f"SELECT order_id, created_at, payload FROM events "
        f"WHERE event_type='NEW_ORDER' AND ({likes}) ORDER BY created_at",
    ).fetchall()
    con.close()
    seen = {}
    for oid, created_at, payload in rows:
        if oid in seen:
            continue
        ev = json.loads(payload)
        ev["order_id"] = str(oid)
        ev["_created_at"] = created_at
        seen[oid] = ev
    orders = sorted(seen.values(), key=lambda e: e["_created_at"])
    return orders


# ─── 2. roster — kto był na zmianie w tym oknie ──────────────────────

def build_roster(date: str, hour_from: int, hour_to: int) -> set:
    """Zbiór cidów które Ziomek realnie rozważał w oknie (z learning_log):
    decision.best.courier_id + PANEL_OVERRIDE proposed/actual. To demonstracyjnie
    aktywna flota tej godziny — bez zależności od Google Sheets grafiku."""
    cids = set()
    hrs = tuple(f'"ts": "{date}T{h:02d}:' for h in range(hour_from, hour_to))
    for log_path in LEARNING_LOGS:  # aktywny + zrotowany — pełne pokrycie dat
        if not os.path.exists(log_path):
            continue
        with _rotated_logs.open_maybe_gz(log_path) as f:
            for line in f:
                if not any(h in line for h in hrs):
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                dec = d.get("decision") or {}
                best = dec.get("best") or {}
                if best.get("courier_id"):
                    cids.add(str(best["courier_id"]))
                for k in ("proposed_courier_id", "actual_courier_id"):
                    if d.get(k):
                        cids.add(str(d[k]))
    return cids


# ─── 3. cold-start fleet ─────────────────────────────────────────────

def build_cold_fleet(roster: set, date: str) -> dict:
    """CourierState per cid: wolny, w centrum, tier z courier_tiers.json,
    zmiana 10:00-22:00 Warsaw (pełne pokrycie okna)."""
    names = _load_courier_names()
    tiers = _load_courier_tiers()
    shift_start = _dt(f"{date}T10:00:00{WARSAW_OFFSET}")
    shift_end = _dt(f"{date}T22:00:00{WARSAW_OFFSET}")
    fleet = {}
    for cid in roster:
        cs = CourierState(courier_id=cid)
        cs.pos = COLD_START_POS
        cs.pos_source = "cold_start"
        cs.pos_age_min = 0.0
        cs.bag = []
        cs.name = names.get(cid) or names.get(str(cid))
        cs.shift_start = shift_start
        cs.shift_end = shift_end
        tinfo = tiers.get(cid) if isinstance(tiers, dict) else None
        if isinstance(tinfo, dict):
            binfo = tinfo.get("bag") or {}
            cs.tier_bag = binfo.get("tier")
            cs.tier_cap_override = binfo.get("cap_override")
            cs.tier_label = tinfo.get("tier_label")
        fleet[cid] = cs
    return fleet


def reconstruct_inflight(date: str, hour_from: int) -> dict:
    """Ordery picked_up-ale-niedostarczone na starcie okna (T0 = date hour_from:00 UTC).

    Z events.db: COURIER_PICKED_UP (przed T0) minus COURIER_DELIVERED (przed T0).
    `_pred_delivered` = REALNY czas COURIER_DELIVERED → bag drenuje się dokładnie
    tyle ile kurier naprawdę jechał. To wierniejsze niż GPS: daje pozycję + ładunek.
    (gps_history w courier_api.db jest pusta dla 17.05 — GPS-per-se niedostępny.)

    Zwraca {cid: [bag_dict picked_up, ...]}.
    """
    T0 = f"{date}T{hour_from:02d}:00:00"
    con = sqlite3.connect(EVENTS_DB)
    picks = {}
    for oid, cid, ca in con.execute(
        "SELECT order_id, courier_id, created_at FROM events "
        "WHERE event_type='COURIER_PICKED_UP' AND created_at<?", (T0,)
    ):
        picks[str(oid)] = (str(cid), ca)
    deliv = {}
    for oid, ca in con.execute(
        "SELECT order_id, created_at FROM events WHERE event_type='COURIER_DELIVERED'"
    ):
        deliv.setdefault(str(oid), ca)
    bags = defaultdict(list)
    for oid, (cid, picked_at) in picks.items():
        d = deliv.get(oid)
        if d and d < T0:
            continue  # dostarczone przed oknem — nie w locie
        no = con.execute(
            "SELECT payload FROM events WHERE event_type='NEW_ORDER' "
            "AND order_id=? LIMIT 1", (oid,)
        ).fetchone()
        if not no:
            continue
        nop = json.loads(no[0])
        bags[cid].append({
            "order_id": oid,
            "status": "picked_up",
            "picked_up_at": picked_at,
            "pickup_coords": nop.get("pickup_coords"),
            "delivery_coords": nop.get("delivery_coords"),
            "pickup_at_warsaw": nop.get("pickup_at_warsaw"),
            "czas_kuriera_warsaw": nop.get("czas_kuriera_warsaw"),
            "courier_id": cid,
            "_pred_delivered": d,          # realny COURIER_DELIVERED (UTC ISO) lub None
            "_picked_at": picked_at,
        })
    con.close()
    return bags


def build_warm_fleet(roster: set, date: str, hour_from: int) -> tuple:
    """Cold fleet + nałożenie zrekonstruowanych bagów picked_up.

    Pozycja kuriera z bagiem = delivery_coords najświeższego picked_up ordera
    (identycznie jak build_fleet_snapshot, pos_source=last_picked_up_delivery).
    Zwraca (fleet, inflight_summary)."""
    inflight = reconstruct_inflight(date, hour_from)
    fleet = build_cold_fleet(roster | set(inflight.keys()), date)
    for cid, bag in inflight.items():
        cs = fleet.get(cid)
        if cs is None:
            continue
        cs.bag = list(bag)
        newest = max(bag, key=lambda b: b.get("_picked_at") or "")
        if newest.get("delivery_coords"):
            cs.pos = tuple(newest["delivery_coords"])
            cs.pos_source = "last_picked_up_delivery"
    summary = {cid: len(b) for cid, b in inflight.items()}
    return fleet, summary


def _bag_entry(ev: dict, cid: str, now: datetime, pred_delivered) -> dict:
    """orders_state-kształtny wpis bagu (konsumowany przez _bag_dict_to_ordersim)."""
    return {
        "order_id": ev["order_id"],
        "status": "assigned",
        "pickup_coords": ev.get("pickup_coords"),
        "delivery_coords": ev.get("delivery_coords"),
        "pickup_at_warsaw": ev.get("pickup_at_warsaw"),
        "czas_kuriera_warsaw": ev.get("czas_kuriera_warsaw"),
        "assigned_at": now.isoformat(),
        "courier_id": cid,
        "_pred_delivered": pred_delivered.isoformat() if pred_delivered else None,
    }


# ─── 4. przebieg ─────────────────────────────────────────────────────

def _assess(ev, fleet, now):
    """1 wywołanie pipeline. Zwraca (verdict, best_cid, best_name, score,
    best_effort, auto_route, pool_feasible, pool_total, sla_violations, plan)."""
    res = DP.assess_order(ev, fleet, None, now)
    best = res.best
    plan = getattr(best, "plan", None) if best else None
    return {
        "order_id": ev["order_id"],
        "now": now.isoformat(),
        "created_at": ev.get("_created_at"),
        "restaurant": (ev.get("restaurant") or "").replace("&#039;", "'"),
        "delivery": ev.get("delivery_address"),
        "verdict": res.verdict,
        "reason": res.reason,
        "best_cid": getattr(best, "courier_id", None) if best else None,
        "best_name": getattr(best, "name", None) if best else None,
        "score": round(getattr(best, "score", 0.0), 1) if best else None,
        "best_effort": bool(getattr(best, "best_effort", False)) if best else False,
        "auto_route": res.auto_route,
        "pool_feasible": res.pool_feasible_count,
        "pool_total": res.pool_total_count,
        "sla_violations": getattr(plan, "sla_violations", None) if plan else None,
        "_plan": plan,
    }


def run_sequential(orders: list, fleet: dict) -> list:
    """Zlecenia po kolei; każdy PROPOSE commitowany do bagu zwycięzcy.
    Przed każdym krokiem wygasają z bagów ordery z _pred_delivered < now."""
    out = []
    for ev in orders:
        now = _dt(ev["_created_at"])
        # wygaszanie dostarczonych
        for cs in fleet.values():
            cs.bag = [
                b for b in cs.bag
                if not b.get("_pred_delivered") or _dt(b["_pred_delivered"]) > now
            ]
        rec = _assess(ev, fleet, now)
        rec["bag_after"] = {c: len(cs.bag) for c, cs in fleet.items() if cs.bag}
        # commit
        if rec["verdict"] == "PROPOSE" and rec["best_cid"]:
            plan = rec["_plan"]
            pred = None
            if plan and getattr(plan, "predicted_delivered_at", None):
                pred = plan.predicted_delivered_at.get(ev["order_id"])
            if pred is None:
                pred = now + timedelta(minutes=45)
            cs = fleet.get(rec["best_cid"])
            if cs is not None:
                cs.bag.append(_bag_entry(ev, rec["best_cid"], now, pred))
        rec.pop("_plan", None)
        out.append(rec)
    return out


def run_naive(orders: list, roster: set, date: str) -> list:
    """Każde zlecenie oceniane wobec ŚWIEŻEJ pustej floty (bez commitu) —
    replikuje shadow-log „opinię per order" dla kontrastu."""
    out = []
    for ev in orders:
        now = _dt(ev["_created_at"])
        fleet = build_cold_fleet(roster, date)
        rec = _assess(ev, fleet, now)
        rec.pop("_plan", None)
        out.append(rec)
    return out


# ─── 4b. rolling re-optymalizacja (late binding + reassignment) ──────

FREEZE_LEAD_MIN = 15  # zlecenie zamrażane FREEZE_LEAD_MIN przed odbiorem


def _pickup_ready(ev) -> datetime:
    """pickup_at_warsaw → UTC-aware; fallback created+40min."""
    raw = ev.get("pickup_at_warsaw") or ev.get("pickup_at")
    d = _dt(raw) if raw else None
    return d if d is not None else _dt(ev["_created_at"]) + timedelta(minutes=40)


def _strip_order(fleet: dict, oid: str) -> None:
    for cs in fleet.values():
        cs.bag = [b for b in cs.bag if b.get("order_id") != oid]


def _reassess(fleet: dict, ev: dict, now: datetime) -> dict:
    """Zdejmij zlecenie z bagów, oceń od nowa, commituj do zwycięzcy. Zwraca rec."""
    _strip_order(fleet, ev["order_id"])
    rec = _assess(ev, fleet, now)
    if rec["verdict"] == "PROPOSE" and rec["best_cid"]:
        plan = rec.get("_plan")
        pred = None
        if plan and getattr(plan, "predicted_delivered_at", None):
            pred = plan.predicted_delivered_at.get(ev["order_id"])
        if pred is None:
            pred = now + timedelta(minutes=45)
        cs = fleet.get(rec["best_cid"])
        if cs is not None:
            cs.bag.append(_bag_entry(ev, rec["best_cid"], now, pred))
    rec.pop("_plan", None)
    return rec


def run_rolling(orders: list, fleet: dict) -> tuple:
    """Rolling re-optymalizacja — trigger per nowe zlecenie.

    Zlecenie jest modyfikowalne (może zmienić kuriera) aż do `odbiór − 15 min`,
    potem ZAMROŻONE. Mechanizm:
      • nowe zlecenie → ocena + tentatywny przydział
      • lokalny re-opt: niezamrożone zlecenia kuriera, który właśnie dostał nowe,
        są przeliczane — mogą przeskoczyć do lżejszego kuriera
      • zlecenie krzyżujące `odbiór − 15 min` → finalna ocena → zamrożone
      • finalna decyzja per zlecenie = ta z momentu zamrożenia (widzi pełny
        bieżący stan floty — to jest „late binding")

    Zwraca (final_records, churn) — churn[oid] = ile razy zlecenie zmieniło
    kuriera zanim się zamroziło.
    """
    orders = sorted(orders, key=lambda e: e["_created_at"])
    ev_by = {e["order_id"]: e for e in orders}
    freeze_at = {
        e["order_id"]: max(_dt(e["_created_at"]),
                            _pickup_ready(e) - timedelta(minutes=FREEZE_LEAD_MIN))
        for e in orders
    }
    tentative = {}      # oid -> cid (zlecenia live, nie-zamrożone)
    final = {}          # oid -> rec (decyzja z zamrożenia)
    churn = defaultdict(int)

    def expire(now):
        for cs in fleet.values():
            cs.bag = [b for b in cs.bag
                      if not b.get("_pred_delivered") or _dt(b["_pred_delivered"]) > now]

    def do_freeze(now):
        due = sorted((o for o in list(tentative) if freeze_at[o] <= now),
                     key=lambda o: freeze_at[o])
        for oid in due:
            prev = tentative.pop(oid)
            rec = _reassess(fleet, ev_by[oid], now)
            if rec["best_cid"] and rec["best_cid"] != prev:
                churn[oid] += 1
            rec["frozen_at"] = now.isoformat()
            final[oid] = rec

    for ev in orders:
        now = _dt(ev["_created_at"])
        oid = ev["order_id"]
        expire(now)
        do_freeze(now)
        rec = _reassess(fleet, ev, now)         # nowe zlecenie — tentatywnie
        tentative[oid] = rec["best_cid"]
        # lokalny re-opt: niezamrożone zlecenia kuriera obciążonego nowym
        touched = rec["best_cid"]
        if touched:
            reopt = sorted((o for o, c in tentative.items()
                            if o != oid and c == touched),
                           key=lambda x: freeze_at[x])
            for o in reopt:
                prev = tentative.get(o)
                r2 = _reassess(fleet, ev_by[o], now)
                if r2["best_cid"] and r2["best_cid"] != prev:
                    churn[o] += 1
                tentative[o] = r2["best_cid"]

    # flush — zlecenia które nie zdążyły zamrozić w pętli
    last = _dt(orders[-1]["_created_at"]) if orders else None
    for oid in list(tentative):
        now = max(freeze_at[oid], last) if last else freeze_at[oid]
        prev = tentative.pop(oid)
        rec = _reassess(fleet, ev_by[oid], now)
        if rec["best_cid"] and rec["best_cid"] != prev:
            churn[oid] += 1
        rec["frozen_at"] = now.isoformat()
        final[oid] = rec

    return [final[e["order_id"]] for e in orders if e["order_id"] in final], dict(churn)


# ─── 5. raport ───────────────────────────────────────────────────────

def _load_distribution(recs: list) -> Counter:
    c = Counter()
    for r in recs:
        if r["verdict"] == "PROPOSE" and r["best_cid"]:
            label = f"{r['best_name'] or '?'} ({r['best_cid']})"
            c[label] += 1
    return c


def summarize(recs: list, fleet: dict, label: str) -> dict:
    n = len(recs)
    verdicts = Counter(r["verdict"] for r in recs)
    proposes = [r for r in recs if r["verdict"] == "PROPOSE"]
    be = sum(1 for r in proposes if r["best_effort"])
    no_feasible = sum(1 for r in proposes if (r["pool_feasible"] or 0) == 0)
    sla_breaches = sum((r["sla_violations"] or 0) for r in proposes)
    auto = Counter(r["auto_route"] for r in proposes)
    dist = _load_distribution(recs)
    peak_bag = {}
    if fleet:
        for r in recs:
            for c, sz in (r.get("bag_after") or {}).items():
                peak_bag[c] = max(peak_bag.get(c, 0), sz)
    return {
        "label": label,
        "n_orders": n,
        "verdicts": dict(verdicts),
        "propose": len(proposes),
        "best_effort": be,
        "propose_0_feasible": no_feasible,
        "sla_breaches_in_plans": sla_breaches,
        "auto_route": dict(auto),
        "distribution": dist.most_common(),
        "peak_bag": peak_bag,
    }


def _block(s):
    print(f"\n  zleceń: {s['n_orders']}   verdykty: {s['verdicts']}")
    print(f"  PROPOSE: {s['propose']}  |  best_effort: {s['best_effort']}  |  "
          f"0 kurierów feasible: {s['propose_0_feasible']}")
    print(f"  auto_route: {s['auto_route']}")
    print(f"  SLA-naruszeń w planach zwycięzców: {s['sla_breaches_in_plans']}")


def _dist(s, with_peak=False):
    for name, cnt in s["distribution"]:
        pk = ""
        if with_peak:
            for c, sz in s["peak_bag"].items():
                if f"({c})" in name:
                    pk = f"  [peak bag: {sz}]"
        print(f"    {name:26} {cnt:3}  {'█' * cnt}{pk}")


def print_report(naive_sum, cold_sum, warm_sum, inflight_summary):
    print("=" * 72)
    print("SEKWENCYJNY HARNESS — Ziomek sam prowadzi flotę")
    print("=" * 72)

    print("\n### TRYB NAIVE (każde zlecenie w izolacji — jak shadow log)")
    _block(naive_sum)
    print("\n  rozkład obciążenia:")
    _dist(naive_sum)

    print("\n### TRYB SEKWENCYJNY COLD-START (wszyscy wolni o starcie)")
    _block(cold_sum)
    print("\n  rozkład obciążenia:")
    _dist(cold_sum, with_peak=True)

    n_if = sum(inflight_summary.values())
    print(f"\n### TRYB SEKWENCYJNY WARM-START "
          f"({n_if} zleceń w locie u {len(inflight_summary)} kurierów o starcie)")
    print(f"  bagi startowe: {inflight_summary}")
    _block(warm_sum)
    print("\n  rozkład obciążenia:")
    _dist(warm_sum, with_peak=True)

    print("\n### RÓŻNICA COLD → WARM")
    for s, lbl in ((naive_sum, "naive "), (cold_sum, "cold  "), (warm_sum, "warm  ")):
        top = s["distribution"][0] if s["distribution"] else ("-", 0)
        print(f"  {lbl}: kurierów użytych {len(s['distribution']):2}  |  "
              f"top {top[1]:2}  |  best_effort {s['best_effort']:2}  |  "
              f"SLA-breach {s['sla_breaches_in_plans']:2}  |  "
              f"ALERT {s['auto_route'].get('ALERT', 0):2}")
    print("=" * 72)


def _per_hour(recs: list) -> list:
    """Rozbicie best_effort per godzina Warsaw. Zwraca [(hh, n, prop, be, pct)]."""
    h = defaultdict(lambda: {"n": 0, "prop": 0, "be": 0})
    for r in recs:
        war = int((r.get("created_at") or r["now"])[11:13]) + 2
        b = h[war]
        b["n"] += 1
        if r["verdict"] == "PROPOSE":
            b["prop"] += 1
            if r["best_effort"]:
                b["be"] += 1
    out = []
    for war in sorted(h):
        b = h[war]
        pct = 100 * b["be"] // max(1, b["prop"])
        out.append((war, b["n"], b["prop"], b["be"], pct))
    return out


def print_rolling_report(base_sum, roll_sum, churn, roll_recs, base_recs):
    print("=" * 72)
    print("ROLLING RE-OPTYMALIZACJA — late binding + reassignment do odbiór−15min")
    print("=" * 72)

    print("\n### BASELINE — one-shot (commit przy utworzeniu, bez zmian)")
    _block(base_sum)
    print("\n### ROLLING — zlecenie modyfikowalne do odbiór−15min")
    _block(roll_sum)

    print("\n### best_effort per godzina (Warsaw) — baseline → rolling")
    base_h = {h: (be, pct) for h, _, _, be, pct in _per_hour(base_recs)}
    for h, n, prop, be, pct in _per_hour(roll_recs):
        b_be, b_pct = base_h.get(h, (0, 0))
        bar = "█" * (pct // 5)
        print(f"  {h:02d}:00  zleceń {n:3}  |  baseline {b_be:3} ({b_pct:3}%)"
              f"  →  rolling {be:3} ({pct:3}%) {bar}")

    print("\n### CHURN (ile razy zlecenie zmieniło kuriera przed zamrożeniem)")
    chist = Counter()
    for r in roll_recs:
        chist[min(churn.get(r["order_id"], 0), 3)] += 1
    total = len(roll_recs)
    moves = sum(churn.values())
    for k in (0, 1, 2, 3):
        lbl = "3+" if k == 3 else str(k)
        c = chist.get(k, 0)
        print(f"  {lbl}× przeskok: {c:3}  ({100 * c // max(1, total)}%)")
    print(f"  łącznie przeskoków: {moves}  |  zleceń ruszonych ≥1×: "
          f"{total - chist.get(0, 0)}")

    print("\n### RÓŻNICA")
    for s, lbl in ((base_sum, "baseline"), (roll_sum, "rolling ")):
        print(f"  {lbl}: best_effort {s['best_effort']:3}  |  "
              f"SLA-breach {s['sla_breaches_in_plans']:3}  |  "
              f"ALERT {s['auto_route'].get('ALERT', 0):3}  |  "
              f"kurierów {len(s['distribution']):2}")
    db = base_sum["best_effort"] - roll_sum["best_effort"]
    print(f"  → rolling zbija best_effort o {db} zleceń "
          f"({100 * db // max(1, base_sum['best_effort'])}%)")
    print("=" * 72)


# ──────────────────────────────────────────────────────────────────────
# diff / fleet‑level comparison helpers
# ──────────────────────────────────────────────────────────────────────

def _gini(values: list) -> float:
    """Współczynnik Giniego dla listy liczb (countów)."""
    n = len(values)
    if n <= 1:
        return 0.0
    total = sum(values)
    if total == 0:
        return 0.0
    sorted_vals = sorted(values)
    weighted = sum((i + 1) * v for i, v in enumerate(sorted_vals))
    gini = (2.0 * weighted / (n * total)) - (n + 1) / n
    return gini


def _fleet_metrics(summary: dict) -> dict:
    """Podstawowe metryki floty z pojedynczego *summary*."""
    sla = int(summary.get("sla_breaches_in_plans", 0))
    be = int(summary.get("best_effort", 0))
    zf = int(summary.get("propose_0_feasible", 0))
    alerts = int((summary.get("auto_route") or {}).get("ALERT", 0))
    counts = [int(c) for _, c in (summary.get("distribution") or [])]
    couriers_used = len(counts)
    max_pile = max(counts) if counts else 0
    mean_load = sum(counts) / couriers_used if couriers_used else 0.0
    pile_ratio = max_pile / mean_load if mean_load > 0 else 0.0
    gini = _gini(counts)
    peek = max(summary.get("peak_bag", {}).values()) if summary.get("peak_bag") else 0
    return {
        "sla_breaches": sla,
        "best_effort": be,
        "zero_feasible": zf,
        "alerts": alerts,
        "couriers_used": couriers_used,
        "max_pile": max_pile,
        "pile_ratio": pile_ratio,
        "gini": gini,
        "peak_bag_max": peek,
    }


def _pick_fleet_summary(report: dict) -> tuple:
    """Wybiera fleet summary z raportu wg priorytetu.

    Zwraca (summary_dict, uzyty_klucz).
    """
    candidates = [
        "summary_warm",
        "summary_cold",
        "summary_baseline",
        "summary_rolling",
        "summary_naive",
    ]
    for key in candidates:
        if key in report and report[key] is not None:
            return report[key], key
    raise ValueError(
        f"Brak fleet summary (sprawdzono: {', '.join(candidates)}) w raporcie."
    )


def _metrics_delta(base: dict, cand: dict) -> dict:
    """Różnica cand – base dla każdej metryki (pod kluczami obu)."""
    keys = [
        "sla_breaches",
        "best_effort",
        "zero_feasible",
        "alerts",
        "couriers_used",
        "max_pile",
        "pile_ratio",
        "gini",
        "peak_bag_max",
    ]
    delta = {}
    for k in keys:
        delta[k] = cand.get(k, 0) - base.get(k, 0)
    return delta


def _determine_verdict(
    base: dict,
    cand: dict,
    delta: dict,
    target: str,
    gini_tol: float,
    pile_tol: float,
) -> tuple:
    """Określa werdykt GO / NO‑GO i listę zablokowanych metryk.

    Zwraca (verdict, blocked_by).
    """
    blocked = []
    # regresje
    if delta["sla_breaches"] > 0:
        blocked.append("sla_breaches")
    if delta["best_effort"] > 0:
        blocked.append("best_effort")
    if delta["zero_feasible"] > 0:
        blocked.append("zero_feasible")
    if delta["alerts"] > 0:
        blocked.append("alerts")
    if delta["gini"] > gini_tol:
        blocked.append("gini")
    if delta["pile_ratio"] > pile_tol:
        blocked.append("pile_ratio")

    if blocked:
        return "NO-GO", blocked

    # target improvement
    if target not in cand:
        raise ValueError(f"Nieznany target '{target}'")
    target_improved = base[target] - cand[target] > 0
    if target_improved:
        return "GO", []
    return "NO-GO", ["target_not_improved"]


def run_diff(
    base_path: str,
    cand_path: str,
    target: str,
    gini_tol: float,
    pile_tol: float,
) -> dict:
    """Wczytuje dwa raporty, oblicza metryki floty i wystawia werdykt.

    Zwraca słownik gotowy do JSON/druku.
    """
    with open(base_path, encoding="utf-8") as fb:
        base_data = json.load(fb)
    with open(cand_path, encoding="utf-8") as fc:
        cand_data = json.load(fc)

    base_sum, base_label = _pick_fleet_summary(base_data)
    cand_sum, cand_label = _pick_fleet_summary(cand_data)

    base_m = _fleet_metrics(base_sum)
    cand_m = _fleet_metrics(cand_sum)

    delta = _metrics_delta(base_m, cand_m)
    verdict, blocked_by = _determine_verdict(
        base_m, cand_m, delta, target, gini_tol, pile_tol
    )

    return {
        "base_label": base_label,
        "cand_label": cand_label,
        "base": base_m,
        "cand": cand_m,
        "delta": delta,
        "verdict": verdict,
        "blocked_by": blocked_by,
        "target": target,
    }


def print_diff(diffres: dict) -> None:
    """Wypisuje czytelne porównanie fleet‑level."""
    print(
        "Porównanie FLEET-LEVEL z sekwencyjnego replayu (cascade-aware)"
        f"\n  base ({diffres['base_label']}) vs cand ({diffres['cand_label']})\n"
    )
    metrics_order = [
        "sla_breaches",
        "best_effort",
        "zero_feasible",
        "alerts",
        "couriers_used",
        "max_pile",
        "pile_ratio",
        "gini",
        "peak_bag_max",
    ]
    headers = f"{'metryka':20} {'BASE':>8} {'CAND':>8} {'DELTA':>8}"
    print(headers)
    print("-" * len(headers))
    for m in metrics_order:
        b = diffres["base"].get(m, 0)
        c = diffres["cand"].get(m, 0)
        d = diffres["delta"].get(m, 0)
        if isinstance(b, int) and isinstance(c, int) and isinstance(d, float):
            # d to delta jako float – wyświetlamy z 3 miejscami
            print(f"{m:20} {b:8} {c:8} {d:8.3f}")
        elif isinstance(b, float) or isinstance(c, float):
            # metryki zmiennoprzecinkowe
            print(f"{m:20} {b:8.3f} {c:8.3f} {d:8.3f}")
        else:
            # int
            print(f"{m:20} {b:8} {c:8} {d:8}")

    verdict = diffres["verdict"]
    blocked = diffres["blocked_by"]
    line = f"WERDYKT: {verdict}"
    if blocked:
        line += f" (zablokowane przez: {', '.join(blocked)})"
    print("\n" + line)


# ──────────────────────────────────────────────────────────────────────


def main():
    ap = argparse.ArgumentParser(description="sekwencyjny replay Ziomka")
    ap.add_argument("--date", default="2026-05-17")
    ap.add_argument("--from", dest="hf", type=int, default=12, help="godzina UTC od")
    ap.add_argument("--to", dest="ht", type=int, default=13, help="godzina UTC do (wykl.)")
    ap.add_argument("--out", default=None, help="ścieżka raportu JSON")
    ap.add_argument("--rolling", action="store_true",
                    help="tryb rolling re-opt (baseline one-shot vs rolling late-binding)")
    # ── diff / fleet‑level comparison arguments ──
    ap.add_argument("--diff-base", default=None, metavar="PATH",
                    help="ścieżka do raportu JSON z przebiegu baseline")
    ap.add_argument("--diff-cand", default=None, metavar="PATH",
                    help="ścieżka do raportu JSON z przebiegu kandydata")
    ap.add_argument("--target", default="sla_breaches",
                    help="metryka docelowa fleet: sla_breaches|best_effort|gini|"
                         "pile_ratio|alerts|zero_feasible (domyślnie: sla_breaches)")
    ap.add_argument("--gini-tol", type=float, default=0.02,
                    help="tolerancja dla wzrostu Giniego (domyślnie 0.02)")
    ap.add_argument("--pile-tol", type=float, default=0.10,
                    help="tolerancja dla wzrostu pile_ratio (domyślnie 0.10)")

    args = ap.parse_args()

    # ---- diff mode ----
    if args.diff_base is not None and args.diff_cand is not None:
        result = run_diff(
            args.diff_base,
            args.diff_cand,
            args.target,
            args.gini_tol,
            args.pile_tol,
        )
        print_diff(result)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=1, default=str)
            print(f"\nraport JSON → {args.out}")
        return

    # ---- original replay paths unchanged ----
    orders = load_orders(args.date, args.hf, args.ht)
    roster = build_roster(args.date, args.hf, args.ht)
    print(f"okno: {args.date} {args.hf:02d}:00-{args.ht:02d}:00 UTC  |  "
          f"zleceń: {len(orders)}  |  flota (roster): {len(roster)} kurierów")
    if not orders or not roster:
        print("BRAK danych — przerwanie.")
        return

    if args.rolling:
        base_fleet = build_cold_fleet(roster, args.date)
        base_recs = run_sequential(orders, base_fleet)
        roll_fleet = build_cold_fleet(roster, args.date)
        roll_recs, churn = run_rolling(orders, roll_fleet)
        base_sum = summarize(base_recs, base_fleet, "baseline-oneshot")
        roll_sum = summarize(roll_recs, roll_fleet, "rolling")
        print_rolling_report(base_sum, roll_sum, churn, roll_recs, base_recs)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump({
                    "window": {"date": args.date, "from": args.hf, "to": args.ht},
                    "roster": sorted(roster),
                    "freeze_lead_min": FREEZE_LEAD_MIN,
                    "summary_baseline": base_sum,
                    "summary_rolling": roll_sum,
                    "churn": churn,
                    "decisions_rolling": roll_recs,
                    "decisions_baseline": base_recs,
                }, f, ensure_ascii=False, indent=1, default=str)
            print(f"\nraport JSON → {args.out}")
        return

    naive_recs = run_naive(orders, roster, args.date)

    cold_fleet = build_cold_fleet(roster, args.date)
    cold_recs = run_sequential(orders, cold_fleet)

    warm_fleet, inflight_summary = build_warm_fleet(roster, args.date, args.hf)
    warm_recs = run_sequential(orders, warm_fleet)

    naive_sum = summarize(naive_recs, {}, "naive")
    cold_sum = summarize(cold_recs, cold_fleet, "cold")
    warm_sum = summarize(warm_recs, warm_fleet, "warm")
    print_report(naive_sum, cold_sum, warm_sum, inflight_summary)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({
                "window": {"date": args.date, "from": args.hf, "to": args.ht},
                "roster": sorted(roster),
                "inflight_warm_start": inflight_summary,
                "summary_naive": naive_sum,
                "summary_cold": cold_sum,
                "summary_warm": warm_sum,
                "decisions_warm": warm_recs,
                "decisions_cold": cold_recs,
                "decisions_naive": naive_recs,
            }, f, ensure_ascii=False, indent=1, default=str)
        print(f"\nraport JSON → {args.out}")


if __name__ == "__main__":
    main()
