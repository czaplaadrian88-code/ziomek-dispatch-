"""L2.1 sentinel-ingest — werdykt okna 2-dniowego po flipie (ETAP 5, +2 dni).

Flip ENABLE_COORD_SENTINEL_INGEST_GUARD=true: 2026-07-01 21:29 UTC (commit eb016c1).
Baseline PRZED (dispatch.log per-day V328 sentinel-eject, distinct ofiary):
30.06 = 8 ofiar · 01.07 = 28 ofiar (432 zdarzenia V328).

Metryka docelowa (POZYTYWNY wpływ, nie tylko brak regresji):
  V328_CP_SOLVER_FAIL z "sentinel (0,0)" PO flipie → 0; distinct (cid,order) → 0.
Kontrola: COORD_INGEST_GUARD hity per warstwa (guard PRACUJE, nie tylko cisza)
+ coord_poison_bag_oids w shadow_decisions (resztkowa trucizna zmierzona).

Wynik → Telegram (send_admin_alert) + stdout (log at-joba).
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

FLIP_TS = "2026-07-01 21:29"
LOG = Path("/root/.openclaw/workspace/scripts/logs/dispatch.log")
SHADOW = Path("/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl")
# testowe oidy/cidy z test_coord_sentinel_ingest_l21 (piszą do tego samego logu)
_TEST_MARKERS = ("L21A", "L21B", "L21C", "L21D", "cid=C1", "cid=C515", "oid=N1", " N1:")


def _is_test(line: str) -> bool:
    return any(m in line for m in _TEST_MARKERS)


def main() -> int:
    v328_sentinel = []
    guard_hits = {}
    for line in LOG.read_text(errors="replace").splitlines():
        if line[:16] < FLIP_TS:
            continue
        if _is_test(line):
            continue
        if "V328_CP_SOLVER_FAIL_PER_COURIER" in line and "sentinel (0,0)" in line:
            v328_sentinel.append(line)
        if "COORD_INGEST_GUARD" in line:
            m = re.search(r"COORD_INGEST_GUARD (\S+)", line)
            k = m.group(1) if m else "?"
            guard_hits[k] = guard_hits.get(k, 0) + 1

    victims = sorted({
        (m.group(1), m.group(2))
        for l in v328_sentinel
        if (m := re.search(r"cid=(\S+) order=(\S+)", l))
    })

    poison_bag = poison_new = scanned = 0
    if SHADOW.exists():
        with SHADOW.open(errors="replace") as f:
            for raw in f:
                if '"coord_poison' not in raw:
                    continue
                try:
                    d = json.loads(raw)
                except Exception:
                    continue
                ts = str(d.get("ts") or d.get("timestamp") or "")
                if ts < "2026-07-01T21:29":
                    continue
                scanned += 1
                for c in (d.get("candidates") or []) + ([d.get("best")] if d.get("best") else []):
                    if c and c.get("coord_poison_bag_oids"):
                        poison_bag += 1
                    if c and c.get("coord_poison_new_delivery"):
                        poison_new += 1

    ok = not v328_sentinel
    verdict = "✅ POZYTYWNY" if ok else "❌ REGRES/NIEDOMKNIĘTE"
    msg = (
        f"L2.1 sentinel-ingest — werdykt +2 dni (flip 01.07 21:29, eb016c1)\n"
        f"{verdict}\n"
        f"V328 sentinel-eject PO flipie: {len(v328_sentinel)} zdarzeń, "
        f"{len(victims)} distinct ofiar (baseline: 28/dzień 01.07, 8/dzień 30.06)\n"
        f"COORD_INGEST_GUARD hity (guard pracuje): {guard_hits or 'brak'}\n"
        f"coord_poison w shadow_decisions: bag={poison_bag} new_delivery={poison_new} "
        f"(rekordów z kluczem: {scanned})\n"
        f"Ofiary (jeśli są): {victims[:10]}"
    )
    print(msg)
    try:
        from dispatch_v2.telegram_utils import send_admin_alert
        send_admin_alert(msg)
    except Exception as e:  # noqa: BLE001
        print(f"telegram fail (nie blokuje werdyktu): {e}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
