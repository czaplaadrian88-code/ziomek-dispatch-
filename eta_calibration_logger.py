#!/usr/bin/env python3
"""eta_calibration_logger — pętla uczenia Ziomka: predykcja ETA vs rzeczywistość.

Sprint 1 (2026-05-17). Po diagnozie 2026-05-17: pierwsza wersja joinowała
predykcję `best` (kuriera, którego Ziomek PROPONUJE) z rzeczywistością — ale
realny kurier ≠ best w 83% przypadków → metryka mierzyła rozjazd atrybucji,
nie błąd modelu. WERSJA 2: joinuje predykcję dla kuriera, który REALNIE
dowiózł zlecenie.

Jak: shadow_decisions.jsonl ma dla każdego zlecenia `best` + `alternatives[]`
— każdy kandydat to inny kurier z własnym `plan.predicted_delivered_at`.
Logger szuka w tej puli kuriera == realny kurier (z sla_log) i bierze JEGO
predykcję. Gdy realnego kuriera nie ma w puli → fallback na `best` + flaga
matched_courier=False (żeby było widać pokrycie).

Metryka nagłówkowa: `eta_error_min` = realny delivered_at − predicted_delivered_at
kuriera realnego (anchor-free). Dodatni = dostawa później niż obiecano.
`prediction_age_min` = picked_up_at − shadow_ts — eksponuje staleness predykcji
(jest jednorazowa, robiona ~44 min przed pickupem).

NIE hot-path. Timer dispatch-eta-calibration co 30 min. Tylko czyta logi
produkcyjne, pisze własny eta_calibration_log.jsonl. Idempotentny per oid.

Uruchomienie:
    /root/.openclaw/venvs/dispatch/bin/python eta_calibration_logger.py
"""
import json
import os
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")

# ETA R3 shadow (2026-06-18) — opcjonalne; logger nie może się wywalić gdy brak modułu/modelu.
# Dual-path import (script-mode ExecStart WD=dispatch_v2 → bare; package-mode pytest → from
# dispatch_v2). eta_residual_infer = stdlib+numpy+lightgbm, bez zależności od pakietu dispatch_v2.
try:
    import eta_residual_infer as _R3
except Exception:
    try:
        from dispatch_v2 import eta_residual_infer as _R3
    except Exception:
        _R3 = None

# Flagę czytamy WPROST z flags.json (KANON hot), NIE przez common — common.py wymaga pakietu
# dispatch_v2 na ścieżce (absolutne importy), czego kontekst wykonania loggera nie gwarantuje.
_FLAGS_PATH = f"{os.path.dirname(os.path.dirname(os.path.abspath(__file__)))}/flags.json"

# Ustawiane raz w main() z flags.json (hot). Domyślnie OFF → zero nowych pól, zachowanie bez zmian.
_R3_SHADOW_ON = False


def _read_r3_flag():
    """Czyta ENABLE_ETA_R3_SHADOW wprost z flags.json (fail-soft → False)."""
    try:
        with open(_FLAGS_PATH, encoding="utf-8") as fh:
            return bool(json.load(fh).get("ENABLE_ETA_R3_SHADOW", False))
    except Exception:
        return False

BASE = "/root/.openclaw/workspace"
SLA_LOG = f"{BASE}/scripts/logs/sla_log.jsonl"
SHADOW_LOG = f"{BASE}/scripts/logs/shadow_decisions.jsonl"
OUT_LOG = f"{BASE}/dispatch_state/eta_calibration_log.jsonl"

PEAK_HOURS = frozenset({11, 12, 13, 17, 18, 19})
SHOULDER_HOURS = frozenset({10, 14, 15, 16, 20})


def _bucket(hour):
    if hour in PEAK_HOURS:
        return "peak"
    if hour in SHOULDER_HOURS:
        return "shoulder"
    return "offpeak"


def _parse_dt(s):
    """Parsuje datetime z logu. Zwraca aware UTC/Warsaw datetime albo None."""
    if not s or not isinstance(s, str):
        return None
    txt = s.strip()
    try:
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


def _cid(v):
    """Normalizuje courier_id do str (źródła mieszają int/str)."""
    if v is None:
        return None
    return str(v).strip()


def _read_jsonl(path):
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
    seen = set()
    for rec in _read_jsonl(OUT_LOG):
        oid = rec.get("oid")
        if oid is not None:
            seen.add(str(oid))
    return seen


