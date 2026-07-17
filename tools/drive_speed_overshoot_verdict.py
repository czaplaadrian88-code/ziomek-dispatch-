#!/usr/bin/env python3
"""Werdykt OVERSHOOT korekty drive-speed tier (LIVE 2026-06-26 ~17:25 UTC, flaga
ENABLE_DRIVE_SPEED_TIER_CORRECTION, gold 0.78/std+ 0.82/std 0.82, AGRESYWNY krok).

Ryzyko korekty: za mocno ściśnięte ETA → predykcja zbyt OPTYMISTYCZNA → kurier
dostarcza PÓŹNIEJ niż przewidziano (overshoot) + feasibility/R6 przepuszcza za
długi worek → realny breach/zimne jedzenie. Ten tool to sprawdza POMIAREM, nie
deklaracją: porównuje cohort ON (dostawy po flipie) vs baseline (przed) na:
  1) bias delivered_at − delivery_pred_last  (baseline ~ -4.7 min = pesymizm;
     CEL: ku 0; ALARM gdy istotnie DODATNI = overshoot/optymizm),
  2) % dostaw PÓŹNIEJ niż żywy ETA (late-vs-pred) ON vs baseline (ALARM gdy ↑),
  3) split per tier (tylko gold/std+/std dotknięte; slow/new = kontrola).

CLEAN → korekta trafna, zostaw. ALARM → cofnij ku 0.85/0.90 (flaga lub wartości).
Read-only. --notify => Telegram (send_admin_alert). Bez peak-blokady.
Powiązane: memory/drive-speed-tier-correction-2026-06-26.md, ziomek-change-protocol.
"""
import argparse
import json
import statistics as st
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

BASE = "/root/.openclaw/workspace"
CALIB = f"{BASE}/dispatch_state/ziomek_pred_calibration.jsonl"
TIERS = f"{BASE}/dispatch_state/courier_tiers.json"
OUT = f"{BASE}/dispatch_state/drive_speed_overshoot_verdict.txt"
# DST-safe CET/CEST — L2 audyt 2.0 (był fixed +2). `delivered_at` to naive Warsaw
# wall-clock → .replace(tzinfo=WARSAW) daje poprawny offset per data (zimą +1, latem +2).
# Żadnego fixed-offset fallbacku — to klasa bomb TZ (ratchet test_tz_zoneinfo).
WARSAW = ZoneInfo("Europe/Warsaw")
# Flip flagi ON (UTC). Override --flip.
FLIP_DEFAULT = "2026-06-26T17:25:22+00:00"
# #5 audyt 28.06: flaga ENABLE_DRIVE_SPEED_TIER_CORRECTION była ON tylko ~15 min (flip→rollback
# 17:40 UTC), potem OFF (dziś OFF). BEZ górnej granicy kohorta „ON" (delivered_at>=flip) łapała
# 98% dostaw PRZY FLADZE OFF (po rollbacku) → fałszywe CLEAN „korekta trafna". ON = [flip, flip_end);
# po flip_end flaga OFF → ani ON ani clean-baseline → POMIŃ. Gdy ON za małe = N/A, NIE CLEAN.
FLIP_END_DEFAULT = "2026-06-26T17:40:00+00:00"
AFFECTED = {"gold", "std+", "std"}
# Progi werdyktu (minuty / pkt proc.)
BIAS_ALARM_MIN = 2.0       # mediana delivered-vs-pred > +2 min ON = overshoot
LATE_FRAC_ALARM_PP = 12.0  # wzrost % late-vs-pred ON vs baseline > 12pp = ALARM


def _flag_on(name):
    """Efektywny stan flagi z flags.json (ta flaga jest hot-reload w flags.json, nie env-frozen)."""
    try:
        with open(f"{BASE}/scripts/flags.json") as f:
            return bool(json.load(f).get(name, False))
    except Exception:
        return False


