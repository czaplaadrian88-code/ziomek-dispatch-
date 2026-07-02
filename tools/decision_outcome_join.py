#!/usr/bin/env python3
"""decision_outcome_join — pomiar REALNEJ jakości decyzji Ziomka (rec#1, 2026-06-23).

READ-ONLY. Łączy decyzję Ziomka (kogo proponował) z FAKTYCZNYM wynikiem dostawy
(kto realnie dowiózł, realny czas, czy na czas) — żeby odpowiedzieć na pytanie
"czy Ziomek decyduje dobrze?" liczbami z RZECZYWISTYCH dostaw, NIE z predykcji
ani z tego, kogo wybrał człowiek (override ≠ jakość — Ziomek pracuje w tle).

Nie duplikuje joinu — KONSUMUJE istniejący `eta_calibration_log.jsonl`
(produkowany przez eta_calibration_logger.py v2: real_delivery_min, sla_ok,
best_courier_id=pick Ziomka, real_courier_id=kto dowiózł, eta_error_min,
matched_courier) + `sla_log.jsonl` (realny wynik floty, niezależny od Ziomka).

Metryki:
  A. Realna jakość floty (sla_log)  — on-time %, R6-breach realny %, mediana czasu.
  B. Zgodność Ziomek↔człowiek       — % best==real (inwersja override; informacyjne).
  C. Jakość gdy WZIĘTO Ziomka       — on-time % gdy best==real vs gdy człowiek nadpisał.
  D. Kalibracja predykcji           — eta_error (real−obiecane) med/p90; % fałszywego optymizmu.
  E. Kontrfaktyk (--counterfactual) — SZACUNEK: gdy człowiek≠Ziomek, debias-predykcja
                                       Ziomka dla JEGO kuriera vs realny wynik człowieka.

NIE hot-path. Nie dotyka decyzji ani stanu (chyba że --emit → decision_outcomes.jsonl).
Uruchom:
  cd /root/.openclaw/workspace/scripts
  PYTHONPATH=. /root/.openclaw/venvs/dispatch/bin/python dispatch_v2/tools/decision_outcome_join.py --days 14 --counterfactual
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
BASE = "/root/.openclaw/workspace"
ETA_CALIB = f"{BASE}/dispatch_state/eta_calibration_log.jsonl"
SLA_LOG = f"{BASE}/scripts/logs/sla_log.jsonl"

# L1.2 (2026-07-02): odczyt shadow_decisions ROTATION-AWARE przez kanon
# (_rotated_logs/ledger_io) — stary hardkod [żywy, .1] gubił .2.gz po rotacji
# (logrotate size 100M / daily + delaycompress). files_in_window daje pełny
# łańcuch (.N.gz→.1→żywy) chronologicznie; ścieżka = ledger_io.LEDGER (jedno
# źródło). Indeks best-pred jest last-wins ("ostatni rekord") → chronologiczny
# porządek kanonu = najświeższy wygrywa (zgodnie z docstringiem). Per-rekord
# filtr ts konsumenta NIETKNIĘTY, metryki BEZ ZMIAN.
try:
    from dispatch_v2.tools import _rotated_logs, ledger_io
except ImportError:
    import os as _os, sys as _sys
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))))
    from dispatch_v2.tools import _rotated_logs, ledger_io

SHADOW_LOGS = _rotated_logs.files_in_window(ledger_io.LEDGER["shadow"])
OUT = f"{BASE}/dispatch_state/decision_outcomes.jsonl"
SLA_MIN = 35.0
# Znany optymizm predykcji odbioru (poślizg ODBIORU, audyt 2026-06-22/23): real ≈ predicted + ~9 min.
PICKUP_SLIP_DEBIAS_MIN = 9.0


def _read_jsonl(path):
    if not os.path.exists(path):
        return
    with _rotated_logs.open_maybe_gz(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue


def _parse_dt(s):
    if not s or not isinstance(s, str):
        return None
    t = s.strip()
    try:
        if "T" in t:
            dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
        else:
            dt = datetime.strptime(t, "%Y-%m-%d %H:%M:%S").replace(tzinfo=WARSAW)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=WARSAW)
        return dt
    except (ValueError, TypeError):
        return None


def _cid(v):
    return None if v is None else str(v).strip()


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _pct(n, d):
    return (100.0 * n / d) if d else 0.0


def _stats(vals):
    v = sorted(x for x in vals if x is not None)
    if not v:
        return (None, None, None, 0)
    n = len(v)
    return (sum(v) / n, v[n // 2], v[min(n - 1, int(n * 0.9))], n)


def _shadow_best_pred_index(cutoff):
    """oid -> przewidziany czas dostawy (min) dla BEST kuriera Ziomka (ostatni rekord).
    Strumieniowo (pliki ~170MB) — trzymamy tylko mały dict oid->float."""
    idx = {}
    for path in SHADOW_LOGS:
        for rec in _read_jsonl(path):
            oid = rec.get("order_id")
            if oid is None:
                continue
            ts = _parse_dt(rec.get("ts"))
            if ts is not None and ts < cutoff:
                continue
            best = rec.get("best") or {}
            plan = best.get("plan") or {}
            pm = (plan.get("per_order_delivery_times") or {}).get(str(oid))
            if _num(pm) is not None:
                idx[str(oid)] = pm  # chronologiczny → ostatni wygrywa
    return idx


def main():
    ap = argparse.ArgumentParser(description="Pomiar realnej jakości decyzji Ziomka (read-only).")
    ap.add_argument("--days", type=int, default=14, help="okno wstecz (dni) wg picked_up_at")
    ap.add_argument("--counterfactual", action="store_true", help="dolicz szacunek 'Ziomek vs człowiek' (skan shadow)")
    ap.add_argument("--emit", action="store_true", help="zapisz per-zlecenie do decision_outcomes.jsonl")
    args = ap.parse_args()

    now = datetime.now(WARSAW)
    cutoff = now - timedelta(days=args.days)

    def in_window(dtstr):
        dt = _parse_dt(dtstr)
        return dt is not None and dt >= cutoff

    print(f"[decision_outcome_join] {now.isoformat()}  okno={args.days}d (>= {cutoff.date()})")
    if not os.path.exists(ETA_CALIB):
        print(f"  ⚠ brak {ETA_CALIB} — uruchom najpierw eta_calibration_logger (timer dispatch-eta-calibration).")
    if not os.path.exists(SLA_LOG):
        print(f"  ⚠ brak {SLA_LOG}")

    # ---------- A. Realna jakość floty (sla_log, niezależna od Ziomka) ----------
    sla = [r for r in _read_jsonl(SLA_LOG) if in_window(r.get("picked_up_at") or r.get("delivered_at"))]
    sla_food = [r for r in sla if not r.get("was_czasowka")]
    dmins = [_num(r.get("delivery_time_minutes")) for r in sla_food]
    dmins = [d for d in dmins if d is not None]
    ontime = [r for r in sla_food if r.get("sla_ok") is True]
    sla_known = [r for r in sla_food if r.get("sla_ok") is not None]
    breach = [d for d in dmins if d > SLA_MIN]
    mean_d, med_d, p90_d, n_d = _stats(dmins)
    print("\n=== A. REALNA JAKOŚĆ FLOTY (sla_log, jedzeniówki, okno) ===")
    print(f"  dostaw (jedzeniówki): {len(sla_food)}  | z sla_ok: {len(sla_known)}  | czasówki pominięte: {len(sla)-len(sla_food)}")
    if n_d:
        print(f"  realny czas dostawy: mediana {med_d:.1f} min | p90 {p90_d:.1f} | śr {mean_d:.1f}")
        print(f"  ON-TIME (sla_ok=True): {len(ontime)}/{len(sla_known)} = {_pct(len(ontime),len(sla_known)):.1f}%")
        print(f"  R6 REALNY breach (>{SLA_MIN:.0f} min): {len(breach)}/{n_d} = {_pct(len(breach),n_d):.1f}%")

    # ---------- B–D. Join Ziomek <-> realny wynik (eta_calibration_log) ----------
    cal = [r for r in _read_jsonl(ETA_CALIB) if in_window(r.get("picked_up_at"))]
    cal_food = [r for r in cal if not r.get("was_czasowka")]

    def is_agree(r):
        b, rl = _cid(r.get("best_courier_id")), _cid(r.get("real_courier_id"))
        return bool(b and rl and b == rl)

    agree = [r for r in cal_food if is_agree(r)]
    disagree = [r for r in cal_food if r.get("best_courier_id") and r.get("real_courier_id") and not is_agree(r)]
    print("\n=== B. ZGODNOŚĆ ZIOMEK↔CZŁOWIEK (informacyjne; override≠jakość) ===")
    den_bd = len(agree) + len(disagree)
    print(f"  zlecenia z porównywalnym pickiem: {den_bd}")
    print(f"  best==real (człowiek wziął #1 Ziomka): {len(agree)} = {_pct(len(agree),den_bd):.1f}%")
    print(f"  best!=real (człowiek wybrał innego):  {len(disagree)} = {_pct(len(disagree),den_bd):.1f}%")

    print("\n=== C. JAKOŚĆ GDY WZIĘTO ZIOMKA vs GDY NADPISANO (realny on-time) ===")
    ag_k = [r for r in agree if r.get("sla_ok") is not None]
    dis_k = [r for r in disagree if r.get("sla_ok") is not None]
    ag_ok = [r for r in ag_k if r.get("sla_ok") is True]
    dis_ok = [r for r in dis_k if r.get("sla_ok") is True]
    print(f"  wzięto Ziomka:  on-time {len(ag_ok)}/{len(ag_k)} = {_pct(len(ag_ok),len(ag_k)):.1f}%")
    print(f"  nadpisano:      on-time {len(dis_ok)}/{len(dis_k)} = {_pct(len(dis_ok),len(dis_k)):.1f}%")
    print("  (uwaga: efekt selekcji — człowiek nadpisuje też trudniejsze przypadki)")

    print("\n=== D. KALIBRACJA PREDYKCJI ZIOMKA (kurier realny, matched) ===")
    matched = [r for r in cal_food if r.get("matched_courier")]
    eta_err = [_num(r.get("eta_error_min")) for r in matched]
    eta_err = [e for e in eta_err if e is not None]
    me, mede, p90e, ne = _stats(eta_err)
    if ne:
        print(f"  eta_error (real − obiecane), n={ne}: mediana {mede:+.1f} min | p90 {p90e:+.1f} | śr {me:+.1f}  [dodatni = za późno]")
    # fałszywy optymizm: obiecane on-time ale realnie spóźnione
    fo = [r for r in matched if (_num(r.get("predicted_delivery_min")) or 99) <= SLA_MIN and (_num(r.get("real_delivery_min")) or 0) > SLA_MIN]
    den_fo = [r for r in matched if _num(r.get("predicted_delivery_min")) is not None and _num(r.get("real_delivery_min")) is not None]
    print(f"  fałszywy optymizm (obiecane ≤35, realnie >35): {len(fo)}/{len(den_fo)} = {_pct(len(fo),len(den_fo)):.1f}%")

    # ---------- E. Kontrfaktyk (szacunek) ----------
    if args.counterfactual:
        print("\n=== E. KONTRFAKTYK: ZIOMEK vs CZŁOWIEK (SZACUNEK — predykcja debias vs realizacja) ===")
        print("  ⚠ kurier Ziomka NIE pojechał → porównujemy debias-predykcję Ziomka (pred+~9 min poślizgu) z realnym wynikiem człowieka.")
        idx = _shadow_best_pred_index(cutoff)
        better = worse = tie = 0
        z_est, h_real = [], []
        for r in disagree:
            oid = str(r.get("oid"))
            bp = _num(idx.get(oid))
            hr = _num(r.get("real_delivery_min"))
            if bp is None or hr is None:
                continue
            zi = bp + PICKUP_SLIP_DEBIAS_MIN
            z_est.append(zi)
            h_real.append(hr)
            if zi < hr - 2:
                better += 1
            elif zi > hr + 2:
                worse += 1
            else:
                tie += 1
        den_e = better + worse + tie
        if den_e:
            _, zmed, _, _ = _stats(z_est)
            _, hmed, _, _ = _stats(h_real)
            print(f"  porównywalnych zleceń (człowiek≠Ziomek, znane oba): {den_e}")
            print(f"  mediana: Ziomek-szac {zmed:.1f} min vs człowiek-real {hmed:.1f} min")
            print(f"  Ziomek prawdopodobnie SZYBSZY: {better} ({_pct(better,den_e):.0f}%) | wolniejszy: {worse} ({_pct(worse,den_e):.0f}%) | ~równo: {tie} ({_pct(tie,den_e):.0f}%)")
            print("  → interpretacja ostrożna: to NIE dowód, że Ziomek był lepszy — to szacunek przy znanym optymizmie predykcji.")
        else:
            print("  brak porównywalnych zleceń (predykcja best niedostępna w shadow w oknie).")

    # ---------- emit per-order ----------
    if args.emit:
        rows = []
        idx2 = {}
        if args.counterfactual:
            idx2 = _shadow_best_pred_index(cutoff)
        for r in cal_food:
            rows.append({
                "oid": r.get("oid"),
                "picked_up_at": r.get("picked_up_at"),
                "ziomek_best_cid": _cid(r.get("best_courier_id")),
                "real_cid": _cid(r.get("real_courier_id")),
                "agreement": is_agree(r),
                "real_delivery_min": _num(r.get("real_delivery_min")),
                "real_ontime": r.get("sla_ok"),
                "real_r6_breach": (_num(r.get("real_delivery_min")) or 0) > SLA_MIN,
                "ziomek_pred_delivery_min": _num(idx2.get(str(r.get("oid")))) if idx2 else _num(r.get("predicted_delivery_min")),
                "eta_error_min": _num(r.get("eta_error_min")),
                "verdict": r.get("verdict"),
                "is_bundle": r.get("is_bundle"),
                "bucket": r.get("bucket"),
            })
        os.makedirs(os.path.dirname(OUT), exist_ok=True)
        tmp = OUT + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            for x in rows:
                fh.write(json.dumps(x, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, OUT)
        print(f"\n  zapisano {len(rows)} wierszy → {OUT}")

    print("\n  (read-only; zero wpływu na decyzje/stan)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