def build_shadow_index():
    """oid -> lista (ts_dt, rekord). Wszystkie decyzje shadow per zlecenie."""
    idx = {}
    for rec in _read_jsonl(SHADOW_LOG):
        oid = rec.get("order_id")
        if oid is None:
            continue
        ts = _parse_dt(rec.get("ts"))
        if ts is None:
            continue
        idx.setdefault(str(oid), []).append((ts, rec))
    for oid in idx:
        idx[oid].sort(key=lambda x: x[0])
    return idx


def _candidates(record):
    """[best] + alternatives[] — wszyscy kurierzy ocenieni dla tego zlecenia."""
    out = []
    best = record.get("best")
    if isinstance(best, dict) and best:
        out.append(best)
    for a in record.get("alternatives") or []:
        if isinstance(a, dict):
            out.append(a)
    return out


def _pred_for(cand, oid):
    """predicted_delivered_at[oid] z planu kandydata albo None."""
    plan = cand.get("plan") or {}
    return (plan.get("predicted_delivered_at") or {}).get(oid)


def pick_prediction(oid, real_cid, delivered_at, shadow_recs):
    """Wybiera predykcję dla kuriera, który REALNIE dowiózł zlecenie.

    Skanuje decyzje shadow od najnowszej: szuka w puli kandydatów (best +
    alternatives) kuriera == real_cid z predykcją dla tego oid. Gdy znajdzie
    → (ts, record, candidate, matched=True). Gdy realnego kuriera nie ma w
    żadnej puli → fallback: najnowszy rekord, jego `best` (matched=False).
    """
    if not shadow_recs:
        return None
    before = [(ts, r) for ts, r in shadow_recs
              if delivered_at is None or ts <= delivered_at] or shadow_recs

    # 1. Predykcja dla realnego kuriera — od najnowszej decyzji.
    if real_cid:
        for ts, rec in reversed(before):
            for cand in _candidates(rec):
                if _cid(cand.get("courier_id")) == real_cid and _pred_for(cand, oid):
                    return (ts, rec, cand, True)

    # 2. Fallback: best najnowszej decyzji (realnego kuriera nie było w puli).
    for ts, rec in reversed(before):
        best = rec.get("best") or {}
        if best and _pred_for(best, oid):
            return (ts, rec, best, False)
    return None


def _bag_final(cand):
    """Finalny rozmiar baga kandydata: r6_bag_size+1 (z fallbackiem)."""
    b = cand.get("r6_bag_size")
    if b is None:
        b = cand.get("bag_size_before")
    if b is None:
        b = cand.get("r7_bag_size")
    return (b + 1) if isinstance(b, (int, float)) else None


