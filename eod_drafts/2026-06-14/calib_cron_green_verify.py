#!/usr/bin/env python3
"""Poranna weryfikacja: czy nocne ticki 3 naprawionych cronów kalibracji są zielone.

Kontekst: 14.06 naprawiono brakujący `cd .../scripts &&` w cronach calib (l.51/52/53),
logi zresetowano (marker "# 2026-06-14 ... log zresetowany"). Crony fire'ują w UTC
(CRON_TZ=Europe/Warsaw NIE honorowane w Vixie cron): restaurant_prep_bias 04:15 /
eta_quantile_calib 04:35 / czasowka_state_cleanup 04:45. Ten skrypt sprawdza PO nich.

Zielone = żaden log NIE ma błędu importu/traceback PO markerze + mapa ETA mtime = dziś.
Dry-run (print, bez Telegrama): CRON_VERIFY_DRY=1 venv python <ten plik>
Uruchamiać z katalogu scripts/ (jak inne notify): cd .../scripts && venv python -m ...
"""
import os
from datetime import datetime, timezone, date

LOG_DIR = "/root/.openclaw/workspace/scripts/logs"
ETA_MAP = "/root/.openclaw/workspace/dispatch_state/eta_quantile_map.json"
CRON_LOGS = {
    "restaurant_prep_bias": "restaurant_prep_bias_cron.log",
    "eta_quantile_calib": "eta_quantile_calib_cron.log",
    "czasowka_state_cleanup": "czasowka_state_cleanup_cron.log",
}
ERROR_PATTERNS = (
    "ModuleNotFoundError",
    "No module named",
    "Traceback (most recent call last)",
    "Error while finding module",
)


def _after_marker(text):
    """Zwróć treść logu PO ostatniej linii-znaczniku resetu (lub cały, gdy brak)."""
    lines = text.splitlines()
    last_marker = -1
    for i, ln in enumerate(lines):
        if ln.startswith("#") and "zresetowany" in ln:
            last_marker = i
    return "\n".join(lines[last_marker + 1:]) if last_marker >= 0 else text


def check_log(path):
    if not os.path.exists(path):
        return {"ok": False, "reason": "BRAK PLIKU LOGU", "fresh_lines": 0, "mtime": None}
    raw = open(path, encoding="utf-8", errors="replace").read()
    body = _after_marker(raw)
    hit = [p for p in ERROR_PATTERNS if p in body]
    mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
    fresh = len([ln for ln in body.splitlines() if ln.strip()])
    return {
        "ok": not hit,
        "reason": ("błąd: " + "; ".join(hit)) if hit else "czysto (brak błędu po resecie)",
        "fresh_lines": fresh,
        "mtime": mtime,
    }


def check_eta_map():
    if not os.path.exists(ETA_MAP):
        return {"ok": False, "reason": "BRAK MAPY", "mtime": None}
    mtime = datetime.fromtimestamp(os.path.getmtime(ETA_MAP), tz=timezone.utc)
    today = datetime.now(timezone.utc).date()
    return {"ok": mtime.date() == today, "mtime": mtime, "today": today}


def build_report():
    rows, all_ok = [], True
    for name, fn in CRON_LOGS.items():
        r = check_log(os.path.join(LOG_DIR, fn))
        all_ok &= r["ok"]
        mt = r["mtime"].strftime("%m-%d %H:%M") if r["mtime"] else "—"
        rows.append(f"{'✅' if r['ok'] else '❌'} {name}: {r['reason']} (mtime {mt}, {r['fresh_lines']} lin.)")
    m = check_eta_map()
    eta_ok = m["ok"]
    all_ok &= eta_ok
    eta_mt = m["mtime"].strftime("%Y-%m-%d %H:%M UTC") if m.get("mtime") else "—"
    eta_line = f"{'✅' if eta_ok else '❌'} mapa ETA mtime: {eta_mt}" + ("" if eta_ok else " (NADAL frozen — eta_quantile_calib NIE odświeżył!)")

    verdict = "🟢 ZIELONE — wszystkie 3 crony przeszły + mapa ETA świeża." if all_ok else \
              "🔴 CZERWONE — patrz ❌ wyżej; cron wymaga interwencji."
    header = f"🌅 Ziomek calib-cron verify {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    return "\n".join([header, "", *rows, eta_line, "", verdict]), all_ok


def main():
    msg, all_ok = build_report()
    if os.environ.get("CRON_VERIFY_DRY") == "1":
        print(msg)
        print(f"\n[DRY] all_ok={all_ok}")
        return
    try:
        from dispatch_v2.telegram_utils import send_admin_alert
        ok = send_admin_alert(msg)
        print(f"{datetime.now(timezone.utc).isoformat()} calib_cron_verify sent={ok} all_ok={all_ok}")
    except Exception as e:  # noqa: BLE001 — notify best-effort, nie wywalaj at-joba
        print(f"{datetime.now(timezone.utc).isoformat()} calib_cron_verify SEND FAIL {type(e).__name__}: {e}")
        print(msg)


if __name__ == "__main__":
    main()
