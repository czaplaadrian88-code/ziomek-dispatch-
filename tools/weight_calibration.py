#!/usr/bin/env python3
"""weight_calibration — czy WAGI bazowe Ziomka (0.30 dyst / 0.25 obc / 0.25 kier / 0.20 czas)
powinny być przekalibrowane? (Adrian 2026-06-23, na czystym podzbiorze)

READ-ONLY, zero zmian. Dwa testy na MIARODAJNYM podzbiorze (calibration_set_june.jsonl) ×
shadow_decisions (cechy per-kandydat: km_to_pickup, bag_size, score, drive_min, tier):

  TEST A — REVEALED PREFERENCE: na NIEZGODACH (Ziomek #1 ≠ wybór koordynatora, oba z realną
  pozycją) porównaj cechy picku koordynatora vs picku Ziomka. Jeśli koordynator systematycznie
  bierze DALSZEGO (Δkm>0) / bardziej OBCIĄŻONEGO → Ziomek PRZE-waża ten wymiar (waga za duża),
  bo człowiek świadomie go ignoruje. Δscore = jak nisko w modelu Ziomka był pick człowieka.

  TEST B — PREDYKCYJNOŚĆ: czy dystans / obciążenie ZWYCIĘZCY (realnego kuriera) w ogóle
  przewiduje realny wynik (on-time / czas)? Jeśli PŁASKO → waga nieuzasadniona wynikiem
  (kandydat do obniżenia). Jeśli rośnie → waga uzasadniona.

Werdykt łączy: waga jest ZA DUŻA jeśli (A) człowiek ją ignoruje ORAZ (B) nie przewiduje wyniku.

Uruchom:
  cd /root/.openclaw/workspace/scripts
  PYTHONPATH=. /root/.openclaw/venvs/dispatch/bin/python dispatch_v2/tools/weight_calibration.py
"""
import json
import os
import statistics as st
import sys

BASE = "/root/.openclaw/workspace"
CLEAN = f"{BASE}/dispatch_state/calibration_set_june.jsonl"
SHADOW_LOGS = [f"{BASE}/scripts/logs/shadow_decisions.jsonl", f"{BASE}/scripts/logs/shadow_decisions.jsonl.1"]
DIST_DECAY_KM = 5.0  # scoring.py — żeby pokazać ile PUNKTÓW score waży dany Δkm