def extract_row(sla_rec, shadow_index):
    """Buduje jeden wiersz kalibracyjny: predykcja realnego kuriera vs rzeczywistość."""
    oid = str(sla_rec.get("order_id"))
    real_cid = _cid(sla_rec.get("courier_id"))
    delivered_at = _parse_dt(sla_rec.get("delivered_at"))
    picked_up_at = _parse_dt(sla_rec.get("picked_up_at"))
    real_min = sla_rec.get("delivery_time_minutes")

    hour = picked_up_at.astimezone(WARSAW).hour if picked_up_at else None
    weekday = picked_up_at.astimezone(WARSAW).weekday() if picked_up_at else None

    row = {
        "oid": oid,
        "logged_at": datetime.now(WARSAW).isoformat(),
        "real_delivery_min": real_min,
        "real_courier_id": real_cid,
        # matched_courier=True → predykcja dotyczy kuriera, który REALNIE dowiózł.
        # To jedyna metryka, na której wolno kalibrować. False → fallback na best.
        "matched_courier": False,
        "predicted_for": None,            # 'real_courier' | 'best_fallback'
        "best_courier_id": None,          # kogo Ziomek proponował (do analizy rozjazdu)
        "predicted_delivered_at": None,
        "predicted_delivery_min": None,   # per_order_delivery_times[oid]
        # eta_error_min: delivered_at − predicted_delivered_at dla REALNEGO kuriera.
        # Dodatni = za późno = czas obiecany za krótki. METRYKA NAGŁÓWKOWA.
        "eta_error_min": None,
        # prediction_age_min: picked_up_at − shadow_ts. Predykcja jest jednorazowa;
        # to pole pokazuje jak bardzo była nieaktualna w chwili odbioru.
        "prediction_age_min": None,
        "bag_size": None,
        "is_bundle": None,
        "r6_max_bag_time_min": None,
        "total_duration_min": None,
        "strategy": None,
        "verdict": None,
        "restaurant": sla_rec.get("restaurant"),
        "delivery_address": sla_rec.get("delivery_address"),
        "picked_up_at": sla_rec.get("picked_up_at"),
        "delivered_at": sla_rec.get("delivered_at"),
        "hour_warsaw": hour,
        "weekday": weekday,
        "is_weekend": (weekday >= 5) if weekday is not None else None,
        "bucket": _bucket(hour) if hour is not None else None,
        "sla_ok": sla_rec.get("sla_ok"),
        "was_czasowka": sla_rec.get("was_czasowka"),
        "n_shadow_records": 0,
        "shadow_ts": None,
        # ETA R3 shadow (tylko gdy ENABLE_ETA_R3_SHADOW): korekta residualna obok bazy OSRM.
        # corrected = predicted_delivery_min + residual_pred; error_min = real − corrected
        # (analogicznie do eta bazowej real − predicted_delivery_min). ZERO wpływu na decyzje.
        "eta_r3_residual_pred": None,
        "eta_r3_corrected_delivery_min": None,
        "eta_r3_corrected_error_min": None,
        # ETA R3 wariant B_drop (tylko gdy ENABLE_ETA_R3_DROP_SHADOW): bez cechy pool_feasible.
        # corrected_drop = base + residual_drop; error_min_drop = real − corrected_drop. ZERO wpływu na decyzje.
        "eta_r3_residual_pred_drop": None,
        "eta_r3_corrected_delivery_min_drop": None,
        "eta_r3_corrected_error_min_drop": None,
    }

    recs = shadow_index.get(oid)
    if recs:
        row["n_shadow_records"] = len(recs)
    picked = pick_prediction(oid, real_cid, delivered_at, recs or [])
    if picked is not None:
        chosen_ts, rec, cand, matched = picked
        plan = cand.get("plan") or {}
        best = rec.get("best") or {}

        row["matched_courier"] = matched
        row["predicted_for"] = "real_courier" if matched else "best_fallback"
        row["best_courier_id"] = _cid(best.get("courier_id"))
        pred_deliv_at = (plan.get("predicted_delivered_at") or {}).get(oid)
        row["predicted_delivered_at"] = pred_deliv_at
        row["predicted_delivery_min"] = (plan.get("per_order_delivery_times") or {}).get(oid)
        row["bag_size"] = _bag_final(cand)
        row["is_bundle"] = (row["bag_size"] >= 2) if row["bag_size"] is not None else None
        row["r6_max_bag_time_min"] = cand.get("r6_max_bag_time_min")
        row["total_duration_min"] = plan.get("total_duration_min")
        row["strategy"] = plan.get("strategy")
        row["verdict"] = rec.get("verdict")
        row["shadow_ts"] = chosen_ts.isoformat()

        pred_dt = _parse_dt(pred_deliv_at)
        if pred_dt is not None and delivered_at is not None:
            row["eta_error_min"] = round(
                (delivered_at - pred_dt).total_seconds() / 60.0, 2)
        if picked_up_at is not None:
            row["prediction_age_min"] = round(
                (picked_up_at - chosen_ts).total_seconds() / 60.0, 2)

        # --- ETA R3 shadow: corrected = base + residual_pred (fail-soft, zero wpływu) ---
        if _R3_SHADOW_ON and _R3 is not None and row["predicted_delivery_min"] is not None:
            try:
                corrected, resid = _R3.predict_corrected(
                    bag_size=row["bag_size"],
                    predicted_delivery_min=row["predicted_delivery_min"],
                    hour_warsaw=row["hour_warsaw"],
                    is_weekend=row["is_weekend"],
                    is_bundle=row["is_bundle"],
                    restaurant=row["restaurant"],
                    courier_id=real_cid,
                    pool_feasible=rec.get("pool_feasible_count"),
                )
                if corrected is not None:
                    row["eta_r3_residual_pred"] = resid
                    row["eta_r3_corrected_delivery_min"] = corrected
                    if isinstance(real_min, (int, float)):
                        row["eta_r3_corrected_error_min"] = round(real_min - corrected, 2)
            except Exception as exc:  # noqa: BLE001 — shadow nigdy nie wywala loggera
                print(f"  WARN R3 shadow oid={oid}: {type(exc).__name__}: {exc}", file=sys.stderr)

        # --- ETA R3 wariant B_drop shadow (guarded: ENABLE_ETA_R3_DROP_SHADOW, fail-soft, zero wpływu) ---
        # Logujemy korektę bez cechy pool_feasible OBOK v1 — pozwala forward-porównać MAE(base) vs
        # MAE(v1) vs MAE(drop) na NOWYCH dniach (zwłaszcza weekendach, gdzie DROP oblał bramkę 06-20).
        if _R3 is not None and row["predicted_delivery_min"] is not None:
            try:
                corrected_d, resid_d = _R3.predict_corrected_drop_if_enabled(
                    bag_size=row["bag_size"],
                    predicted_delivery_min=row["predicted_delivery_min"],
                    hour_warsaw=row["hour_warsaw"],
                    is_weekend=row["is_weekend"],
                    is_bundle=row["is_bundle"],
                    restaurant=row["restaurant"],
                    courier_id=real_cid,
                )
                if corrected_d is not None:
                    row["eta_r3_residual_pred_drop"] = resid_d
                    row["eta_r3_corrected_delivery_min_drop"] = corrected_d
                    if isinstance(real_min, (int, float)):
                        row["eta_r3_corrected_error_min_drop"] = round(real_min - corrected_d, 2)
            except Exception as exc:  # noqa: BLE001 — shadow nigdy nie wywala loggera
                print(f"  WARN R3 DROP shadow oid={oid}: {type(exc).__name__}: {exc}", file=sys.stderr)

    return row


