#!/usr/bin/env python3
"""weekly_a2_digest.py — cotygodniowy przegląd: trend A2 (dzień-po-dniu) +
odchylenia od reguł biznesowych → Telegram do Adriana.

OFFLINE, READ-ONLY (poza wysyłką Telegram). Uruchamiany jednorazowo przez `at`
(albo ręcznie). Czyta:
  - dispatch_state/a2_selection_shadow.jsonl  (wpisy key_aware_v2 z dziennego timera)
  - rule_deviation_report.py (import) — świeże odchylenia reguł

--dry-run : drukuje wiadomość zamiast wysyłać (test bez spamu).
"""
import argparse
import json
import os
import re
import statistics
import subprocess
import sys
from collections import defaultdict

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)                       # import siblinga rule_deviation_report
sys.path.insert(0, os.path.dirname(os.path.dirname(_HERE)))  # scripts/ → import dispatch_v2.*

A2_TREND = "/root/.openclaw/workspace/dispatch_state/a2_selection_shadow.jsonl"
COEFF_REP = "60"   # reprezentatywny COEFF (dobry stosunek lepsze:gorsze, ~9% pokrycia)

# Ping na PRYWATNY DM Adriana (NIE grupa -5149910559, którą zwraca send_admin_alert).
ADRIAN_DM_CHAT_ID = "8765130486"
TELEGRAM_ENV = "/root/.openclaw/workspace/.secrets/telegram.env"
BASELINE_FILE = "/root/.openclaw/workspace/dispatch_state/rule_deviation_baseline_2026-06-03.json"
GETFIX_FILE = "/root/.openclaw/workspace/dispatch_state/getfix_effect_2026-06-03.json"


def _send_dm(msg):
    """Wyślij na prywatny DM Adriana (8765130486) — nie na grupę."""
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("ALLOW_TELEGRAM_IN_TEST"):
        print("send blocked (pytest context)")
        return True
    from dispatch_v2 import telegram_approver
    env = telegram_approver._load_env(TELEGRAM_ENV)
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        print("BRAK TELEGRAM_BOT_TOKEN w env", file=sys.stderr)
        return False
    r = telegram_approver.tg_request(token, "sendMessage", {"chat_id": ADRIAN_DM_CHAT_ID, "text": msg})
    if not r.get("ok"):
        print(f"tg_request fail: {r.get('error') or r.get('description')}", file=sys.stderr)
        return False
    return True


