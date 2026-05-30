#!/usr/bin/env python3
"""Werdykt +7d sprintu OBJ FRESH (świeżość odbioru) → Telegram digest.

Odpalany przez `at` ~2026-06-06. Liczy ogon luzu odbioru (a)=projected_pickup-ready
dla okna POST-flip (--since flip 2026-05-30T19:13:00+00:00) i porównuje z baseline
pre-flip (cała historia mierzona przed flipem: >5min=30.6%, >10min=17.5%). Dorzuca
sanity kosztu jazdy (mean total_duration_min pre vs post) jako guard "czy kara nie
nadłożyła deadheadu". Buduje rekomendację ON/OFF i wysyła JEDEN digest na Telegram.

Kryterium "było warto" (z OBJ_FRESH_baseline_and_deploy.md): ogon >10min spada
wyraźnie (cel <12%) BEZ wzrostu kosztu jazdy. Read-only — nie dotyka prod/flag.
"""
import argparse
import importlib.util
import os
import statistics as st
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

SCRIPTS_DIR = "/root/.openclaw/workspace/scripts"
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

MEASURE_PATH = (
    "/root/.openclaw/workspace/scripts/dispatch_v2/"
    "eod_drafts/2026-05-30/measure_pickup_freshness_tail.py"
)
SHADOW = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
REPORT = (
    "/root/.openclaw/workspace/scripts/dispatch_v2/"
    "eod_drafts/2026-05-30/obj_fresh_verdict.md"
)
FLIP_ISO = "2026-05-30T19:13:00+00:00"

# Baseline pre-flip (z OBJ_FRESH_baseline_and_deploy.md, n=1632 food-only).
BASE_GT5 = 30.6
BASE_GT10 = 17.5
BASE_GT15 = 9.9
TARGET_GT10 = 12.0  # cel "było warto"

WARSAW = ZoneInfo("Europe/Warsaw")
UTC = ZoneInfo("UTC")