def append_atomic(rows):
    if not rows:
        return
    os.makedirs(os.path.dirname(OUT_LOG), exist_ok=True)
    with open(OUT_LOG, "a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def summarize(rows):
    """Podsumowanie na stdout. Metryka liczy się TYLKO z matched_courier=True."""
    czas = [r for r in rows if r.get("was_czasowka")]
    matched = [r for r in rows if r.get("matched_courier")
               and r.get("eta_error_min") is not None and not r.get("was_czasowka")]
    fallback = [r for r in rows if not r.get("matched_courier")
                and not r.get("was_czasowka")]
    print(f"  nowych wierszy: {len(rows)}  (czasówki: {len(czas)})")
    print(f"  kurier dopasowany (metryka wiarygodna): {len(matched)}  |  "
          f"fallback na best (pomijane): {len(fallback)}")
    if not matched:
        return

    def _stats(v):
        s = sorted(v)
        return sum(v) / len(v), s[len(s) // 2], s[int(len(s) * 0.9)]

    by_bucket = {}
    for r in matched:
        by_bucket.setdefault(r["bucket"], []).append(r["eta_error_min"])
    print("  eta_error (delivered - obiecane, kurier realny) [dodatni = za krótko]:")
    for b in ("peak", "shoulder", "offpeak"):
        v = by_bucket.get(b)
        if not v:
            continue
        mean, med, p90 = _stats(v)
        print(f"    {b:9s} n={len(v):4d}  mean={mean:+6.1f}  median={med:+6.1f}  p90={p90:+6.1f}")
    ages = [r["prediction_age_min"] for r in matched if r.get("prediction_age_min") is not None]
    if ages:
        _, amed, _ = _stats(ages)
        print(f"  wiek predykcji (shadow_ts→pickup) mediana: {amed:.0f} min")


def main():
    global _R3_SHADOW_ON
    print(f"[eta_calibration_logger v2] {datetime.now(WARSAW).isoformat()}")
    _R3_SHADOW_ON = _read_r3_flag()
    if _R3_SHADOW_ON:
        avail = _R3.is_available() if _R3 is not None else False
        print(f"  ETA R3 shadow: ON (model_available={avail})")
    already = load_already_logged()
    shadow_index = build_shadow_index()

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
        except Exception as exc:  # noqa: BLE001 — pojedynczy oid nie wywala całości
            print(f"  WARN oid={oid}: {type(exc).__name__}: {exc}", file=sys.stderr)

    append_atomic(new_rows)
    summarize(new_rows)
    print(f"  log: {OUT_LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
