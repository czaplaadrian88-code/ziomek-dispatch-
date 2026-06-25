#!/usr/bin/env python3
"""Jednorazowe przypomnienie 02.07 (Adrian 2026-06-25): po tym jak timer
dispatch-bundle-calib-review wyśle werdykt o 07:00 UTC, ten skrypt (at-job 08:00 UTC):
1) re-odpala review z --no-telegram, zapisuje pełny werdykt do pliku (durable snapshot,
   bo Telegram scrolluje), 2) wyłuskuje linię WERDYKT, 3) pinguje Adriana (send_admin_alert)
żeby ODPALIŁ sesję CC do DECYZJI o sprincie silnika wąskiej reguły X/Y/Z (Opcja 3:
kurier niesie A + stoi pod restauracją odbioru B). Read-only, NIC nie flipuje.
"""
import sys
import subprocess

SCRIPTS = "/root/.openclaw/workspace/scripts"
OUT = "/root/.openclaw/workspace/dispatch_state/bundle_calib_review_verdict_2026-07-02.txt"
sys.path.insert(0, SCRIPTS)


def _run_review():
    try:
        res = subprocess.run(
            ["/root/.openclaw/venvs/dispatch/bin/python", "-m",
             "dispatch_v2.tools.bundle_calib_review", "--no-telegram"],
            cwd=SCRIPTS, capture_output=True, text=True, timeout=600)
        return res.stdout or res.stderr or "(pusty output review)"
    except Exception as e:
        return f"(review run fail: {type(e).__name__}: {e})"


def main():
    body = _run_review()
    try:
        with open(OUT, "w", encoding="utf-8") as fh:
            fh.write(body)
    except Exception as e:
        print(f"[zapis snapshot fail] {e}")
    verdict = next((ln.strip() for ln in body.splitlines() if "WERDYKT" in ln), "(brak linii WERDYKT)")
    msg = (
        "PRZYPOMNIENIE 02.07 — werdykt BUNDLE-CALIB (kalibracja X/Y/Z, Opcja 3 Adriana: "
        "kurier niesie A + stoi pod restauracja odbioru B).\n"
        f"{verdict}\n"
        f"Pelny werdykt zapisany: {OUT}\n"
        "-> Odpal sesje Claude Code do DECYZJI:\n"
        "  - GO pod capem Z -> sprint silnika waskiej reguly X/Y/Z (detour<=X/Y ORAZ carried<=Z) "
        "trojka feasibility+route_simulator+plan_recheck RAZEM, ziomek-change-protocol ETAP 0-7.\n"
        "  - INCONCLUSIVE (coverage under_z <20) -> przedluzyc zbieranie shadow.\n"
        "  - NIC nie flipowac bez ACK Adriana.\n"
        "Kontekst: memory carried-vs-coloc-pickup-priority-2026-06-25 + shadow-jobs-registry."
    )
    try:
        from dispatch_v2.telegram_utils import send_admin_alert
        ok = send_admin_alert(msg, source="bundle_calib_verdict_reminder")
        print(f"[telegram] wyslano={ok}")
    except Exception as e:
        print(f"[telegram] fail: {type(e).__name__}: {e}")
    print(msg)


if __name__ == "__main__":
    main()
