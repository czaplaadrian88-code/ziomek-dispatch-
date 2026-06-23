#!/usr/bin/env python3
"""One-shot przegląd flipu carried-age-tzfix — po lunch-peaku (zaplanowany 2026-06-24).

READ-ONLY. Sprawdza czy fix (ENABLE_CARRIED_AGE_TZ_FIX, commit 10ae264) działa:
  1) logger ziomek_pred_calibration --summary (rozjazdy odbiór+dostawa × assign/last × solo/bundle),
  2) bundle DOSTAWA PRED vs real DZIŚ (eta_calibration_log) vs referencja 17-23.06 (+6,3 → cel spada),
  3) carried-parking w żywych planach (cel ~0; przed fixem replay 57%),
  4) zdrowie serwisów + błędy.
Werdykt GO/NO-GO → raport Telegram (DM admina). `--dry` = tylko stdout, bez Telegrama.

Kontekst: memory/console-app-time-route-divergence-2026-06-23.md. Rollback gdy NO-GO:
rm /etc/systemd/system/dispatch-{plan-recheck,panel-watcher}.service.d/carried-age-tzfix.conf
+ daemon-reload + systemctl restart dispatch-panel-watcher.
"""
import json, os, sys, subprocess, urllib.request, urllib.parse
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

WAW = ZoneInfo("Europe/Warsaw")
STATE = "/root/.openclaw/workspace/dispatch_state"
PY = "/root/.openclaw/venvs/dispatch/bin/python"
LOGGER = "/root/.openclaw/workspace/scripts/dispatch_v2/tools/ziomek_pred_calibration.py"
TELEGRAM_ENV = "/root/.openclaw/workspace/.secrets/telegram.env"
DRY = "--dry" in sys.argv


def _utc(s):
    try:
        d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except Exception:
        return None


def _naive_waw(s):
    try:
        d = datetime.fromisoformat(str(s).strip())
        return (d.replace(tzinfo=WAW) if d.tzinfo is None else d).astimezone(timezone.utc)
    except Exception:
        return None


