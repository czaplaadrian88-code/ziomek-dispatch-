#!/usr/bin/env python3
"""FIX-C (2026-06-13) — backfill `delivered_at` dla doreczen, ktore go zgubily.

Kontekst: bug sink-guard (B3) sprawial, ze doreczenia ze sciezki manual/reconcile
(panel nie podaje czas_doreczenia) mialy delivered_at=null mimo status=delivered.
build_delivered (courier_orders.py:525) wyklucza takie -> zakladka "Doreczone"
pusta + utarg 0. Dotknelo dzis cid=370 (Jakub OL): 480306/480310/480311/480319.

FIX-B (state_machine.py) zatrzymuje WYCIEK na przyszlosc, ale reconcile NIGDY
nie odwiedza ponownie status=delivered (panel_watcher pomija) -> juz-zepsute
ordery sie NIE samonaprawia. Ten skrypt je odzyskuje ze zrodla prawdy:
`courier_ground_truth.json` (ma wierny delivered_at epoch per order).

BEZPIECZENSTWO:
- Zapis WYLACZNIE przez state_machine.upsert_order (fcntl _locked_write) — ten
  sam lock co panel-watcher, zero race (incydent clobber 2026-05-18 nie powtorzy sie).
- GUARD courier_id: backfill TYLKO gdy ground_truth[oid].courier_id == state.courier_id
  (chroni przed mis-atrybucja przy reassignmencie — np. 480342 GT=370 vs state=530).
- event=None -> brak wpisu do history (czysta korekta pola).
- Idempotentny: cel = status=delivered AND not delivered_at. Re-run = 0 celow.
- Format delivered_at = naiwny string Warsaw "YYYY-MM-DD HH:MM:SS" (jak czas_doreczenia
  z panelu) -> przechodzi filtr build_delivered str(d).startswith(_warsaw_today()).

Uzycie:
  python3 backfill_delivered_at.py            # DRY-RUN (domyslnie, nic nie pisze)
  python3 backfill_delivered_at.py --apply    # zapis (backup orders_state przed)
"""
import argparse
import json
import os
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
ROOT = Path(__file__).resolve().parents[3]          # .../scripts
STATE_DIR = ROOT.parent / "dispatch_state"          # .../dispatch_state
ORDERS_STATE = STATE_DIR / "orders_state.json"
GROUND_TRUTH = STATE_DIR / "courier_ground_truth.json"

sys.path.insert(0, str(ROOT))                       # import dispatch_v2.*


def _epoch_to_warsaw_str(epoch) -> str:
    return datetime.fromtimestamp(int(epoch), WARSAW).strftime("%Y-%m-%d %H:%M:%S")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="wykonaj zapis (domyslnie dry-run)")
    args = ap.parse_args()

    orders = json.loads(ORDERS_STATE.read_text())
    gt = json.loads(GROUND_TRUTH.read_text())

    targets, skipped = [], []
    for oid, o in orders.items():
        if o.get("status") != "delivered" or o.get("delivered_at"):
            continue
        g = gt.get(str(oid))
        if not g:
            skipped.append((oid, o.get("courier_id"), "brak w ground_truth"))
            continue
        # GUARD mis-atrybucji (B6): kurier w GT musi zgadzac sie ze stanem
        if str(g.get("courier_id")) != str(o.get("courier_id")):
            skipped.append((oid, o.get("courier_id"),
                            f"courier_id mismatch GT={g.get('courier_id')} state={o.get('courier_id')}"))
            continue
        epoch = g.get("delivered_at")
        if not epoch:
            # fallback: history COURIER_DELIVERED 'at' (ISO UTC) -> Warsaw str
            hist = [h for h in o.get("history", []) if h.get("event") == "COURIER_DELIVERED"]
            if hist:
                try:
                    dt = datetime.fromisoformat(hist[-1]["at"].replace("Z", "+00:00"))
                    new_val = dt.astimezone(WARSAW).strftime("%Y-%m-%d %H:%M:%S")
                    targets.append((oid, o.get("courier_id"), new_val, "history(COURIER_DELIVERED)"))
                    continue
                except Exception:
                    pass
            skipped.append((oid, o.get("courier_id"), "brak delivered_at w GT i history"))
            continue
        targets.append((oid, o.get("courier_id"), _epoch_to_warsaw_str(epoch), "ground_truth"))

    print(f"\n=== BACKFILL delivered_at — {'APPLY' if args.apply else 'DRY-RUN'} ===")
    print(f"orders_state: {len(orders)} zlecen | doreczone bez delivered_at -> "
          f"{len(targets)} do naprawy, {len(skipped)} pominietych\n")
    print("DO NAPRAWY:")
    for oid, cid, val, src in targets:
        r = orders[oid]
        print(f"  {oid} cid={cid} {r.get('restaurant')!r} -> delivered_at={val}  (src={src})")
    if skipped:
        print("\nPOMINIETE (guard/brak danych):")
        for oid, cid, why in skipped:
            print(f"  {oid} cid={cid}: {why}")

    if not args.apply:
        print("\n(DRY-RUN — nic nie zapisano. Uruchom z --apply aby naprawic.)")
        return 0
    if not targets:
        print("\n0 celow — nic do zrobienia.")
        return 0

    # backup orders_state przed zapisem
    ts = time.strftime("%Y%m%d-%H%M%S")
    bak = STATE_DIR / f"orders_state.json.bak-pre-backfill-delivered-{ts}"
    shutil.copy2(ORDERS_STATE, bak)
    print(f"\nbackup: {bak.name}")

    from dispatch_v2 import state_machine  # noqa: E402 (po sys.path)
    ok = 0
    for oid, cid, val, _src in targets:
        state_machine.upsert_order(str(oid), {"delivered_at": val}, event=None)
        check = state_machine.get_order(str(oid))
        status = "OK" if check.get("delivered_at") == val else f"WERYFIKACJA FAIL ({check.get('delivered_at')!r})"
        print(f"  {oid} -> {val}  [{status}]")
        ok += 1 if check.get("delivered_at") == val else 0
    print(f"\nNaprawiono {ok}/{len(targets)}. Backup: {bak.name}")
    return 0 if ok == len(targets) else 1


if __name__ == "__main__":
    sys.exit(main())
