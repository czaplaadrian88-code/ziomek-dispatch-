#!/usr/bin/env python3
"""eta_calibration_logger — pętla uczenia Ziomka: predykcja ETA vs rzeczywistość.

Sprint 1 (2026-05-17). Adrian: "Ziomek daje za krótkie czasy" — żeby je
skalibrować i żeby Ziomek się uczył, musi najpierw WIDZIEĆ swój błąd.

Co robi: joinuje dwa logi i dopisuje wynik do dedykowanego logu kalibracyjnego.
  - shadow_decisions.jsonl  — decyzje dispatchu; `best.plan.per_order_delivery_times[oid]`
                              to predykcja Ziomka (ile minut zajmie dostawa).
  - sla_log.jsonl           — `delivery_time_minutes` to REALNY czas pickup→delivery.

Dla każdego dostarczonego zlecenia liczy `error_min = real - predicted`
(dodatni = Ziomek niedoszacował) i zapisuje wiersz do eta_calibration_log.jsonl
wraz z kontekstem (rozmiar baga, godzina, restauracja, strategia TSP, ...).

WAŻNE — to NIE jest hot-path. Osobny proces uruchamiany z timera, tylko CZYTA
logi produkcyjne i pisze własny plik. Zero ryzyka dla dispatchu.

Idempotentny: pamięta które oid-y już zapisał (czyta istniejący log na starcie),
więc kolejne uruchomienia dopisują tylko nowe. Pierwsze uruchomienie backfilluje
całą historię z sla_log.

Anchor (uwaga dla analizy w Sprincie 2/4): `per_order_delivery_times` liczone od
`pickup_ready_at`, `delivery_time_minutes` od faktycznego `picked_up_at` — lekko
różne kotwice. Logujemy oba pola surowe + picked_up_at, żeby warstwa analizy
mogła je przeliczyć. Logger jest tylko WIERNYM rejestratorem, nie interpretuje.

Uruchomienie:
    /root/.openclaw/venvs/dispatch/bin/python eta_calibration_logger.py
"""
import json
import os
import sys
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")

BASE = "/root/.openclaw/workspace"
SLA_LOG = f"{BASE}/scripts/logs/sla_log.jsonl"
SHADOW_LOG = f"{BASE}/scripts/logs/shadow_decisions.jsonl"
OUT_LOG = f"{BASE}/dispatch_state/eta_calibration_log.jsonl"

# Godziny szczytu (Warsaw) — peak window dispatchu Białystok. Wartość pomocnicza;
# źródłem prawdy dla analizy jest surowe hour_warsaw + weekday w wierszu.
PEAK_HOURS = frozenset({11, 12, 13, 17, 18, 19})
SHOULDER_HOURS = frozenset({10, 14, 15, 16, 20})


def _bucket(hour):
    if hour in PEAK_HOURS:
        return "peak"
    if hour in SHOULDER_HOURS:
        return "shoulder"
    return "offpeak"


def _parse_dt(s):
    """Parsuje datetime z logu. Zwraca aware UTC datetime albo None."""
    if not s or not isinstance(s, str):
        return None
    txt = s.strip()
    try:
        # ISO z offsetem (logged_at / ts / shadow ts)
        if "T" in txt:
            dt = datetime.fromisoformat(txt.replace("Z", "+00:00"))
        else:
            # "2026-05-17 17:19:48" — picked_up_at/delivered_at, czas Warsaw
            dt = datetime.strptime(txt, "%Y-%m-%d %H:%M:%S").replace(tzinfo=WARSAW)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=WARSAW)
        return dt
    except (ValueError, TypeError):
        return None


def _read_jsonl(path):
    """Czyta plik JSONL linia-po-linii, pomija uszkodzone wiersze."""
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


def load_already_logged():
    """Zbiór oid-ów już zapisanych w logu kalibracyjnym (idempotencja)."""
    seen = set()
    for rec in _read_jsonl(OUT_LOG):
        oid = rec.get("oid")
        if oid is not None:
            seen.add(str(oid))
    return seen


