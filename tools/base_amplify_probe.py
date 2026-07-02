#!/usr/bin/env python3
"""base_amplify_probe — czy WZMOCNIENIE siły wag bazowych (baza ×K) dałoby lepsze picki?
(Adrian 2026-06-23, przed zamknięciem wątku wag)

READ-ONLY. Reshape/reweight wag = no-op (baza 0-100 dławiona bonusami ±150 + marginesy ~88).
Inne pytanie: WZMOCNIĆ całą bazę (×K), by bazowe człony konkurowały z bonusami. To zmieni picki —
ale w dobrą stronę? Wzmocniona baza woli BLISKI+PUSTY nad bundlem (free-stop). Mierzymy:

  1) Ile picków Ziomka (#1) jest NIESIONYCH przez bonus bundla (free-stop r4 / L1) — to dokładnie te,
     które wzmocnienie bazy by ZERWAŁO (bliski-pusty solo by wygrał).
  2) base-proxy (0.30·s_dyst + 0.25·s_obc — dwie dostępne, największe składowe bazy): jak często
     preferowałaby INNEGO (bliższego/pustszego) niż faktyczny #1 → skala „flipów" przy wzmocnieniu.
  3) Wynik tych bundli (gdy #1==realny): czy są dobre (to co byśmy zerwali).

Werdykt: jeśli duży % picków = bundle niesione bonusem o DOBRYM wyniku → wzmocnienie bazy = zerwanie
walidowanego bundlingu → GORZEJ (bonusy dławią bazę CELOWO: „free stop +150" > „bliżej o 1 km").

Uruchom:
  cd /root/.openclaw/workspace/scripts
  PYTHONPATH=. /root/.openclaw/venvs/dispatch/bin/python dispatch_v2/tools/base_amplify_probe.py
"""
import json
import math
import os
import statistics as st
import sys

BASE = "/root/.openclaw/workspace"
CLEAN = f"{BASE}/dispatch_state/calibration_set_june.jsonl"
# L1.2 (2026-07-02): odczyt shadow_decisions ROTATION-AWARE przez kanon
# (_rotated_logs/ledger_io) — stary hardkod [żywy, .1] gubił .2.gz po rotacji
# (logrotate size 100M / daily + delaycompress). files_in_window daje pełny
# łańcuch (.N.gz→.1→żywy) chronologicznie; ścieżka = ledger_io.LEDGER. Indeks
# decs jest first-wins per oid (0 kolizji między plikami → identycznie), metryki BEZ ZMIAN.
try:
    from dispatch_v2.tools import _rotated_logs, ledger_io
except ImportError:
    import sys as _sys
    _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from dispatch_v2.tools import _rotated_logs, ledger_io

SHADOW_LOGS = _rotated_logs.files_in_window(ledger_io.LEDGER["shadow"])


def s_dyst(km):
    return None if km is None else max(0.0, min(100.0, 100.0 * math.exp(-km / 5.0)))


def s_obc(bag):
    if bag is None:
        return None
    return 0.0 if bag >= 5 else 100.0 * (1.0 - bag / 5.0)


def base_proxy(km, bag):
    sd, so = s_dyst(km), s_obc(bag)
    if sd is None or so is None:
        return None
    return 0.30 * sd + 0.25 * so  # dwie dostępne składowe bazy (dystans+obciążenie)


def _read_jsonl(path):
    if not os.path.exists(path):
        return
    for line in _rotated_logs.open_maybe_gz(path):
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue


def _cid(v):
    return None if v is None else str(v).strip()


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _med(xs):
    xs = [x for x in xs if x is not None]
    return round(st.median(xs), 2) if xs else None


def _pct(n, d):
    return round(100.0 * n / d, 1) if d else 0.0