def _read_jsonl(path):
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
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
    clean = {}
    for r in _read_jsonl(CLEAN):
        clean[str(r.get("oid"))] = r
    clean_oids = set(clean)
    print(f"[weight_calibration]  miarodajny zbiór: {len(clean_oids)} decyzji")

    # shadow: dla oid z czystego zbioru zbierz best + kandydatów (cid->cechy)
    shrec = {}
    for path in SHADOW_LOGS:
        for r in _read_jsonl(path):
            oid = str(r.get("order_id"))
            if oid not in clean_oids or r.get("verdict") != "PROPOSE":
                continue
            best = r.get("best") or {}
            cands = {}
            for c in [best] + (r.get("alternatives") or []):
                cid = _cid(c.get("courier_id"))
                if cid:
                    cands[cid] = c
            shrec[oid] = {"best": best, "cands": cands}

    feat = lambda c, k: _num(c.get(k))
    # ---- TEST A: revealed preference na niezgodach (oba real pos) ----
    dkm, dbag, ddrive, dscore = [], [], [], []
    tier_shift = {"gold→": 0, "→gold": 0}
    farther_out = {"farther_ok": 0, "farther_n": 0, "closer_ok": 0, "closer_n": 0,
                   "farther_dmin": [], "closer_dmin": []}
    n_disagree = n_both_pos = 0
    for oid, cs in clean.items():
        sr = shrec.get(oid)
        if not sr or cs.get("agreement"):
            continue
        n_disagree += 1
        best_c = sr["best"]
        real_c = sr["cands"].get(_cid(cs.get("real_cid")))
        if real_c is None:
            continue
        bk, rk = feat(best_c, "km_to_pickup"), feat(real_c, "km_to_pickup")
        if bk is None or rk is None:
            continue  # wymagamy realnych pozycji obu
        n_both_pos += 1
        dkm.append(rk - bk)
        bb, rb = feat(best_c, "bag_size_before"), feat(real_c, "bag_size_before")
        if bb is not None and rb is not None:
            dbag.append(rb - bb)
        bd, rd = feat(best_c, "drive_min"), feat(real_c, "drive_min")
        if bd is not None and rd is not None:
            ddrive.append(rd - bd)
        bs, rsc = feat(best_c, "score"), feat(real_c, "score")
        if bs is not None and rsc is not None:
            dscore.append(rsc - bs)
        if (best_c.get("v326_speed_tier_used") == "gold") and (real_c.get("v326_speed_tier_used") != "gold"):
            tier_shift["gold→"] += 1
        if (best_c.get("v326_speed_tier_used") != "gold") and (real_c.get("v326_speed_tier_used") == "gold"):
            tier_shift["→gold"] += 1
        # czy "dalszy pick koordynatora" psuje wynik?
        ok = cs.get("real_ontime") is True
        dm = _num(cs.get("real_delivery_min"))
        if (rk - bk) > 0.5:
            farther_out["farther_n"] += 1; farther_out["farther_ok"] += int(ok)
            if dm is not None: farther_out["farther_dmin"].append(dm)
        elif (rk - bk) < -0.5:
            farther_out["closer_n"] += 1; farther_out["closer_ok"] += int(ok)
            if dm is not None: farther_out["closer_dmin"].append(dm)

    print(f"\n=== TEST A — REVEALED PREFERENCE (niezgody, oba z realną pozycją; n={n_both_pos} z {n_disagree} niezgód) ===")
    if n_both_pos:
        mdkm = _med(dkm)
        print(f"  Δkm  (koordynator − Ziomek): mediana {mdkm:+}  [>0 = człowiek bierze DALSZEGO → Ziomek prze-waża dystans]")
        if mdkm is not None:
            # ile punktów score'a waży taka mediana Δkm (gradient exp przy ~mediana km)
            print(f"  Δbag (obciążenie):           mediana {_med(dbag):+}  [>0 = człowiek bierze bardziej OBCIĄŻONEGO]")
            print(f"  Δdrive_min:                  mediana {_med(ddrive):+}")
            print(f"  Δscore (pick człowieka w modelu Ziomka): mediana {_med(dscore):+}  [jak nisko Ziomek rankował wybór człowieka]")
            print(f"  tier: gold→nie-gold {tier_shift['gold→']}  |  nie-gold→gold {tier_shift['→gold']}")
        f, c = farther_out, farther_out
        print(f"\n  Czy DALSZY pick koordynatora psuje wynik?")
        print(f"    człowiek wziął DALSZEGO: on-time {_pct(f['farther_ok'],f['farther_n'])}% (n={f['farther_n']}), czas med {_med(f['farther_dmin'])}")
        print(f"    człowiek wziął BLIŻSZEGO: on-time {_pct(f['closer_ok'],f['closer_n'])}% (n={f['closer_n']}), czas med {_med(f['closer_dmin'])}")

    # ---- TEST B: czy dystans/obciążenie ZWYCIĘZCY przewiduje wynik (cały czysty zbiór × shadow) ----
    print("\n=== TEST B — PREDYKCYJNOŚĆ cech zwycięzcy (czy waga uzasadniona wynikiem) ===")
    rows = []
    for oid, cs in clean.items():
        sr = shrec.get(oid)
        if not sr:
            continue
        real_c = sr["cands"].get(_cid(cs.get("real_cid")))
        if real_c is None:
            continue
        rows.append((feat(real_c, "km_to_pickup"), feat(real_c, "bag_size_before"),
                     cs.get("real_ontime"), _num(cs.get("real_delivery_min"))))
    print(f"  zwycięzcy z cechami: {len(rows)}")

    def bin_report(name, getval, bins):
        print(f"  {name}:")
        for lo, hi in bins:
            sub = [(ot, dm) for v, _b, ot, dm in rows if v is not None and lo <= v < hi] if name.startswith("dyst") \
                else [(ot, dm) for _v, b, ot, dm in rows if b is not None and lo <= b < hi]
            ok = [1 for ot, _ in sub if ot is True]
            dms = [dm for _, dm in sub if dm is not None]
            lbl = f"{lo}-{hi if hi < 99 else '∞'}"
            if sub:
                print(f"    {lbl:8s} n={len(sub):4d}  on-time {_pct(len(ok),len(sub)):5.1f}%  czas-med {_med(dms)}")
    bin_report("dystans km_to_pickup", None, [(0, 1), (1, 2), (2, 4), (4, 99)])
    bin_report("obciążenie bag", None, [(0, 1), (1, 2), (2, 3), (3, 99)])

    print("\n  WERDYKT (do oceny): waga ZA DUŻA jeśli (A) człowiek ją ignoruje (Δ>0) ORAZ (B) cecha NIE przewiduje wyniku (płasko).")
    print("  (read-only; zero wpływu na decyzje/stan)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