def build_shadow_index():
    """oid -> lista (ts_dt, rekord) decyzji shadow z populated `best.plan`.

    Jedno zlecenie może mieć kilka decyzji (re-propozycje). Trzymamy wszystkie,
    posortowane rosnąco po czasie; selekcja predykcji następuje przy joinie.
    """
    idx = {}
    for rec in _read_jsonl(SHADOW_LOG):
        oid = rec.get("order_id")
        if oid is None:
            continue
        best = rec.get("best") or {}
        plan = best.get("plan") or {}
        podt = plan.get("per_order_delivery_times") or {}
        if str(oid) not in podt:
            continue  # decyzja bez predykcji dla tego zlecenia — pomijamy
        ts = _parse_dt(rec.get("ts"))
        if ts is None:
            continue
        idx.setdefault(str(oid), []).append((ts, rec))
    for oid in idx:
        idx[oid].sort(key=lambda x: x[0])
    return idx


def pick_prediction(oid, delivered_at, shadow_recs):
    """Wybiera decyzję shadow reprezentatywną dla predykcji Ziomka.

    Bierze NAJPÓŹNIEJSZĄ decyzję sprzed dostawy (najświeższy obraz sytuacji,
    na którym najpewniej działał koordynator). Jeśli żadna nie jest sprzed
    dostawy — bierze pierwszą dostępną (edge: zegar/log lag).
    """
    if not shadow_recs:
        return None
    before = [(ts, r) for ts, r in shadow_recs
              if delivered_at is None or ts <= delivered_at]
    chosen_ts, chosen = (before[-1] if before else shadow_recs[0])
    return chosen_ts, chosen, len(shadow_recs)


def extract_row(sla_rec, shadow_index):
    """Buduje jeden wiersz kalibracyjny ze zlecenia sla_log + kontekstu shadow."""
    oid = str(sla_rec.get("order_id"))
    delivered_at = _parse_dt(sla_rec.get("delivered_at"))
    picked_up_at = _parse_dt(sla_rec.get("picked_up_at"))
    real_min = sla_rec.get("delivery_time_minutes")

    hour_warsaw = picked_up_at.astimezone(WARSAW).hour if picked_up_at else None
    weekday = picked_up_at.astimezone(WARSAW).weekday() if picked_up_at else None

    row = {
        "oid": oid,
        "logged_at": datetime.now(WARSAW).isoformat(),
        "real_delivery_min": real_min,
        "predicted_delivery_min": None,
        # error_min: real - predykcja czasu trwania. UWAGA: per_order_delivery_times
        # kotwiczone na pickup_ready_at, real na picked_up_at — przy bagach ta
        # różnica kotwic zniekształca metrykę. Do diagnostyki, NIE do kalibracji.
        "error_min": None,
        # eta_error_min: faktyczny delivered_at - predicted_delivered_at (oba
        # absolutne timestampy → anchor-free). Dodatni = dostawa PÓŹNIEJ niż Ziomek
        # obiecał = "za krótkie czasy". To jest metryka nagłówkowa kalibracji.
        "predicted_delivered_at": None,
        "eta_error_min": None,
        "matched": False,
        "bag_size": None,             # finalny bag w którym jechało zlecenie
        "is_bundle": None,
        "r6_max_bag_time_min": None,
        "total_duration_min": None,
        "strategy": None,             # ortools / greedy / ortools_rejected_v3274
        "verdict": None,
        "courier_id": sla_rec.get("courier_id"),
        "courier_name": None,
        "restaurant": sla_rec.get("restaurant"),
        "delivery_address": sla_rec.get("delivery_address"),
        "picked_up_at": sla_rec.get("picked_up_at"),
        "delivered_at": sla_rec.get("delivered_at"),
        "hour_warsaw": hour_warsaw,
        "weekday": weekday,           # 0=pon ... 5=sob, 6=niedz
        "is_weekend": (weekday >= 5) if weekday is not None else None,
        "bucket": _bucket(hour_warsaw) if hour_warsaw is not None else None,
        "sla_ok": sla_rec.get("sla_ok"),
        "was_czasowka": sla_rec.get("was_czasowka"),
        "n_shadow_records": 0,
        "shadow_ts": None,
    }

    picked = pick_prediction(oid, delivered_at, shadow_index.get(oid))
    if picked is not None:
        chosen_ts, rec, n_recs = picked
        best = rec.get("best") or {}
        plan = best.get("plan") or {}
        podt = plan.get("per_order_delivery_times") or {}
        pred = podt.get(oid)
        # finalny rozmiar baga: r6_bag_size = ile było PRZED dodaniem; +1 = z nowym.
        # Fallback chain jak w fixie #474227 (pole bywa null przy early-return R6).
        bag_before = best.get("r6_bag_size")
        if bag_before is None:
            bag_before = best.get("bag_size_before")
        if bag_before is None:
            bag_before = best.get("r7_bag_size")
        bag_final = (bag_before + 1) if isinstance(bag_before, (int, float)) else None

        row["matched"] = True
        row["predicted_delivery_min"] = pred
        if isinstance(pred, (int, float)) and isinstance(real_min, (int, float)):
            row["error_min"] = round(real_min - pred, 2)
        # Anchor-free: predicted_delivered_at vs faktyczny delivered_at.
        pred_deliv_at = (plan.get("predicted_delivered_at") or {}).get(oid)
        row["predicted_delivered_at"] = pred_deliv_at
        pred_deliv_dt = _parse_dt(pred_deliv_at)
        if pred_deliv_dt is not None and delivered_at is not None:
            row["eta_error_min"] = round(
                (delivered_at - pred_deliv_dt).total_seconds() / 60.0, 2)
        row["bag_size"] = bag_final
        row["is_bundle"] = (bag_final >= 2) if bag_final is not None else None
        row["r6_max_bag_time_min"] = best.get("r6_max_bag_time_min")
        row["total_duration_min"] = plan.get("total_duration_min")
        row["strategy"] = plan.get("strategy")
        row["verdict"] = rec.get("verdict")
        row["courier_name"] = best.get("name")
        row["n_shadow_records"] = n_recs
        row["shadow_ts"] = chosen_ts.isoformat()

    return row


