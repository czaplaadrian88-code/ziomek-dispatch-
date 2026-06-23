#!/usr/bin/env python3
"""Ziomek prediction calibration — rozjazd PRZEWIDYWANY vs RZECZYWISTY czas (READ-ONLY).

Cel (Adrian 2026-06-23): zmierzyć GDZIE Ziomek się myli w czasach, żeby skalibrować
„dobre czasy" pokazywane w konsoli/apce. Per zlecenie liczymy DWA rozjazdy:
  • ODBIÓR  : kiedy kurier REALNIE odebrał  − przewidywany odbiór (predicted_at pickupa)
  • DOSTAWA : kiedy kurier REALNIE doręczył − przewidywana dostawa (predicted_at dropoffa)
Każdy w DWÓCH kotwicach (decyzja Adriana — „oba"):
  • assign : pierwsza predykcja po przypisaniu (= OBIETNICA Ziomka)
  • last   : ostatnia predykcja przed zdarzeniem (= ŻYWE ETA, to co realnie widać w konsoli)
Segmentowane SOLO vs BUNDLE (rozmiar worka kuriera, gdy zlecenie było aktywne).

Działa jak `ziomek_time_route_monitor`: oneshot per tick (świeży proces), stan w pliku
(bo predicted_at zmienia się przy replanach — łapiemy first+last). NIC nie mutuje
(orders_state/plany/gastro nietknięte) — pisze tylko własny shadow JSONL + state.

Tryby:
  (bez argr.)   — jeden tick: snapshot aktywnych + domknięcie zakończonych → JSONL
  --summary     — agregaty rozjazdów (mediana/śr/p90) per odbiór|dostawa × assign|last × solo|bundle
                  + SUGEROWANA korekta (mediana) do kalibracji „dobrych czasów"

Strefy (PUŁAPKA, patrz [[console-app-time-route-divergence-2026-06-23]]):
  • plan predicted_at         = ISO UTC (+00:00)
  • orders_state picked_up_at / delivered_at = NAIWNY czas LOKALNY Warszawy
  • czas_kuriera_warsaw       = tz-aware Warsaw
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
STATE_DIR = Path("/root/.openclaw/workspace/dispatch_state")
ORDERS_PATH = STATE_DIR / "orders_state.json"
PLANS_PATH = STATE_DIR / "courier_plans.json"
STATE_PATH = STATE_DIR / "ziomek_pred_calibration_state.json"
OUT_PATH = STATE_DIR / "ziomek_pred_calibration.jsonl"

ACTIVE = {"assigned", "picked_up", "en_route"}


# --------------------------------------------------------------------------- tz utils
def _utc(iso) -> datetime | None:
    """predicted_at / pola z offsetem → aware UTC. None gdy się nie da."""
    if not iso or not isinstance(iso, str):
        return None
    try:
        d = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except Exception:
        return None


def _naive_warsaw(s) -> datetime | None:
    """picked_up_at / delivered_at = naiwny czas LOKALNY Warszawy → aware UTC."""
    if not s or not isinstance(s, str):
        return None
    try:
        d = datetime.fromisoformat(s.strip())
        return (d.replace(tzinfo=WARSAW) if d.tzinfo is None else d).astimezone(timezone.utc)
    except Exception:
        return None


def _load(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _atomic_write(path: Path, data) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _orders_dict(raw) -> dict:
    if isinstance(raw, dict):
        inner = raw.get("orders", raw)
        return inner if isinstance(inner, dict) else {}
    return {}


def _plan_preds(plan_doc) -> dict:
    """{oid: {'pickup': predicted_at_iso|None, 'dropoff': predicted_at_iso|None}} z planu kuriera."""
    out: dict = {}
    if not isinstance(plan_doc, dict):
        return out
    for s in (plan_doc.get("stops") or []):
        if not isinstance(s, dict):
            continue
        oid = str(s.get("order_id"))
        typ = "pickup" if s.get("type") == "pickup" else "dropoff"
        out.setdefault(oid, {})
        # pierwszy stop danego typu dla oid wygrywa (kolejność planu)
        out[oid].setdefault(typ, s.get("predicted_at"))
    return out


def _min_between(actual_utc: datetime | None, pred_iso) -> float | None:
    """rozjazd w minutach: REAL − PRZEWIDYWANY (dodatnie = później niż Ziomek przewidział)."""
    p = _utc(pred_iso)
    if actual_utc is None or p is None:
        return None
    return round((actual_utc - p).total_seconds() / 60.0, 1)


# --------------------------------------------------------------------------- tick
def run_tick() -> int:
    now = datetime.now(timezone.utc)
    orders = _orders_dict(_load(ORDERS_PATH))
    plans = _load(PLANS_PATH) or {}
    state = _load(STATE_PATH) or {}
    tracked: dict = state.get("tracked", {})

    # rozmiar worka per kurier (do solo/bundle)
    bag_size: dict = {}
    for o in orders.values():
        if isinstance(o, dict) and o.get("status") in ACTIVE and o.get("courier_id") is not None:
            bag_size[str(o["courier_id"])] = bag_size.get(str(o["courier_id"]), 0) + 1

    closed = []
    # 1) snapshot aktywnych (assign = pierwsze widzenie, last = każde kolejne)
    for oid, o in orders.items():
        if not isinstance(o, dict):
            continue
        oid = str(oid)
        status = o.get("status")
        cid = o.get("courier_id")
        if status in ACTIVE and cid is not None:
            preds = _plan_preds(plans.get(str(cid)) if isinstance(plans, dict) else None).get(oid, {})
            snap = {
                "pickup_pred": preds.get("pickup"),
                "delivery_pred": preds.get("dropoff"),
                "bag_size": bag_size.get(str(cid), 1),
                "cid": str(cid),
                "at": now.isoformat(),
            }
            t = tracked.get(oid)
            if t is None:
                tracked[oid] = {
                    "assign": snap, "last": snap,
                    "max_bag": snap["bag_size"],
                    "restaurant": o.get("restaurant"),
                    "delivery_address": o.get("delivery_address"),
                    "order_type": o.get("order_type"),
                    "czas_kuriera_hhmm": o.get("czas_kuriera_hhmm"),
                }
            else:
                # zachowaj assign; aktualizuj last; tylko gdy plan COŚ podaje (nie kasuj predykcji nullem)
                if snap["pickup_pred"] or snap["delivery_pred"]:
                    t["last"] = snap
                t["max_bag"] = max(t.get("max_bag", 1), snap["bag_size"])
                t["czas_kuriera_hhmm"] = o.get("czas_kuriera_hhmm")

    # 2) domknięcie: śledzone zlecenia, które są już delivered (rozjazd → JSONL, usuń ze stanu)
    for oid in list(tracked.keys()):
        o = orders.get(oid)
        if not isinstance(o, dict):
            # zniknęło z orders_state (prune) — przestań śledzić bez rekordu
            tracked.pop(oid, None)
            continue
        if o.get("status") != "delivered":
            continue
        pk = _naive_warsaw(o.get("picked_up_at"))
        dl = _naive_warsaw(o.get("delivered_at"))
        t = tracked.pop(oid)
        a, l = t.get("assign", {}), t.get("last", {})
        rec = {
            "oid": oid,
            "logged_at": now.astimezone(WARSAW).isoformat(),
            "cid": (l or a).get("cid"),
            "restaurant": t.get("restaurant"),
            "delivery_address": t.get("delivery_address"),
            "order_type": t.get("order_type"),
            "czas_kuriera_hhmm": t.get("czas_kuriera_hhmm"),
            "max_bag": t.get("max_bag", 1),
            "klasa": "solo" if (t.get("max_bag", 1) or 1) <= 1 else "bundle",
            # rzeczywiste (Warsaw HH:MM dla czytelności + ISO)
            "picked_up_at": o.get("picked_up_at"),
            "delivered_at": o.get("delivered_at"),
            # ── ROZJAZDY (real − predicted, minuty; + = później niż Ziomek przewidział) ──
            "pickup_pred_assign": a.get("pickup_pred"),
            "pickup_pred_last": l.get("pickup_pred"),
            "delivery_pred_assign": a.get("delivery_pred"),
            "delivery_pred_last": l.get("delivery_pred"),
            "rozjazd_odbior_assign": _min_between(pk, a.get("pickup_pred")),
            "rozjazd_odbior_last": _min_between(pk, l.get("pickup_pred")),
            "rozjazd_dostawa_assign": _min_between(dl, a.get("delivery_pred")),
            "rozjazd_dostawa_last": _min_between(dl, l.get("delivery_pred")),
        }
        closed.append(rec)
        try:
            with open(OUT_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except OSError:
            pass

    _atomic_write(STATE_PATH, {"tracked": tracked, "updated_at": now.isoformat()})
    print(f"[{now.isoformat()}] śledzonych={len(tracked)} domkniętych_teraz={len(closed)} "
          f"(zapis {OUT_PATH.name})")
    for r in closed:
        print(f"  #{r['oid']} {r['klasa']:<6} odbiór rozjazd assign={r['rozjazd_odbior_assign']}"
              f"/last={r['rozjazd_odbior_last']}  dostawa assign={r['rozjazd_dostawa_assign']}"
              f"/last={r['rozjazd_dostawa_last']}")
    return 0


# --------------------------------------------------------------------------- summary
def _stats(xs: list[float]) -> dict:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return {"n": 0}
    n = len(xs)
    med = xs[n // 2] if n % 2 else round((xs[n // 2 - 1] + xs[n // 2]) / 2, 1)
    p90 = xs[min(n - 1, int(0.9 * (n - 1)))]
    return {"n": n, "mediana": med, "śr": round(sum(xs) / n, 1), "p90": p90,
            "min": xs[0], "max": xs[-1]}


def run_summary() -> int:
    recs = []
    if OUT_PATH.exists():
        for line in OUT_PATH.read_text(encoding="utf-8").splitlines():
            try:
                recs.append(json.loads(line))
            except Exception:
                pass
    print(f"=== KALIBRACJA rozjazdów Ziomka (rekordów: {len(recs)}) — {OUT_PATH.name} ===")
    if not recs:
        print("  (brak rekordów — logger zbiera od momentu uruchomienia timera; potrzebuje "
              "domknąć zlecenia po dostawie)")
        return 0
    print("  rozjazd = REAL − PRZEWIDYWANY (min); + = później niż Ziomek przewidział\n")
    for event, fa, fl in (("ODBIÓR", "rozjazd_odbior_assign", "rozjazd_odbior_last"),
                          ("DOSTAWA", "rozjazd_dostawa_assign", "rozjazd_dostawa_last")):
        print(f"── {event} ──")
        for klasa in ("solo", "bundle"):
            sub = [r for r in recs if r.get("klasa") == klasa]
            sa = _stats([r.get(fa) for r in sub])
            sl = _stats([r.get(fl) for r in sub])
            print(f"  {klasa:<6} assign: {sa}")
            print(f"  {klasa:<6} last  : {sl}")
            if sl.get("n"):
                print(f"         → sugerowana korekta '{event.lower()} {klasa}' (mediana last): "
                      f"{sl['mediana']:+} min do predykcji")
        print()
    return 0


def main() -> int:
    if "--summary" in sys.argv:
        return run_summary()
    return run_tick()


if __name__ == "__main__":
    raise SystemExit(main())
