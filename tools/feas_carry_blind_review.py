#!/usr/bin/env python3
"""Przeglad B2 / P-6 feas_carry_blind shadow (jednorazowy, plan 2026-06-28).

Czyta dispatch_state/feas_carry_blind_shadow.jsonl, liczy werdykt (would_redirect%,
regret med/mean/p90, marginal, redirect_kind) z FRESH (od 26.06) + CUMULATIVE,
porownuje do baseline 25.06 (178 rek, 38,2% redirect, regret med 8,4/mean 9,0,
16 marginal, r6_new 42/sla 26), rekomenduje fix-czy-nie. NIE buduje nic.
--notify => Telegram do Adriana (send_admin_alert). Read-only, bez peak-blokady.

Kontekst: root #483000 (bramka feasibility 'wybacza' najgorszy breach niesionego,
wycina lepszego kuriera). Fix (gdy ACK): unifikacja bramki carry-inclusive PRIMARY +
gradient + new-order cap wg eod_drafts/2026-06-23/SPEC_best_effort_carry_blind_r6.md,
PRZEZ protokol /root/.claude/projects/-root/memory/ziomek-change-protocol.md.
"""
import argparse
import json
import os
import statistics as st
import sys

SCRIPTS = "/root/.openclaw/workspace/scripts"
sys.path.insert(0, SCRIPTS)
LOG = "/root/.openclaw/workspace/dispatch_state/feas_carry_blind_shadow.jsonl"
REPORT_DIR = os.path.join(SCRIPTS, "dispatch_v2", "eod_drafts", "2026-06-28")


def _load():
    out = []
    try:
        for ln in open(LOG, encoding="utf-8", errors="ignore"):
            ln = ln.strip()
            if not ln:
                continue
            try:
                out.append(json.loads(ln))
            except Exception:
                pass
    except FileNotFoundError:
        pass
    return out


def _date(r):
    try:
        return str(r.get("ts", ""))[:10]
    except Exception:
        return "?"


def _q(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    i = min(len(s) - 1, int(round(p * (len(s) - 1))))
    return round(s[i], 1)


def _stats(recs):
    n = len(recs)
    red = [r for r in recs if r.get("would_redirect")]
    regr = [float(r["regret_min"]) for r in red if isinstance(r.get("regret_min"), (int, float))]
    marg = sum(1 for r in recs if r.get("marginal"))
    kinds = {}
    for r in red:
        k = r.get("redirect_kind", "?")
        kinds[k] = kinds.get(k, 0) + 1
    return dict(
        n=n, redirect=len(red),
        redirect_pct=round(100 * len(red) / max(1, n), 1),
        regret_med=round(st.median(regr), 1) if regr else 0.0,
        regret_mean=round(st.mean(regr), 1) if regr else 0.0,
        regret_p90=_q(regr, 0.9), marginal=marg, kinds=kinds,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--notify", action="store_true", help="wyslij werdykt na Telegram (Adrian)")
    a = ap.parse_args()

    recs = _load()
    fresh = [r for r in recs if _date(r) >= "2026-06-26"]
    cum, fr = _stats(recs), _stats(fresh)
    span = f"{_date(recs[0])}..{_date(recs[-1])}" if recs else "brak danych"

    judge = fr if fr["n"] >= 40 else cum  # werdykt na fresh gdy dosc danych
    if judge["redirect_pct"] >= 30 and judge["regret_mean"] >= 5:
        verdict = ("✅ SYGNAL TRZYMA -> REKOMENDUJE budowe fixu B2 (unifikacja bramki "
                   "feasibility: carry-inclusive PRIMARY + gradient + new-order cap) "
                   "PRZEZ protokol ziomek-change-protocol.md + Twoj ACK (shadow-first, "
                   "replay, pelna regresja).")
    elif judge["redirect_pct"] < 25:
        verdict = ("🔻 SYGNAL OSLABL (<25%) -> proponuje ZAMKNAC temat z liczbami "
                   "(nie budowac fixu).")
    else:
        verdict = "🟡 GRANICZNIE (25-30%) -> zbierac dalej / Twoja decyzja."

    msg = (
        f"🔎 Przeglad shadow B2 (feas_carry_blind / P-6) — okno {span}\n"
        f"Co sie dzieje: bramka feasibility 'wybacza' najgorszy breach niesionego i "
        f"wycina lepszego kuriera (root #483000); shadow mierzy jak czesto warto by "
        f"przekierowac.\n\n"
        f"FRESH (od 26.06, n={fr['n']}): redirect {fr['redirect_pct']}% | "
        f"regret med {fr['regret_med']}/sr {fr['regret_mean']}/p90 {fr['regret_p90']} min | "
        f"marginal {fr['marginal']} | {fr['kinds']}\n"
        f"CUMULATIVE (n={cum['n']}): redirect {cum['redirect_pct']}% | "
        f"regret sr {cum['regret_mean']} min | {cum['kinds']}\n"
        f"BASELINE 25.06 (n=178): redirect 38,2% | regret sr 9,0 min | r6_new 42 / sla 26\n\n"
        f"WERDYKT (na {'FRESH' if judge is fr else 'CUMULATIVE'}): {verdict}\n"
        f"Co robisz: jesli ✅ -> daj ACK na build (przez protokol). "
        f"Raport: eod_drafts/2026-06-28/feas_carry_blind_review.md"
    )
    print(msg)

    try:
        os.makedirs(REPORT_DIR, exist_ok=True)
        with open(os.path.join(REPORT_DIR, "feas_carry_blind_review.md"), "w", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception as e:
        print("[report write FAIL]", e)

    if a.notify:
        try:
            from dispatch_v2.telegram_utils import send_admin_alert
            send_admin_alert(msg, source="feas_carry_blind_review")
            print("[telegram OK]")
        except Exception as e:
            print("[telegram FAIL]", e)


if __name__ == "__main__":
    main()