def _load_measure():
    spec = importlib.util.spec_from_file_location("measure_pf", MEASURE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def collect(M, since=None, until=None):
    """Zwraca dict: slack list (a) + lista total_duration_min (drive-cost proxy)."""
    import json

    slack, durations = [], []
    n_firm = n_noproj = 0
    with open(SHADOW) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            ts = M.parse_aware(rec.get("ts"))
            if since and (ts is None or ts < since):
                continue
            if until and (ts is None or ts >= until):
                continue
            oid = str(rec.get("order_id") or "")
            if M.is_firm(rec.get("restaurant")):
                n_firm += 1
                continue
            plan = (rec.get("best") or {}).get("plan") or {}
            proj = M.parse_aware((plan.get("pickup_at") or {}).get(oid))
            ready = M.parse_aware(rec.get("pickup_ready_at"))
            if proj is None or ready is None:
                n_noproj += 1
                continue
            slack.append((proj - ready).total_seconds() / 60.0)
            dur = plan.get("total_duration_min")
            if isinstance(dur, (int, float)):
                durations.append(float(dur))
    return {
        "slack": slack,
        "durations": durations,
        "n_firm": n_firm,
        "n_noproj": n_noproj,
    }


def tail(xs, thr):
    if not xs:
        return 0.0
    return sum(1 for x in xs if x > thr) / len(xs) * 100.0


def build_message():
    if not os.path.exists(MEASURE_PATH):
        return f"🔴 OBJ FRESH werdykt: BRAK skryptu pomiaru ({MEASURE_PATH})."
    if not os.path.exists(SHADOW):
        return f"🔴 OBJ FRESH werdykt: BRAK shadow logu ({SHADOW})."

    M = _load_measure()
    since = M.parse_aware(FLIP_ISO)
    post = collect(M, since=since)
    pre = collect(M, until=since)  # czyste okno pre-flip (drive-cost porównanie)

    xs = post["slack"]
    n = len(xs)
    if n == 0:
        return (
            "🟡 OBJ FRESH werdykt: 0 rekordów food-only w oknie post-flip "
            f"(since {FLIP_ISO}). Sprawdź czy dispatch-shadow produkuje plany."
        )

    g5, g10, g15 = tail(xs, 5), tail(xs, 10), tail(xs, 15)
    med = st.median(xs)
    chg10 = g10 - BASE_GT10  # ujemny = ogon spadł (dobrze)
    drop10 = -chg10          # dodatni = wielkość spadku

    # drive-cost sanity: mediana total_duration_min pre-flip vs post-flip
    # (mediana = odporna na outliery wieloorderowych planów; te same okna co ogon).
    post_dur, pre_dur = post["durations"], pre["durations"]
    if post_dur and pre_dur:
        pre_med = st.median(pre_dur)
        post_med = st.median(post_dur)
        delta = post_med - pre_med
        drive_flag = "OK" if delta <= 0.5 else "⚠ WZROST"
        drive_line = (
            f"koszt jazdy (mediana total_duration): pre {pre_med:.1f} → "
            f"post {post_med:.1f} min (Δ{delta:+.1f}) {drive_flag}"
        )
    else:
        drive_line = "koszt jazdy: brak danych total_duration_min do porównania"

    drive_ok = ("⚠" not in drive_line)
    if g10 < TARGET_GT10 and drive_ok:
        verdict, rec = "🟢", "ZOSTAW ON — kara świeżości się opłaca."
    elif g10 < BASE_GT10 - 2.0 and drive_ok:
        verdict, rec = (
            "🟡",
            f"ogon spada ({drop10:+.1f}pp) ale >cel {TARGET_GT10:.0f}% — "
            "rozważ podbicie COEFF lub niższy próg (env, bez redeploy).",
        )
    elif not drive_ok:
        verdict, rec = (
            "🟡",
            "ogon spada ale koszt jazdy wzrósł — zważ trade-off; ewentualnie "
            "obniż COEFF.",
        )
    else:
        verdict, rec = (
            "🔴",
            f"ogon NIE spadł istotnie ({drop10:+.1f}pp) — FLAGA OFF "
            "(usuń linię env + daemon-reload + restart dispatch-shadow).",
        )

    now_w = datetime.now(WARSAW).strftime("%Y-%m-%d %H:%M")
    parts = [
        f"{verdict} OBJ FRESH werdykt +7d ({now_w} Warsaw)",
        f"okno post-flip od {FLIP_ISO}, n={n} food-only "
        f"(firm_skip={post['n_firm']}, no_proj={post['n_noproj']})",
        f"ogon luzu odbioru >5min: {g5:.1f}% (baseline {BASE_GT5}%)",
        f"ogon >10min: {g10:.1f}% (baseline {BASE_GT10}%, cel <{TARGET_GT10:.0f}%) "
        f"Δ{chg10:+.1f}pp",
        f"ogon >15min: {g15:.1f}% (baseline {BASE_GT15}%)",
        f"mediana: {med:.1f} min",
        drive_line,
        f"REKOMENDACJA: {rec}",
    ]
    msg = "\n".join(parts)

    try:
        with open(REPORT, "w") as fh:
            fh.write("# OBJ FRESH — werdykt +7d (auto)\n\n" + msg + "\n")
    except Exception as e:  # noqa: BLE001
        msg += f"\n(report write FAIL: {type(e).__name__})"
    return msg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-telegram", action="store_true",
                    help="tylko policz + wypisz, NIE wysyłaj na Telegram (test)")
    args = ap.parse_args()

    msg = build_message()
    print(msg)
    if args.no_telegram:
        print("\n[telegram] SKIP (--no-telegram)")
        return
    try:
        from dispatch_v2 import telegram_utils as T

        ok = T.send_admin_alert(msg)
        print(f"\n[telegram] send_admin_alert ok={ok}")
    except Exception as e:  # noqa: BLE001
        print(f"\n[telegram] FAIL {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