def main():
    clean = {str(r.get("oid")): r for r in _read_jsonl(CLEAN)}
    clean_oids = set(clean)
    seen = set()
    cat = {"free_stop_silny(r4>=50)": 0, "free_stop_slaby(r4 1-49)": 0, "L1_same_rest(bundle>=25)": 0, "solo/bez_bonusu": 0}
    n = 0
    bundle_ok = bundle_known = 0
    flip = flip_winner_bundle = flip_eligible = 0
    bonus_mag = []

    for path in SHADOW_LOGS:
        for r in _read_jsonl(path):
            oid = str(r.get("order_id"))
            if oid not in clean_oids or oid in seen or r.get("verdict") != "PROPOSE":
                continue
            cands = []
            for c in [r.get("best") or {}] + (r.get("alternatives") or []):
                if c.get("feasibility") != "MAYBE":
                    continue
                cid = _cid(c.get("courier_id"))
                sc = _num(c.get("score"))
                if cid and sc is not None:
                    cands.append({"cid": cid, "score": sc, "km": _num(c.get("km_to_pickup")),
                                  "bag": _num(c.get("bag_size_before")), "r4": _num(c.get("bonus_r4")) or 0.0,
                                  "bb": _num(c.get("bundle_bonus")) or 0.0, "l1": _num(c.get("bonus_l1")) or 0.0})
            if len(cands) < 2:
                continue
            seen.add(oid)
            n += 1
            top = max(cands, key=lambda x: x["score"])
            real = _cid(clean[oid].get("real_cid"))

            # 1) kategoria picku #1
            if top["r4"] >= 50:
                cat["free_stop_silny(r4>=50)"] += 1; carried = True
            elif top["r4"] >= 1:
                cat["free_stop_slaby(r4 1-49)"] += 1; carried = True
            elif top["bb"] >= 25 or top["l1"] >= 25:
                cat["L1_same_rest(bundle>=25)"] += 1; carried = True
            else:
                cat["solo/bez_bonusu"] += 1; carried = False
            if carried:
                bonus_mag.append(top["r4"] + top["bb"])
                if top["cid"] == real and clean[oid].get("real_ontime") is not None:
                    bundle_known += 1; bundle_ok += int(clean[oid].get("real_ontime") is True)

            # 2) base-proxy flip (kandydaci z realnymi km)
            bp = [(c, base_proxy(c["km"], c["bag"])) for c in cands]
            bp = [(c, v) for c, v in bp if v is not None]
            if len(bp) >= 2 and top["km"] is not None:
                flip_eligible += 1
                bp_top = max(bp, key=lambda x: x[1])[0]
                if bp_top["cid"] != top["cid"]:
                    flip += 1
                    if carried:
                        flip_winner_bundle += 1

    print(f"[base_amplify_probe]  decyzji: {n}")
    print("\n=== 1. Z CZEGO żyje pick #1 Ziomka (co wzmocnienie bazy by zerwało) ===")
    for k, v in cat.items():
        print(f"  {k:28s} {v:5d} = {_pct(v,n)}%")
    carried_tot = n - cat["solo/bez_bonusu"]
    print(f"  → NIESIONE bonusem (bundle/free-stop): {carried_tot} = {_pct(carried_tot,n)}%  (mediana bonusu {_med(bonus_mag)} pkt)")
    if bundle_known:
        print(f"  → wynik tych bundli (gdy #1==realny): on-time {bundle_ok}/{bundle_known} = {_pct(bundle_ok,bundle_known)}%  (to byśmy zerwali)")

    print("\n=== 2. base-proxy (bliski+pusty) — jak często chce INNEGO niż #1 (skala flipów przy wzmocnieniu) ===")
    print(f"  decyzje porównywalne (km realne): {flip_eligible}")
    print(f"  base-proxy ≠ #1 Ziomka: {flip} = {_pct(flip,flip_eligible)}%   (z tego #1 był BUNDLEM: {flip_winner_bundle})")

    print("\n  WERDYKT (do oceny):")
    print("   • Jeśli duży % #1 = bundle niesiony bonusem o DOBRYM on-time, a base-proxy chce kogoś innego")
    print("     → wzmocnienie bazy ZERWAŁOBY te (dobre) bundle na rzecz bliski-pusty-solo → GORZEJ.")
    print("   • Bonusy dławią bazę CELOWO: 'free stop na trasie +150' > 'bliżej o 1 km' (a dystans i tak nie")
    print("     przewiduje on-time — TEST B). Wzmacnianie bazy = cofanie tej (słusznej) hierarchii.")
    print("  (read-only; zero wpływu na decyzje/stan)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
