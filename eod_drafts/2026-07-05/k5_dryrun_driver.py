#!/usr/bin/env python3
"""K5 — sandboxowy dry-run ścieżki LIVE resweepa (dla FLIPMASTERA, PRZED flipem).

Uruchamia PRAWDZIWY run_once z FLAG_LIVE wymuszonym na ON, ale WSZYSTKIE ZAPISY
(pending + jsonl) idą w KOPIĘ w /tmp — żywy pending_proposals.json NIETKNIĘTY
(assert anty-prod). Odczyty (orders_state, flota, OSRM, flags→bramka geometrii)
= żywe, read-only. Sens: zobaczyć realne podmiany na realnych WISZĄCYCH zanim
flipniesz. ⚠ Uruchamiać gdy są wiszące (dzień roboczy, poza peakiem Pn-Pt
11-14/17-20); przy hanging=0 (np. późny wieczór) wynik = no-op (też informacja).

Użycie: /root/.openclaw/venvs/dispatch/bin/python eod_drafts/2026-07-05/k5_dryrun_driver.py
"""
import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import common as C  # noqa: E402
from dispatch_v2 import pending_proposals_store as PPS  # noqa: E402
from dispatch_v2.tools import pending_global_resweep as PGR  # noqa: E402

LIVE_PENDING = "/root/.openclaw/workspace/dispatch_state/pending_proposals.json"


def main() -> int:
    sb = Path(tempfile.mkdtemp(prefix="k5_dryrun_"))
    shutil.copy(LIVE_PENDING, sb / "pending.json")
    PGR.PENDING_PATH = str(sb / "pending.json")
    PPS.PENDING_PATH = str(sb / "pending.json")
    PGR.OUT_JSONL = str(sb / "out.jsonl")
    assert "/dispatch_state/" not in PPS.PENDING_PATH, "ANTY-PROD: sandbox nie ustawiony"

    _orig = C.flag
    C.flag = lambda n, d=False: True if n in (PGR.FLAG, PGR.FLAG_LIVE) else _orig(n, d)
    try:
        before = json.load(open(LIVE_PENDING))
        print(f"sandbox: {sb}")
        print(f"bramka live_gate_open (żywe flagi): {PGR.live_gate_open()}")
        s = PGR.run_once()
        keep = ("hanging", "would_repropose", "live_acted",
                "maxpile_before", "maxpile_after", "spread_improved", "duration_s")
        print("summary:", {k: s.get(k) for k in keep})
        after = json.load(open(PPS.PENDING_PATH))
        for oid, e in sorted(after.items()):
            if "resweep_live" in e:
                rl = e["resweep_live"]
                nb = ((e.get("decision_record") or {}).get("best") or {})
                print(f"  SWAP {oid}: {rl['old_cid']} -> {rl['new_cid']} "
                      f"(delta_vs_now {rl['delta_vs_now']}, {rl['reason']}); "
                      f"best={nb.get('courier_id')} plan_w_rekordzie={'plan' in nb}")
        live_now = json.load(open(LIVE_PENDING))
        print(f"żywy pending NIETKNIĘTY: {live_now == before}")
        return 0
    finally:
        C.flag = _orig


if __name__ == "__main__":
    raise SystemExit(main())
