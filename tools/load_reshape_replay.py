#!/usr/bin/env python3
"""load_reshape_replay — replay przeregulowanej formuły OBCIĄŻENIA (Adrian 2026-06-23).

READ-ONLY, zero zmian/flag. Pytanie: czy reshape `s_obciazenie` z liniowej (100·(1−bag/5):
prze-nagradza pusty worek) na PŁASKĄ 0-2 + kara 3+ poprawia decyzje Ziomka?

METODA (dokładna, bez re-runu silnika): zmiana formuły obciążenia rusza final_score WPROST
o jeden człon: Δ = W_OBCIAZENIE(0.25)·(s_obc_new(bag) − s_obc_old(bag)). Wszystkie inne człony
(dystans, kierunek, czas, bonusy r4/tier/wait/...) NIEZMIENIONE. Więc dla każdego zalogowanego
kandydata: new_score = score + 0.25·(s_obc_new − s_obc_old), gdzie bag = bag_size_before.
Re-rankujemy feasible (MAYBE) kandydatów per decyzja → nowy #1.

Mierzymy na MIARODAJNYM zbiorze (calibration_set_june.jsonl × shadow):
  - ile decyzji zmienia #1; czy nowy #1 bierze BARDZIEJ obciążonego (bundling↑);
  - czy nowy #1 ZGADZA się z koordynatorem CZĘŚCIEJ (gained vs lost agreement) — koordynator
    = słaby wzorzec „dobrego" wyboru; gained z dobrym on-time = reshape rusza Ziomka ku dobrym pickom;
  - on-time tam gdzie nowy #1 == realny kurier (obserwowalne).

⚠ Re-rank po SCORE; live silnik ma jeszcze przejścia post-score (_demote_blind_empty, late-pickup
tiering) — to PIERWSZORZĘDNY efekt formuły, nie pełna selekcja. Reshape nie zmienia feasibility
(s_obc to scoring, NIE twarda bramka — bag-capy osobne). Werdykt = pre-flip sygnał, nie flip.

Uruchom:
  cd /root/.openclaw/workspace/scripts
  PYTHONPATH=. /root/.openclaw/venvs/dispatch/bin/python dispatch_v2/tools/load_reshape_replay.py
"""
import json
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
W_OBCIAZENIE = 0.25
MAX_BAG_TSP = 5  # scoring.py: s_obciazenie 0 dla bag>=5

# Przereguowana formuła (hipoteza Adriana): płaska 0-2, kara od 3+
NEW_OBC = {0: 100.0, 1: 100.0, 2: 100.0, 3: 60.0, 4: 20.0}


def old_obc(bag):
    if bag is None:
        return None
    if bag >= MAX_BAG_TSP:
        return 0.0
    return 100.0 * (1.0 - bag / MAX_BAG_TSP)


def new_obc(bag):
    if bag is None:
        return None
    return NEW_OBC.get(int(bag), 0.0)


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
    print(f"[load_reshape_replay]  zbiór: {len(clean_oids)}  | reshape s_obc: {NEW_OBC} (3+ kara), W={W_OBCIAZENIE}")

    analyzed = changed = 0
    old_agree = new_agree = 0
    gained = lost = 0          # gained: old#1!=real ale new#1==real ; lost: odwrotnie
    gained_ok = gained_known = 0
    old_bags, new_bags = [], []
    new_more_loaded = 0

    seen = set()
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
                bag = _num(c.get("bag_size_before"))
                if cid and sc is not None and bag is not None:
                    cands.append((cid, sc, int(bag)))
            if len(cands) < 2:
                continue
            seen.add(oid)
            analyzed += 1
            real = _cid(clean[oid].get("real_cid"))

            old_top = max(cands, key=lambda x: x[1])
            new_top = max(cands, key=lambda x: x[1] + W_OBCIAZENIE * (new_obc(x[2]) - old_obc(x[2])))
            old_bags.append(old_top[2])
            new_bags.append(new_top[2])
            if old_top[0] != new_top[0]:
                changed += 1
                if new_top[2] > old_top[2]:
                    new_more_loaded += 1
            oa = (old_top[0] == real)
            na = (new_top[0] == real)
            old_agree += int(oa)
            new_agree += int(na)
            if not oa and na:        # reshape ZBLIŻYŁ do wyboru człowieka
                gained += 1
                if clean[oid].get("real_ontime") is not None:
                    gained_known += 1
                    gained_ok += int(clean[oid].get("real_ontime") is True)
            if oa and not na:        # reshape ODDALIŁ od wyboru człowieka
                lost += 1

    print(f"\n=== WYNIK (n={analyzed} decyzji z ≥2 feasible) ===")
    print(f"  zmieniło #1: {changed} = {_pct(changed,analyzed)}%   (z tego nowy #1 bardziej obciążony: {new_more_loaded})")
    print(f"  mediana bag #1:  STARY {_med(old_bags)}  →  NOWY {_med(new_bags)}   [↑ = więcej bundlingu, zamierzone]")
    print(f"\n  zgodność z koordynatorem:  STARY {old_agree}/{analyzed} = {_pct(old_agree,analyzed)}%  →  NOWY {new_agree}/{analyzed} = {_pct(new_agree,analyzed)}%")
    print(f"  gained (reshape zbliżył do człowieka): {gained}   |  lost (oddalił): {lost}   |  NET {gained-lost:+}")
    if gained_known:
        print(f"  on-time picków 'gained' (wybór człowieka, teraz też Ziomka): {gained_ok}/{gained_known} = {_pct(gained_ok,gained_known)}%")
    print("\n  WERDYKT (do oceny):")
    print("   • NET agreement ≫ 0 + on-time gained ~90%  → reshape rusza Ziomka ku DOBRYM (ludzkim) pickom → warto (pre-flip).")
    print("   • NET ~0 lub on-time gained słabe          → reshape to wash → NIE flipować (kolejne no-op).")
    print("   • bundling (mediana bag↑) sam w sobie = plus efektywności, jeśli bez utraty jakości.")
    print("  ⚠ pierwszorzędny efekt formuły (re-rank po score); pełny flip wymaga shadow w silniku z monitorem R6/SLA.")
    print("  (read-only; zero wpływu na decyzje/stan)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
