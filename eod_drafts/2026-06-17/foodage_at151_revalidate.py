#!/usr/bin/env python3
"""at#151 (21.06 07:00) — niezależna re-walidacja food-age HARD-SLA na świeżym
tygodniu danych → werdykt GO/NO-GO na Telegram + plik wyniku. Gate flipu Fazy 5.

Odpala foodage_phase4_validation.py (hard-SLA ON, base vs hardsla) na oknie
ostatnich ~7 dni, wyciąga G1/G2/G3 + WERDYKT, wysyła zwięzły alert do Adriana.
Read-only (walidacja nic nie flipuje). Po GO → flip za ACK (osobny krok).
"""
import subprocess
import sys
from datetime import datetime, timezone, timedelta

SCRIPTS = "/root/.openclaw/workspace/scripts"
sys.path.insert(0, SCRIPTS)
PY = "/root/.openclaw/venvs/dispatch/bin/python"
VAL = SCRIPTS + "/dispatch_v2/eod_drafts/2026-06-17/foodage_phase4_validation.py"

now = datetime.now(timezone.utc)
frm = (now - timedelta(days=7)).strftime("%Y-%m-%dT00:00")
to = now.strftime("%Y-%m-%dT23:59")

res = subprocess.run(
    [PY, VAL, "--from", frm, "--to", to, "--min-bag", "3", "--max", "9000"],
    capture_output=True, text=True, cwd=SCRIPTS)
report = res.stdout or ""
err = (res.stderr or "")[-500:]

# wyciągnij kluczowe linie
def _grab(substr):
    return [l.strip() for l in report.splitlines() if substr in l]

lines = []
lines += _grab("ortools-decyzji n=")
lines += _grab("nowe regresje:")
lines += _grab("changed-rate")
lines += _grab("sla_improved")
lines += _grab("hardsla p50")
lines += _grab("delta median")
verdict = next((l.strip() for l in report.splitlines() if "WERDYKT" in l), "WERDYKT: ? (brak — sprawdź log)")

out_path = SCRIPTS + f"/dispatch_v2/eod_drafts/2026-06-17/FOODAGE_AT151_RESULT_{now.strftime('%Y-%m-%d')}.txt"
with open(out_path, "w") as f:
    f.write(report + ("\n\n[stderr tail]\n" + err if err else ""))

msg = ("🍔 food-age HARD-SLA — at#151 re-walidacja (świeży tydzień)\n"
       f"okno {frm[:10]}..{to[:10]}\n"
       + "\n".join(lines) + "\n\n" + verdict
       + f"\n\nGO → flip za ACK: flags.json ENABLE_OBJ_DELIVERY_FOOD_AGE+"
         f"ENABLE_OBJ_FOOD_AGE_HARD_SLA=true + restart dispatch-shadow (off-peak).\n"
       + f"pełny wynik: {out_path}")

try:
    from dispatch_v2.telegram_utils import send_admin_alert
    send_admin_alert(msg, source="foodage_at151")
    print("telegram sent")
except Exception as e:
    print(f"telegram FAIL {type(e).__name__}: {e}")
print(msg)
