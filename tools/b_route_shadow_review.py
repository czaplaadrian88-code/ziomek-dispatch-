"""b_route_shadow_review — outcome-join + werdykt korpusu B-shadow (Adrian 23.06: przegląd 30.06).

Czyta `b_route_shadow.jsonl` (served/B/B-lite + estymowane metryki) i joinuje `order_ids` do
REALNYCH wyników z `sla_log.jsonl` (delivered_at, picked_up_at, delivery_time_minutes, on_time).
Odpowiada: czy worki, gdzie B dałby INNĄ trasę niż serwowana, kończyły się GORZEJ przy serwowanej?
GO (B realnie lepszy + served słabo) → wdrożyć B-lite. NO-GO (B estym. gorszy / served OK) → zamknąć.

Read-only, jednorazowy. Werdykt na Telegram (grupa ziomka). Uruchamiany przez one-shot timer 30.06.
"""
import os
import sys
import json
import statistics
from datetime import datetime, timezone

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

# L1.2 (2026-07-02): ground-truth arm przepięty z MARTWEGO dispatch_state/sla_log
# (zamrożony 2026-06-20 → real_joined=0, werdykt VOID w rejestrze FAZA1_03) na
# ŻYWY kanon ledger_io.iter_sla. Różnice schematu żywego loga obsłużone jawnie:
# stemple naive=Warsaw (parse_sla_ts), brak on_time (ready-anchor) → fallback
# sla_ok (pickup-anchor), brak is_peak → rekompute z delivered_at tą samą
# definicją (ontime_lib.is_peak).
from dispatch_v2.tools import ledger_io, ontime_lib  # noqa: E402

STATE_DIR = "/root/.openclaw/workspace/dispatch_state"
CORPUS = f"{STATE_DIR}/b_route_shadow.jsonl"
MIN_DIFFERS = 20          # minimum różniących się worków na pewny werdykt


def _read_jsonl(path):
    out = []
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except Exception:
                        pass
    except FileNotFoundError:
        pass
    return out


def _parse(ts):
    if not ts or ts == "None":
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _med(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 2) if xs else None


def _sla_index():
    """{order_id: {real_food_age_min, on_time, delivery_min, is_peak}} z ŻYWEGO
    sla_log (kanon ledger_io.iter_sla; ostatni wpis/oid). on_time: pole
    ready-anchored gdy jest (martwy schemat), inaczej sla_ok (pickup-anchor)."""
    idx = {}
    for r in ledger_io.iter_sla(None):
        oid = str(r.get("order_id"))
        d = ledger_io.parse_sla_ts(r.get("delivered_at"))
        p = ledger_io.parse_sla_ts(r.get("picked_up_at"))
        age = (d - p).total_seconds() / 60.0 if (d and p) else None
        if "on_time" in r:
            on_time = str(r.get("on_time")) == "True"
        else:  # żywy log: sla_ok (≤35 min od ODBIORU) — jawnie inna kotwica
            on_time = r.get("sla_ok") is True
        ip = r.get("is_peak")
        if ip is None and d is not None:
            ip = ontime_lib.is_peak(d)  # ta sama definicja, którą pisał martwy log
        idx[oid] = {
            "real_food_age_min": round(age, 1) if age is not None else None,
            "on_time": on_time,
            "delivery_min": float(r.get("delivery_time_minutes")) if r.get("delivery_time_minutes") not in (None, "None") else None,
            "is_peak": str(ip) == "True" or ip is True,
        }
    return idx


