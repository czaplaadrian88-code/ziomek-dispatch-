#!/usr/bin/env python3
"""B6 (2026-06-13) — GC reassign-artefaktów w courier_ground_truth.json.

Problem: ground_truth jest pisany WYŁĄCZNIE przez raporty z apki (status_store,
source=auto_geofence), keyed po order_id. Gdy zlecenie zostanie REASSIGNOWANE
(kurier A→B), a B doręcza przez RECONCILE panelu (nie zgłasza przez apkę), wpis
A (np. status 3 "dojazd") NIGDY się nie aktualizuje → wisi phantom: stary kurier
"jedzie" do zlecenia, które ktoś inny już doręczył. Przykład: 480342 gt cid=370/dojazd,
orders_state cid=530/delivered.

Nieszkodliwe dla konsumenta (courier_gps_commitment_shadow.reconcile pomija wpisy bez
picked_up_at/delivered_at — l.132-133 — i ma guard COURIER_MISMATCH + okno 8h; shadow
jest OFF), ale to cruft w pliku. Ten GC go czyści BEZPIECZNIE.

Reguła prune: usuń gt[oid] gdy zlecenie jest TERMINALNE w orders_state
(delivered/cancelled/returned_to_pool) ORAZ wpis NIE MA picked_up_at ANI delivered_at
(czyli status-only artefakt typu dojazd/odbior-bez-faktu — zero wartości kalibracyjnej,
zlecenie zakończone). Wpisy z realnym faktem GPS (picked_up_at/delivered_at) ZOSTAJĄ
(shadow ich używa do kalibracji timingu).

BEZPIECZEŃSTWO: ten sam flock co status_store.write_ground_truth ({path}.lock, LOCK_EX)
→ zero wyścigu z courier-api. Atomic temp+replace. Dry-run domyślnie.

Użycie:
  python3 gc_ground_truth_reassign_artifacts.py          # DRY-RUN
  python3 gc_ground_truth_reassign_artifacts.py --apply   # zapis (backup przed)
"""
import argparse
import fcntl
import json
import os
import shutil
import sys
import time

STATE_DIR = os.environ.get("DISPATCH_STATE_DIR") or "/root/.openclaw/workspace/dispatch_state"
GT_PATH = os.path.join(STATE_DIR, "courier_ground_truth.json")
ORDERS_STATE = os.path.join(STATE_DIR, "orders_state.json")
TERMINAL = ("delivered", "cancelled", "returned_to_pool")


def find_artifacts(gt: dict, orders: dict) -> list:
    """[(oid, entry, reason)] reassign/stale artefaktów do usunięcia."""
    out = []
    for oid, e in gt.items():
        if not isinstance(e, dict):
            continue
        if e.get("picked_up_at") or e.get("delivered_at"):
            continue  # ma realny fakt GPS — ZOSTAW (kalibracja)
        o = orders.get(str(oid))
        if not isinstance(o, dict) or o.get("status") not in TERMINAL:
            continue  # zlecenie nie-terminalne (w toku) lub brak → ZOSTAW
        gt_cid = str(e.get("courier_id"))
        st_cid = str(o.get("courier_id"))
        mism = "" if gt_cid == st_cid else f" reassign {gt_cid}→{st_cid}"
        out.append((oid, e, f"order={o.get('status')} status-only(code={e.get('last_status_code')}){mism}"))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    orders = json.load(open(ORDERS_STATE))
    lockf = open(f"{GT_PATH}.lock", "w")
    fcntl.flock(lockf, fcntl.LOCK_EX)   # ten sam lock co status_store.write_ground_truth
    try:
        gt = json.load(open(GT_PATH))
        artifacts = find_artifacts(gt, orders)
        print(f"\n=== GC ground_truth reassign-artifacts — {'APPLY' if args.apply else 'DRY-RUN'} ===")
        print(f"ground_truth: {len(gt)} wpisów | artefaktów do usunięcia: {len(artifacts)}\n")
        for oid, e, reason in artifacts:
            print(f"  {oid}: cid={e.get('courier_id')} {e.get('last_status_label')!r} — {reason}")
        if not args.apply:
            print("\n(DRY-RUN — nic nie usunięto. --apply aby wyczyścić.)")
            return 0
        if not artifacts:
            print("\n0 artefaktów — nic do zrobienia.")
            return 0
        ts = time.strftime("%Y%m%d-%H%M%S")
        bak = f"{GT_PATH}.bak-pre-gc-{ts}"
        shutil.copy2(GT_PATH, bak)
        for oid, _e, _r in artifacts:
            gt.pop(oid, None)
        tmp = f"{GT_PATH}.tmp.{os.getpid()}"
        with open(tmp, "w") as f:
            json.dump(gt, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, GT_PATH)
        print(f"\nUsunięto {len(artifacts)} artefaktów. Backup: {os.path.basename(bak)} | wpisów teraz: {len(gt)}")
        return 0
    finally:
        fcntl.flock(lockf, fcntl.LOCK_UN)
        lockf.close()


if __name__ == "__main__":
    sys.exit(main())
