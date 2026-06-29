#!/usr/bin/env python3
"""Samodzielny monitor regresji propozycji po deployu #3 (pre-shift cap 60 + kara
gradientowa, dispatch-shadow restart 2026-06-24 11:43 UTC). Niezależny od sesji CC —
odpalany przez `at` joby w peaku obiadowym (15-18 UTC = 17-20 Warsaw). READ-ONLY na
produkcji; pisze tylko własny stan/log + alert Telegram (send_admin_alert) PRZY REGRESJI.

Tryby:
  (domyślny)  check okna 35 min → alert Telegram tylko gdy VERDICT∈{REGRESJA,WATCH} (cooldown 30 min / eskalacja)
  --final     zawsze wyślij podsumowanie peaku (werdykt + liczby)
  --armed     jednorazowy komunikat „monitor uzbrojony" (test ścieżki Telegram)

Baseline lunch 24.06 pre-deploy: KOORD 10.5%, err 0, BRAK_KAND 0. Serwer = UTC.
Rollback (hot-reload, ~5s): ENABLE_PRE_SHIFT_GRADIENT_PENALTY=0 / PRE_SHIFT_WINDOW_MAX_MIN=9999 w flags.json.
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/root/.openclaw/workspace/scripts")
WS = "/root/.openclaw/workspace"
SLOG = f"{WS}/scripts/logs/shadow.log"
WLOG = f"{WS}/scripts/logs/watcher.log"
STATE = f"{WS}/dispatch_state/peak_monitor_2026-06-24_state.json"
JLOG = f"{WS}/dispatch_state/peak_monitor_2026-06-24.jsonl"
WIN_MIN = 35
COOLDOWN_MIN = 30
BASELINE_KOORD = 10.5


def _tg(text):
    try:
        from dispatch_v2 import telegram_utils
        return telegram_utils.send_admin_alert(text, source="preshift_peak_monitor", priority="high")
    except Exception as e:  # nigdy nie wywalaj monitora przez błąd wysyłki
        print(f"[tg-fail] {type(e).__name__}: {e}", file=sys.stderr)
        return False


def _journal(units, since_utc):
    try:
        args = ["journalctl"] + sum([["-u", u] for u in units], []) + \
               ["--since", since_utc, "--no-pager"]
        return subprocess.run(args, capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return ""


def _count_log(path, cut16, needles):
    c = {k: 0 for k in needles}
    try:
        with open(path, errors="ignore") as f:
            for l in f:
                if len(l) < 16 or l[:4] != "2026" or l[:16] < cut16:
                    continue
                low = l.lower()
                for k, pat in needles.items():
                    if pat in (l if k == "OVR" else low):
                        c[k] += 1
    except FileNotFoundError:
        pass
    return c


def measure():
    now = datetime.now(timezone.utc)
    cut = now - timedelta(minutes=WIN_MIN)
    cut_s = cut.strftime("%Y-%m-%d %H:%M:%S")
    cut16 = cut_s[:16]
    sc = _count_log(SLOG, cut16, {"prop": "→ propose", "koord": "→ koord",
                                  "err": "traceback"})
    wc = _count_log(WLOG, cut16, {"OVR": "PANEL_OVERRIDE"})
    j = _journal(["dispatch-shadow.service"], cut_s)
    jp = _journal(["dispatch-shadow.service", "dispatch-panel-watcher.service"], cut_s)
    jerr = len(re.findall(r"traceback|exception|critical", j, re.I))
    brak = len(re.findall(r"brak kandydat|no_candidate|BRAK_KAND", j, re.I))
    preshift = len(re.findall(r"off_shift_or_window|pre_shift_window_cap", jp, re.I))
    lg = re.findall(r"loadgov_load_ewma['\":= ]+([0-9.]+)", j)
    tot = sc["prop"] + sc["koord"]
    kr = round(100 * sc["koord"] / tot, 1) if tot else None
    verdict = "OK"
    if jerr > 0:
        verdict = f"REGRESJA(błędy={jerr})"
    elif brak >= 3:
        verdict = f"REGRESJA(brak_kand={brak})"
    elif tot >= 12 and kr is not None and kr >= 25 and sc["koord"] >= 4:
        verdict = f"REGRESJA(KOORD {kr}% n={tot})"
    elif tot >= 12 and kr is not None and kr >= 18:
        verdict = f"WATCH(KOORD {kr}%)"
    return {
        "ts": now.isoformat(), "win_min": WIN_MIN, "verdict": verdict,
        "propose": sc["prop"], "koord": sc["koord"], "koord_pct": kr,
        "err_journal": jerr, "brak_kand": brak, "panel_override": wc["OVR"],
        "preshift_cap_skip": preshift, "loadgov_ewma": (lg[-1] if lg else None),
    }


def _load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {"last_alert_ts": None, "last_verdict": "OK"}


def _save_state(s):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f)
    os.replace(tmp, STATE)


def _log(rec):
    try:
        with open(JLOG, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _fmt(m, head):
    return (f"{head}\n"
            f"PROPOSE={m['propose']} KOORD={m['koord']} ({m['koord_pct']}% ; baseline {BASELINE_KOORD})\n"
            f"błędy={m['err_journal']} BRAK_KAND={m['brak_kand']} override={m['panel_override']} "
            f"preshift_cap_skip={m['preshift_cap_skip']} loadgov_ewma={m['loadgov_ewma']}\n"
            f"(okno {m['win_min']} min, {m['ts'][11:16]} UTC)")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "--armed":
        ok = _tg("🟢 [Ziomek #3 monitor] Monitor regresji propozycji UZBROJONY na peak 17-20. "
                 "Alarm tylko przy regresji; podsumowanie po peaku. "
                 "Rollback: ENABLE_PRE_SHIFT_GRADIENT_PENALTY=0 / PRE_SHIFT_WINDOW_MAX_MIN=9999.")
        print("armed sent:", ok)
        return
    m = measure()
    _log(m)
    st = _load_state()
    now = datetime.now(timezone.utc)
    if mode == "--final":
        icon = "🟢" if m["verdict"] == "OK" else ("🟡" if m["verdict"].startswith("WATCH") else "🔴")
        _tg(_fmt(m, f"{icon} [Ziomek #3 monitor] PEAK ZAKOŃCZONY — werdykt: {m['verdict']}"))
        print("final:", m["verdict"])
        return
    reg = m["verdict"].startswith("REGRESJA")
    watch = m["verdict"].startswith("WATCH")
    if reg or watch:
        escalated = (m["verdict"] != st.get("last_verdict")) and not (
            watch and str(st.get("last_verdict", "")).startswith("REGRESJA"))
        last = st.get("last_alert_ts")
        cooled = (last is None) or (
            (now - datetime.fromisoformat(last)).total_seconds() / 60.0 >= COOLDOWN_MIN)
        if escalated or cooled:
            icon = "🔴" if reg else "🟡"
            tip = ("\nRollback (hot-reload ~5s): ENABLE_PRE_SHIFT_GRADIENT_PENALTY=0 lub "
                   "PRE_SHIFT_WINDOW_MAX_MIN=9999 w flags.json." if reg else "")
            _tg(_fmt(m, f"{icon} [Ziomek #3 monitor] {m['verdict']}") + tip)
            st["last_alert_ts"] = now.isoformat()
    st["last_verdict"] = m["verdict"]
    _save_state(st)
    print(m["verdict"], m["propose"], m["koord"], m["koord_pct"])


if __name__ == "__main__":
    main()