def append_atomic(rows):
    """Dopisuje wiersze do logu kalibracyjnego (append + fsync, bez nadpisania)."""
    if not rows:
        return
    os.makedirs(os.path.dirname(OUT_LOG), exist_ok=True)
    with open(OUT_LOG, "a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def summarize(rows):
    """Zwięzłe podsumowanie na stdout — logger pełni też rolę mini-dashboardu.

    Metryka nagłówkowa: eta_error_min (anchor-free). Dodatni = dostawa później
    niż Ziomek obiecał = czasy za krótkie.
    """
    matched = [r for r in rows if r.get("eta_error_min") is not None]
    print(f"  nowych wierszy: {len(rows)}  |  z metryką ETA (matched): {len(matched)}")
    if not matched:
        return

    def _stats(errs):
        s = sorted(errs)
        return sum(errs) / len(errs), s[len(s) // 2], s[int(len(s) * 0.9)]

    by_bucket = {}
    for r in matched:
        by_bucket.setdefault(r["bucket"], []).append(r["eta_error_min"])
    print("  eta_error (delivered - obiecane) per bucket [dodatni = za krótko]:")
    for b in ("peak", "shoulder", "offpeak"):
        errs = by_bucket.get(b)
        if not errs:
            continue
        mean, med, p90 = _stats(errs)
        print(f"    {b:9s} n={len(errs):4d}  mean={mean:+6.1f}  median={med:+6.1f}  p90={p90:+6.1f}")
    solo = [r["eta_error_min"] for r in matched if r.get("bag_size") == 1]
    bund = [r["eta_error_min"] for r in matched if r.get("bag_size") and r["bag_size"] >= 2]
    if solo:
        mean, med, _ = _stats(solo)
        print(f"  solo:   n={len(solo):4d}  mean={mean:+6.1f}  median={med:+6.1f}")
    if bund:
        mean, med, _ = _stats(bund)
        print(f"  bundle: n={len(bund):4d}  mean={mean:+6.1f}  median={med:+6.1f}")


def main():
    print(f"[eta_calibration_logger] {datetime.now(WARSAW).isoformat()}")
    already = load_already_logged()
    shadow_index = build_shadow_index()

    # Ostatni rekord per oid w sla_log (deduplikacja ewentualnych powtórek).
    sla_by_oid = {}
    for rec in _read_jsonl(SLA_LOG):
        oid = rec.get("order_id")
        if oid is None:
            continue
        sla_by_oid[str(oid)] = rec

    new_rows = []
    for oid, sla_rec in sla_by_oid.items():
        if oid in already:
            continue
        try:
            new_rows.append(extract_row(sla_rec, shadow_index))
        except Exception as exc:  # noqa: BLE001 — defensywnie, pojedynczy oid nie wywala całości
            print(f"  WARN oid={oid}: {type(exc).__name__}: {exc}", file=sys.stderr)

    append_atomic(new_rows)
    summarize(new_rows)
    print(f"  log: {OUT_LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