def build_report():
    corpus = _read_jsonl(CORPUS)
    sla = _sla_index()
    multi = [r for r in corpus if r.get("n_orders", 0) >= 2]
    b_computed = [r for r in multi if r.get("b")]
    differs_b = [r for r in b_computed if r.get("differs_b")]
    differs_blite = [r for r in multi if r.get("differs_blite")]

    # Estymowane delty B-vs-served (>0 = B lepszy): jazda, świeżość niesionego
    d_drive = [r.get("delta_drive_b") for r in differs_b]
    d_age = [r.get("delta_carried_age_b") for r in differs_b]
    # punktualność: m_served.pickup_late - m_b.pickup_late (>0 = B mniej spóźnia)
    d_late = []
    for r in differs_b:
        ms, mb = r.get("m_served") or {}, r.get("m_b") or {}
        if ms.get("pickup_late_max_min") is not None and mb.get("pickup_late_max_min") is not None:
            d_late.append(ms["pickup_late_max_min"] - mb["pickup_late_max_min"])

    b_better = sum(1 for r in differs_b
                   if (r.get("delta_drive_b") or 0) > 0.5 and (r.get("delta_carried_age_b") or 0) >= 0)
    b_worse = sum(1 for r in differs_b
                  if (r.get("delta_drive_b") or 0) < -0.5 or (r.get("delta_carried_age_b") or 0) < 0)

    # REALNY outcome served w worku gdzie B≠served (z sla_log): świeżość + on-time
    real_ages, real_ontime, joined = [], [], 0
    for r in differs_b:
        ids = r.get("order_ids") or []
        hit = [sla[o] for o in ids if o in sla]
        if not hit:
            continue
        joined += 1
        ages = [h["real_food_age_min"] for h in hit if h["real_food_age_min"] is not None]
        if ages:
            real_ages.append(max(ages))
        real_ontime.append(all(h["on_time"] for h in hit))

    rep = {
        "corpus_total": len(corpus),
        "multi_order": len(multi),
        "b_computed": len(b_computed),
        "differs_b": len(differs_b),
        "differs_blite": len(differs_blite),
        "differs_b_pct": round(100 * len(differs_b) / max(len(b_computed), 1), 1),
        "est_delta_drive_med": _med(d_drive),
        "est_delta_carried_age_med": _med(d_age),
        "est_delta_pickup_late_med": _med(d_late),
        "b_better_n": b_better,
        "b_worse_n": b_worse,
        "real_joined": joined,
        "real_served_food_age_med": _med(real_ages),
        "real_served_ontime_pct": round(100 * sum(real_ontime) / len(real_ontime), 1) if real_ontime else None,
    }
    rep["verdict"], rep["recommendation"] = _verdict(rep)
    return rep


def _verdict(r):
    if r["differs_b"] < MIN_DIFFERS:
        return ("INCONCLUSIVE", f"Za mało worków B≠served ({r['differs_b']}<{MIN_DIFFERS}) — przedłużyć shadow.")
    dd = r["est_delta_drive_med"] or 0
    da = r["est_delta_carried_age_med"] or 0
    # B estym. GORSZY (krótsza jazda/świeżość po stronie served) → NO-GO
    if dd <= 0 and da <= 0 and r["b_worse_n"] >= r["b_better_n"]:
        return ("NO-GO", "B re-TSP NIE poprawia (estym. gorszy/równy na jeździe+świeżości). Zamknąć temat, wyłączyć shadow.")
    # B estym. LEPSZY w większości — sprawdź czy served realnie słabo tam gdzie B≠
    if r["b_better_n"] > r["b_worse_n"] and dd > 0.5:
        if r["real_served_ontime_pct"] is not None and r["real_served_ontime_pct"] < 80:
            return ("GO-KANDYDAT", "B estym. lepszy + served realnie spóźnia (<80% on-time) → zbudować+zwalidować B-lite (~10ms).")
        return ("MIXED", "B estym. lepszy, ale served realnie OK — zysk niepewny; przedłużyć/zawęzić do worków gdzie served realnie słabo.")
    return ("MIXED", "Sygnał niejednoznaczny — przedłużyć shadow lub zawęzić analizę.")


def _fmt(r):
    L = ["🔬 B-SHADOW przegląd (czy re-TSP/B-lite warto vs serwowany kanon)",
         f"Korpus: {r['corpus_total']} wpisów / {r['multi_order']} multi-order / B policzone {r['b_computed']}",
         f"B≠served: {r['differs_b']} ({r['differs_b_pct']}%) · B-lite≠served: {r['differs_blite']}",
         "",
         "Estym. B vs served (gdy B≠, >0 = B lepszy):",
         f"  jazda Δ med: {r['est_delta_drive_med']} min · świeżość niesionego Δ med: {r['est_delta_carried_age_med']} min · odbiór-spóźnienie Δ med: {r['est_delta_pickup_late_med']} min",
         f"  B lepszy w {r['b_better_n']} / B gorszy w {r['b_worse_n']} worków",
         "",
         f"Realny outcome served (join sla_log, n={r['real_joined']}): świeżość med {r['real_served_food_age_med']} min · on-time {r['real_served_ontime_pct']}%",
         "",
         f"➤ WERDYKT: {r['verdict']}",
         f"   {r['recommendation']}"]
    return "\n".join(L)


def main():
    rep = build_report()
    msg = _fmt(rep)
    print(msg)
    print("\nJSON:", json.dumps(rep, ensure_ascii=False))
    if "--no-telegram" not in sys.argv:
        try:
            from dispatch_v2.telegram_utils import send_admin_alert
            send_admin_alert(msg, source="b_route_shadow_review")
            print("\n[telegram] wysłano")
        except Exception as e:
            print(f"\n[telegram] fail: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
