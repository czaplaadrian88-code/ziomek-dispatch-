#!/usr/bin/env python3
"""Przypominajka + karta GO/NO-GO flipa HARD inwariantu claim-ledger (Sprint B, 08.07).
Czyta okno obserwacji CHECK (log-loud) z resweepu, liczy zero-FP, wypisuje werdykt.
READ-ONLY. Uruchamiane przez `at` 10.07 rano. Detal: [[sprint-inwarianty-claim-ledger-2026-07-08]]."""
import json, os, datetime as dt

LOG = "/root/.openclaw/workspace/dispatch_state/pending_global_resweep.jsonl"
WINDOW_START = "2026-07-08T14:14:00+00:00"  # flip CHECK ON (ACK Adriana)
OUT_DIR = "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-07-10"
OUT = os.path.join(OUT_DIR, "CLAIM_LEDGER_HARD_GATE_CARD.md")

start = dt.datetime.fromisoformat(WINDOW_START)
rows = breaches = 0
first_ts = last_ts = None
breach_examples = []
try:
    with open(LOG) as f:
        for line in f:
            line = line.strip()
            if not line or "g_claim_ledger_breaches" not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            ts = d.get("ts")
            if not ts:
                continue
            try:
                t = dt.datetime.fromisoformat(ts)
            except Exception:
                continue
            if t < start:
                continue
            rows += 1
            first_ts = first_ts or ts
            last_ts = ts
            b = d.get("g_claim_ledger_breaches", 0) or 0
            if b:
                breaches += b
                if len(breach_examples) < 5:
                    breach_examples.append({"ts": ts, "oid": d.get("order_id"), "breaches": b})
except FileNotFoundError:
    rows = -1

hours = 0.0
if first_ts and last_ts:
    hours = (dt.datetime.fromisoformat(last_ts) - dt.datetime.fromisoformat(first_ts)).total_seconds() / 3600.0

if rows < 0:
    verdict = "BŁĄD — brak logu resweepu"
elif rows == 0:
    verdict = "WAIT — brak wpisów w oknie (sprawdź czy resweep żyje)"
elif breaches > 0:
    verdict = f"⛔ NO-GO — {breaches} breach w {rows} wierszach (fałszywka log-loud → NIE flipuj HARD, diagnozuj)"
elif hours < 40:
    verdict = f"🟡 WAIT — 0 breach, ale okno tylko {hours:.1f} h (<~2 dni); poczekaj na pełny cykl peak+off-peak"
else:
    verdict = f"✅ GO — 0 breach w {rows} wierszach przez {hours:.1f} h (≥2 dni). Flip HARD za ACK; decyzja: raise vs drop-feral-claim"

card = f"""# KARTA GO/NO-GO — flip HARD inwariantu claim-ledger (Sprint B)
Wygenerowana automatycznie przez `at` 10.07. Detal: memory [[sprint-inwarianty-claim-ledger-2026-07-08]].

## WERDYKT: {verdict}

- Okno od: {WINDOW_START} (flip CHECK ON, ACK Adriana 08.07)
- Wpisów resweepu z polem g_claim_ledger_breaches: {rows}
- Pierwszy / ostatni wpis: {first_ts} … {last_ts}  ({hours:.1f} h)
- Suma breach (fałszywek log-loud, oczekiwane 0): {breaches}
- Przykłady breach: {breach_examples if breach_examples else 'brak (0 FP)'}

## Jeśli GO — kroki flipa HARD (FLIPMASTER, za ACK Adriana):
1. Decyzja semantyki: raise wyjątek vs „drop feralnego claimu" (⚠ HARD w resweepie zatrzymałby tick).
2. Dopisać `ENABLE_CLAIM_LEDGER_INVARIANT_HARD=true` w flags.json (hot).
3. Monitor 1h + rollback = flaga false.

## PRZYPOMNIENIE — Sprint A (perf) też czeka:
Flip **A2** (deterministyczny budżet OR-Tools, branch `perf/p95-ortools` NIEzmergowany) — bramka: replay end-to-end przez route_simulator + 2 dni cienia + ACK. Detal: [[sprint-perf-p95-ortools-det-2026-07-08]].
"""

os.makedirs(OUT_DIR, exist_ok=True)
with open(OUT, "w") as f:
    f.write(card)
print(card)
print(f"\n[Karta zapisana: {OUT}]")