def _p(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def _tier_of(tiers, cid):
    e = tiers.get(str(cid))
    return (e.get("bag") or {}).get("tier", "?") if isinstance(e, dict) else "?"


def _deliv_bias(r):
    """delivered_at − delivery_pred_last (min). + = później niż ETA (overshoot)."""
    a = _p(r.get("delivered_at"))
    p = _p(r.get("delivery_pred_last")) or _p(r.get("delivery_pred_assign"))
    if not a or not p:
        return None
    a = a.replace(tzinfo=WARSAW)  # actual naive = Warsaw
    return (a - p).total_seconds() / 60.0


def compute(flip_iso, flip_end_iso=None):
    flip = _p(flip_iso)
    flip_end = _p(flip_end_iso) if flip_end_iso else None
    tiers = json.load(open(TIERS))
    rows = []
    with open(CALIB) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    on, base = [], []
    for r in rows:
        d = _p(r.get("delivered_at"))
        if not d:
            continue
        d = d.replace(tzinfo=WARSAW)
        t = _tier_of(tiers, r.get("cid"))
        if t not in AFFECTED:
            continue
        bias = _deliv_bias(r)
        if bias is None:
            continue
        rec = {"tier": t, "bias": bias}
        # #5 audyt: ON = [flip, flip_end); po flip_end flaga OFF (rollback) → wyklucz (ani ON ani baseline)
        if d < flip:
            base.append(rec)
        elif flip_end is None or d < flip_end:
            on.append(rec)
        # else d >= flip_end: flaga cofnięta → POMIŃ (NIE wlicz do ON ani baseline)

    def agg(rs):
        b = [x["bias"] for x in rs]
        if not b:
            return {"n": 0}
        late = sum(1 for x in b if x > 0)
        return {
            "n": len(b),
            "bias_med": round(st.median(b), 1),
            "bias_mean": round(st.mean(b), 1),
            "late_frac_pp": round(100 * late / len(b), 1),
        }

    res = {"flip": flip_iso, "ON": agg(on), "baseline": agg(base)}
    res["per_tier_ON"] = {
        t: agg([x for x in on if x["tier"] == t]) for t in sorted(AFFECTED)
    }
    # #5 audyt 28.06: korekta NIE jest LIVE (flaga OFF/cofnięta) → werdykt BEZPRZEDMIOTOWY = N/A.
    # Bez tego „CLEAN" (na kohorcie delivered_at, której flaga w ogóle nie dotknęła) sugerowałby
    # przyszłej sesji „zostaw/wskrześ korektę" — a była świadomie cofnięta (mis-targeted; właściwy
    # lewar = dwell-parytet). delivered_at ≠ czas DECYZJI, więc nawet okno [flip,flip_end) to proxy
    # — definitywny werdykt wymaga per-decyzja stempla flagi; przy OFF i tak nie ma czego mierzyć.
    if not _flag_on("ENABLE_DRIVE_SPEED_TIER_CORRECTION"):
        res["verdict"] = "N/A"
        res["note"] = ("ENABLE_DRIVE_SPEED_TIER_CORRECTION = OFF (cofnieta 26.06 ~17:40 UTC) — korekta "
                       "NIE jest LIVE, nie ma czego ocenic. NIE wskrzeszac na podstawie tego werdyktu; "
                       "wlasciwy lewar = dwell-parytet PLAN_RECHECK_TIER_DWELL (LIVE).")
        return res
    # Werdykt
    o, bl = res["ON"], res["baseline"]
    alarms = []
    if o.get("n", 0) < 8:
        if flip_end is not None:
            verdict = "N/A"
            note = (f"flaga ON tylko [{flip_iso} .. {flip_end_iso}] (rollback po ~15 min, dzis OFF); "
                    f"kohorta ON ograniczona do okna = n={o.get('n',0)}<8 -> KOREKTY NIE DA SIE OCENIC. "
                    f"NIE czytac jako CLEAN/zostaw-korekte: byla swiadomie cofnieta (mis-targeted; "
                    f"wlasciwy lewar = dwell-parytet PLAN_RECHECK_TIER_DWELL).")
        else:
            verdict = "INCONCLUSIVE"
            note = f"za mała próba ON (n={o.get('n',0)}<8) — poczekaj na pełny peak"
    else:
        if o["bias_med"] > BIAS_ALARM_MIN:
            alarms.append(f"bias ON med={o['bias_med']:+}min > +{BIAS_ALARM_MIN} = OVERSHOOT (dostawy później niż ETA)")
        if bl.get("n", 0) >= 8 and (o["late_frac_pp"] - bl["late_frac_pp"]) > LATE_FRAC_ALARM_PP:
            alarms.append(f"late-vs-ETA wzrósł {bl['late_frac_pp']}%→{o['late_frac_pp']}% (+{round(o['late_frac_pp']-bl['late_frac_pp'],1)}pp)")
        verdict = "ALARM" if alarms else "CLEAN"
        note = "; ".join(alarms) if alarms else "bias siadł ku 0 bez wzrostu realnych spóźnień — korekta trafna"
    res["verdict"] = verdict
    res["note"] = note
    return res


def fmt(res):
    o, bl = res["ON"], res["baseline"]
    L = [f"🚦 DRIVE-SPEED OVERSHOOT WERDYKT: {res['verdict']}",
         f"flip ON: {res['flip']}",
         f"ON (po flipie):   n={o.get('n',0)} bias med={o.get('bias_med','?')} mean={o.get('bias_mean','?')} late-vs-ETA={o.get('late_frac_pp','?')}%",
         f"baseline (przed): n={bl.get('n',0)} bias med={bl.get('bias_med','?')} late-vs-ETA={bl.get('late_frac_pp','?')}%",
         "(bias + = dostawa PÓŹNIEJ niż ETA = overshoot; − = wcześniej = ok/zapas)"]
    for t, a in res["per_tier_ON"].items():
        L.append(f"  {t}: n={a.get('n',0)} bias med={a.get('bias_med','?')} late={a.get('late_frac_pp','?')}%")
    L.append(f"→ {res['note']}")
    if res["verdict"] == "ALARM":
        L.append("ROLLBACK/dial: ENABLE_DRIVE_SPEED_TIER_CORRECTION=false (hot) LUB DRIVE_SPEED_MULT_BY_TIER ku 0.85/0.90 + restart dispatch-shadow.")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--flip", default=FLIP_DEFAULT)
    ap.add_argument("--flip-end", default=FLIP_END_DEFAULT, dest="flip_end",
                    help="koniec okna ON (rollback flagi); po nim flaga OFF → wykluczone. Pusty=bez granicy.")
    ap.add_argument("--notify", action="store_true")
    a = ap.parse_args()
    res = compute(a.flip, a.flip_end or None)
    txt = fmt(res)
    print(txt)
    try:
        with open(OUT, "w", encoding="utf-8") as fh:
            fh.write(txt + "\n")
    except Exception as e:
        print(f"[warn] write OUT fail: {e}", file=sys.stderr)
    if a.notify:
        try:
            sys.path.insert(0, f"{BASE}/scripts")
            from dispatch_v2.telegram_utils import send_admin_alert
            send_admin_alert(txt, source="drive_speed_overshoot_verdict")
        except Exception as e:
            print(f"[warn] telegram fail: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