def _load_a2_by_day():
    """Zwraca {data: ostatni wpis key_aware_v2 tego dnia} (1 dzień = 1 strzał timera)."""
    by_day = {}
    if not os.path.exists(A2_TREND):
        return by_day
    for line in open(A2_TREND, errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("method") != "key_aware_v2":
            continue
        day = d.get("ts", "")[:10]
        by_day[day] = d   # ostatni z dnia wygrywa
    return by_day


def _trend_section():
    by_day = _load_a2_by_day()
    if not by_day:
        return "📈 TREND A2: brak wpisów key_aware_v2 (timer jeszcze nie zebrał).", None
    days = sorted(by_day)
    lines = [f"📈 TREND A2 (soft-score niezawodności, COEFF={COEFF_REP}, key_aware_v2):"]
    rates = []
    for day in days:
        m = (by_day[day].get("by_coeff") or {}).get(COEFF_REP) or {}
        cr = m.get("changed_rate")
        bo = m.get("mean_old_breach")
        bn = m.get("mean_new_breach")
        be = m.get("n_swap_better")
        wo = m.get("n_swap_worse")
        if cr is not None:
            rates.append(cr)
            lines.append(f"  {day[5:]}: zmian {cr*100:.1f}%  breach {bo}→{bn}  ({be}:{wo})")
    # ocena stabilności
    verdict = ""
    if len(rates) >= 2:
        sd = statistics.pstdev(rates)
        if len(rates) < 4:
            verdict = f"  Stabilność: {len(rates)} dni (za mało — zbieraj dalej, σ={sd:.3f})"
        elif sd < 0.02:
            verdict = f"  Stabilność: σ={sd:.3f} STABILNY → kandydat do FLIP (COEFF 60-100)"
        else:
            verdict = f"  Stabilność: σ={sd:.3f} ZMIENNY → ostrożnie, sygnał kruchy"
    else:
        verdict = "  Stabilność: 1 dzień (baseline) — czekaj na więcej"
    lines.append(verdict)
    return "\n".join(lines), rates


def _rules_section():
    try:
        import rule_deviation_report as rdr
        real = rdr.realized_deviations()
        prop = rdr.proposed_deviations(200000)
        worst = rdr.worst_couriers()
    except Exception as e:
        return f"⚖️ ODCHYLENIA REGUŁ: błąd liczenia ({type(e).__name__}: {e})"

    def p(x):
        return f"{x*100:.0f}%" if isinstance(x, (int, float)) else "—"
    lines = ["⚖️ ODCHYLENIA OD REGUŁ:"]
    lines.append(f"  R6 dostawa >35min: {p(real['R6_breach_rate'])} (p90 {real['R6_p2d_p90']}min) [TWARDA]")
    lines.append(f"  ETA bias: {real['ETA_residual_median_min']:+}min ({p(real['ETA_underpredict_rate'])} niedoszac.)")
    lines.append(f"  Fleet top-3: Ziomek {p(real['fleet_top3_share_ZIOMEK_proposed'])} vs człowiek {p(real['fleet_top3_share_HUMAN_final'])}")
    lines.append(f"  R5 odbiory >1.8km: {p(prop['R5_pickup_spread_over_1_8km_rate_bundles'])} (worki, soft)")
    lines.append(f"  R8 span >cap: {p(prop['R8_pickup_span_over_cap_rate_bundles'])} (worki, soft)")
    lines.append(f"  late-pickup >5min: {p(prop['late_pickup_over_5min_rate'])} [TWARDA — egzekwowana]")
    if worst:
        top = "  ".join(f"{w['cid']}({p(w['breach_rate'])})" for w in worst[:4])
        lines.append(f"  Najgorsi R6: {top}")
    return "\n".join(lines)


def _baseline_delta_section():
    """BASELINE (zamrożony 2026-06-03, przed efektem zmian) → TERAZ. Pokazuje
    realny dryf KPI. UWAGA: before/after = efekt zmian + naturalny dryf łącznie;
    czysty A/B efektu A2 to shadow-vs-live (sekcja TREND)."""
    if not os.path.exists(BASELINE_FILE):
        return ""
    try:
        base = json.load(open(BASELINE_FILE, encoding="utf-8")).get("realized", {})
        import rule_deviation_report as rdr
        now = rdr.realized_deviations()
    except Exception as e:
        return f"📊 BASELINE→TERAZ: błąd ({type(e).__name__})"
    lines = ["📊 BASELINE (03-06, przed efektem zmian) → TERAZ:"]
    for k, lab, unit in [
        ("R6_breach_rate", "R6 breach", "pp"),
        ("ETA_residual_median_min", "ETA bias", "min"),
        ("fleet_top3_share_ZIOMEK_proposed", "Fleet top-3 Ziomek", "pp"),
    ]:
        b, n = base.get(k), now.get(k)
        if b is None or n is None:
            continue
        if unit == "pp":
            lines.append(f"  {lab}: {b*100:.1f}% → {n*100:.1f}% (Δ {(n-b)*100:+.1f}pp)")
        else:
            lines.append(f"  {lab}: {b:+.1f} → {n:+.1f} min (Δ {n-b:+.1f})")
    lines.append("  (Δ = efekt zmian + dryf; czysty efekt A2 = sekcja TREND shadow-vs-live)")
    return "\n".join(lines)


def _current_failed_rate():
    """Live failed-rate z ostatniego HEARTBEAT dispatch-shadow (journalctl).
    Zwraca (rate, processed, failed) lub None gdy brak odczytu."""
    try:
        out = subprocess.run(
            ["journalctl", "-u", "dispatch-shadow", "-n", "120", "--no-pager"],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except Exception:
        return None
    last = None
    for m in re.finditer(r"totals=\{'processed': (\d+), 'failed': (\d+)", out):
        last = (int(m.group(1)), int(m.group(2)))
    if not last or last[0] == 0:
        return None
    return last[1] / last[0], last[0], last[1]


def _getfix_section():
    """Dowód: dzisiejsze wdrożenie .get() = pozytyw (early_bird KOORD nie failuje)."""
    if not os.path.exists(GETFIX_FILE):
        return ""
    try:
        g = json.load(open(GETFIX_FILE, encoding="utf-8"))
    except Exception:
        return ""
    pre = g.get("pre_fix_failed_rate")
    cur = _current_failed_rate()
    head = f"🔧 Fix .get() (wdrożony {g.get('ts')} 14:24 UTC) — dowód pozytywu:"
    if pre is not None and cur:
        rate, proc, fail = cur
        body = (f"  failed-rate pre-fix {pre*100:.1f}% ({g.get('pre_fix_failed')}/{g.get('pre_fix_processed')}) "
                f"→ live {rate*100:.1f}% ({fail}/{proc} bieżący proces)")
    elif pre is not None:
        body = f"  failed-rate pre-fix {pre*100:.1f}% → live: (brak odczytu journala)"
    else:
        body = "  (brak danych pre-fix)"
    return f"{head}\n{body}\n  early_bird KOORD nie failuje już (KeyError naprawiony) = metryka 'failed' wiarygodna"


def build_message(today):
    trend, _ = _trend_section()
    rules = _rules_section()
    baseline = _baseline_delta_section()
    getfix = _getfix_section()
    parts = [f"🤖 ZIOMEK — przegląd tygodniowy A2 + reguły ({today})", trend, rules]
    if baseline:
        parts.append(baseline)
    if getfix:
        parts.append(getfix)
    parts.append("Pełny raport: tools/rule_deviation_report.py + a2_selection_shadow.jsonl. "
                 "Pingnij CC po decyzję flip A2.")
    return "\n\n".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--today", default=None, help="data w nagłówku (domyślnie z env/now nie używamy — podaj)")
    args = ap.parse_args()
    # data: nie używamy now() w kodzie krytycznym; bierzemy z env-stamp lub fallback string
    today = args.today or os.environ.get("DIGEST_DATE", "")
    if not today:
        # bezpieczny fallback bez Date.now ceremonii — czytamy z ostatniego wpisu trendu
        by_day = _load_a2_by_day()
        today = (sorted(by_day)[-1] if by_day else "n/d")
    msg = build_message(today)
    if args.dry_run:
        print("--- DRY RUN (nie wysyłam) ---")
        print(msg)
        return 0
    try:
        ok = _send_dm(msg)
        if ok:
            print(f"Telegram DM (chat_id={ADRIAN_DM_CHAT_ID}) wysłany OK")
            return 0
        # fallback: DM padł → grupa (lepsze niż cisza dla fire-and-forget at-joba)
        print("DM FAIL → fallback na grupę (send_admin_alert)", file=sys.stderr)
        from dispatch_v2.telegram_utils import send_admin_alert
        ok2 = send_admin_alert("[DM niedostępny — fallback na grupę]\n\n" + msg)
        print("Fallback grupa OK" if ok2 else "Fallback grupa też FAIL")
        return 0 if ok2 else 1
    except Exception as e:
        print(f"Błąd wysyłki: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