def _med(xs):
    xs = sorted(v for v in xs if v is not None)
    if not xs:
        return None
    n = len(xs)
    return xs[n // 2] if n % 2 else round((xs[n // 2 - 1] + xs[n // 2]) / 2, 1)


def calibration_summary():
    try:
        r = subprocess.run([PY, LOGGER, "--summary"], capture_output=True, text=True, timeout=60)
        return r.stdout.strip() or "(logger --summary: brak wyjścia)"
    except Exception as e:
        return f"(logger --summary błąd: {e})"


def bundle_today():
    """Bundle/solo DOSTAWA PRED vs real DZIŚ z eta_calibration_log."""
    today = datetime.now(WAW).strftime("%Y-%m-%d")
    R = {"solo": [], "bundle": []}
    P = {"solo": [], "bundle": []}
    try:
        for ln in open(f"{STATE}/eta_calibration_log.jsonl", encoding="utf-8"):
            try:
                r = json.loads(ln)
            except Exception:
                continue
            dl = _naive_waw(r.get("delivered_at"))
            if not dl or dl.astimezone(WAW).strftime("%Y-%m-%d") != today:
                continue
            rm, pm = r.get("real_delivery_min"), r.get("predicted_delivery_min")
            k = "solo" if (r.get("bag_size") or 1) <= 1 else "bundle"
            if rm is not None and 0 < rm <= 90:
                R[k].append(rm)
            if pm is not None and 0 < pm <= 90:
                P[k].append(pm)
    except FileNotFoundError:
        pass
    out = {}
    for k in ("solo", "bundle"):
        out[k] = (_med(P[k]), _med(R[k]), len(R[k]))
    return today, out


def carried_parking():
    """Aktywne plany z carried (picked_up) dostawą ZA nowym (assigned) odbiorem."""
    try:
        pl = json.load(open(f"{STATE}/courier_plans.json"))
        od = json.load(open(f"{STATE}/orders_state.json"))
    except Exception as e:
        return None, f"(błąd odczytu planów: {e})"
    def stt(o):
        return (od.get(str(o)) or {}).get("status")
    n_active = n_parked = 0
    detail = []
    for cid, p in pl.items():
        if not isinstance(p, dict) or p.get("invalidated_at"):
            continue
        n_active += 1
        seen = False
        parked = []
        for s in p.get("stops") or []:
            oid = str(s.get("order_id"))
            if s.get("type") == "pickup" and stt(oid) == "assigned":
                seen = True
            if s.get("type") == "dropoff" and stt(oid) == "picked_up" and seen:
                parked.append(oid)
        if parked:
            n_parked += 1
            detail.append(f"{cid}:{','.join(parked)}")
    return (n_active, n_parked), ("; ".join(detail) or "—")


def health():
    svc = ["dispatch-panel-watcher", "dispatch-plan-recheck.timer", "dispatch-ziomek-pred-calibration.timer"]
    st = {}
    for s in svc:
        try:
            st[s] = subprocess.run(["systemctl", "is-active", s], capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            st[s] = "?"
    try:
        j = subprocess.run(["journalctl", "-u", "dispatch-panel-watcher", "--since", "6 hours ago", "--no-pager"],
                           capture_output=True, text=True, timeout=20).stdout
        errs = sum(1 for ln in j.splitlines() if any(t in ln.lower() for t in ("error", "traceback", "carried_age")))
    except Exception:
        errs = -1
    return st, errs


def telegram(text):
    env = {}
    try:
        for line in open(TELEGRAM_ENV, encoding="utf-8"):
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k] = v.strip().strip('"').strip("'")
    except Exception:
        return False
    token, chat = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_ADMIN_ID")
    if not token or not chat:
        return False
    try:
        data = urllib.parse.urlencode({"chat_id": chat, "text": text, "disable_web_page_preview": "true"}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode()).get("ok", False)
    except Exception:
        return False


def main():
    summ = calibration_summary()
    day, bt = bundle_today()
    park, park_detail = carried_parking()
    st, errs = health()

    # werdykt
    bp, br, bn = bt["bundle"]
    bundle_diff = (bp - br) if (bp is not None and br is not None) else None
    flags_ok = (st.get("dispatch-panel-watcher") == "active")
    parking_ok = (park is not None and park[1] == 0)
    bundle_ok = (bundle_diff is not None and bundle_diff <= 4.0)  # cel: spadło z +6,3 ku ~0
    err_ok = (errs == 0)
    go = flags_ok and parking_ok and err_ok and (bundle_ok or bn < 20)
    verdict = "🟢 GO — fix trzyma" if go else "🔴 NO-GO / sprawdź"

    lines = [
        f"📊 Przegląd carried-age-tzfix ({datetime.now(WAW).strftime('%Y-%m-%d %H:%M')} W) — {verdict}",
        "",
        f"BUNDLE dostawa DZIŚ ({day}): PRED={bp} real={br} różnica={('%+.1f' % bundle_diff) if bundle_diff is not None else '—'} min (n={bn}) | ref 17-23.06 = +6,3 → cel spada",
        f"SOLO dostawa: PRED={bt['solo'][0]} real={bt['solo'][1]} (n={bt['solo'][2]})",
        f"carried-parking w planach: {park[1] if park else '?'}/{park[0] if park else '?'} aktywnych (cel 0; przed fixem 57%) {('['+park_detail+']') if park and park[1] else ''}",
        f"serwisy: panel-watcher={st.get('dispatch-panel-watcher')} plan-recheck.timer={st.get('dispatch-plan-recheck.timer')} kalibracja.timer={st.get('dispatch-ziomek-pred-calibration.timer')} | błędy 6h={errs}",
        "",
        "— logger --summary —",
        summ,
    ]
    if not go:
        lines += ["", "Rollback: rm /etc/systemd/system/dispatch-{plan-recheck,panel-watcher}.service.d/carried-age-tzfix.conf + daemon-reload + restart panel-watcher"]
    report = "\n".join(lines)
    print(report)
    if not DRY:
        ok = telegram(report)
        print(f"\n[telegram sent: {ok}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
