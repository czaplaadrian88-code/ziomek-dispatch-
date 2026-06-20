"""[A2] Online shadow-parity dwumodelu vs obecny system — na ŻYWYM shadow_decisions.jsonl.

CAŁKOWICIE OFFLINE/READ-ONLY. Nie dotyka prod modelu, nie flipuje, nie restartuje.

Dwie warstwy dowodu parytetu:

  (A) AUTHORITATIVE pairwise per reżim — z held-out forward okna (real reconstructed
      data v2.0, definicje PRODUKCYJNE, out-of-time). To jest twardy pomiar jakości
      rankingu dwumodelu (solo/bundle) — bo shadow_decisions NIE zawiera ground-truth
      „kto był słuszny" ani surowych lat/lon do pełnej rekonstrukcji cech.

  (B) ONLINE top-1 agreement na shadow_decisions.jsonl — rekonstruuje kandydatów z pól
      żywych (bag_size_before, bag_context→drops/pickup, km_to_pickup→dist, pos_source,
      district z v326_r06_*), liczy cechy ŻYWĄ ścieżką (_compute_all_candidate_features:
      delta=pool_mean, haversine×1.42), routuje po stanie worka, i porównuje top-1
      dwumodelu z:
        - obecnym pickiem rule-based (`best`),
        - obecnym pojedynczym modelem (`lgbm_shadow.winner_cid`).
      Plus: czy dwumodel „psuje" decyzje, w których obecny system jest PEWNY
      (duża przewaga score best vs 2. miejsce).

Uruchom:
  /root/.openclaw/workspace/scripts/ml_data_prep/venv/bin/python3 \
      /root/.openclaw/workspace/scripts/dispatch_v2/ml_data_prep/online_shadow_parity.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "2")

HERE = Path(__file__).resolve().parent
SCRIPTS = Path("/root/.openclaw/workspace/scripts")
PROD_ML = SCRIPTS / "ml_data_prep"
for p in (str(HERE), str(SCRIPTS), str(PROD_ML)):
    if p not in sys.path:
        sys.path.insert(0, p)

SHADOW_LOG = SCRIPTS / "logs" / "shadow_decisions.jsonl"
OUT = HERE / "models_twomodel" / "online_shadow_parity_report.json"


# ─────────────────────────────────────────────────────────────────────────────
# (A) Authoritative pairwise per reżim z held-out forward okna
# ─────────────────────────────────────────────────────────────────────────────
def authoritative_pairwise(forward_days: int = 14) -> Dict[str, Any]:
    import numpy as np
    import lightgbm as lgb
    import pandas as pd
    import train_two_models as tm
    from twomodel_common import load_split, solo_mask

    frames = [load_split(s) for s in ("train", "val", "test")]
    allp = pd.concat(frames, ignore_index=True)
    allp["_date"] = pd.to_datetime(allp["date"], errors="coerce").dt.normalize()
    days = sorted(allp["_date"].dropna().unique())
    cutoff = days[-forward_days]
    train = allp[allp["_date"] < cutoff].reset_index(drop=True)
    fwd = allp[allp["_date"] >= cutoff].reset_index(drop=True)

    def train_one(pairs, drop_bundle):
        d = sorted(pairs["_date"].dropna().unique())
        nval = max(1, int(len(d) * 0.10))
        vd = set(d[-nval:])
        tr = pairs[~pairs["_date"].isin(vd)]
        va = pairs[pairs["_date"].isin(vd)]
        tpw = tm.build_pointwise(tr, drop_bundle=drop_bundle)
        vpw = tm.build_pointwise(va, drop_bundle=drop_bundle)
        le = tm.fit_label_encoders(tpw)
        tc = tm.fit_tier_categories(tpw)
        tpw = tm.apply_tier_onehot(tm.apply_label_encoders(tpw, le), tc)
        vpw = tm.apply_tier_onehot(tm.apply_label_encoders(vpw, le), tc)
        fo = tm.feature_columns_of(tpw)
        Xtr, ytr, gtr = tm.to_arrays(tpw, fo)
        Xva, yva, gva = tm.to_arrays(vpw, fo)
        hp = dict(tm.HYPERPARAMS); hp["num_threads"] = 2
        m = lgb.LGBMRanker(**hp)
        m.fit(Xtr, ytr, group=gtr, eval_set=[(Xva, yva)], eval_group=[gva],
              eval_at=[5], callbacks=[lgb.early_stopping(50, verbose=False)])
        return m.booster_, le, tc, fo

    out = {}
    for regime, drop_bundle in (("solo", True), ("bundle", False)):
        fp = fwd[solo_mask(fwd)] if drop_bundle else fwd[~solo_mask(fwd)]
        fp = fp.reset_index(drop=True)
        booster, le, tc, fo = train_one(
            (train[solo_mask(train)] if drop_bundle else train[~solo_mask(train)]).reset_index(drop=True),
            drop_bundle,
        )
        pa, n = tm.pairwise_accuracy(fp, booster, le, tc, fo, drop_bundle)
        out[regime] = {"forward_pairwise": round(pa, 4), "n_pairs": int(n)}
    out["cutoff_date"] = str(cutoff.date()) if hasattr(cutoff, "date") else str(cutoff)
    out["forward_days"] = forward_days
    return out


# ─────────────────────────────────────────────────────────────────────────────
# (B) Online top-1 agreement na shadow_decisions.jsonl
# ─────────────────────────────────────────────────────────────────────────────
class _ShadowCand:
    """Kandydat zrekonstruowany z rekordu shadow (pola żywe)."""
    def __init__(self, rec: Dict[str, Any]):
        self.courier_id = str(rec.get("courier_id") or "")
        self.name = rec.get("name")
        self.courier_name = rec.get("name")
        bs = rec.get("bag_size_before")
        self.bag_size = int(bs) if bs is not None else 0
        bc = rec.get("bag_context") or []
        self.bag_drops_pending = sum(
            1 for b in bc if isinstance(b, dict) and b.get("delivered_at") is None and b.get("picked_up_at") is not None
        )
        self.bag_pickup_pending = sum(
            1 for b in bc if isinstance(b, dict) and b.get("picked_up_at") is None
        )
        self.bag_n_distinct_districts = 0
        self.bag_has_distant_drop = False
        # dist_to_pickup_km = km_to_pickup żywego systemu (road km).
        self._km = rec.get("km_to_pickup")
        self.idle_min = None  # nieobecne per-kandydat w shadow → None (= -1 jak prod)
        self.orders_today_before_T0 = 0
        self.last_pos_lat = None
        self.last_pos_lon = None
        self.metrics = {}
        self._cur_score = rec.get("score")


def _patch_feature_compute(inferer, cands: List[_ShadowCand]):
    """Wstrzyknij km_to_pickup jako dist_to_pickup_km (brak surowych lat/lon w shadow).

    Tworzy lekką podmiankę _compute_road_km, by feature-path liczył dystans z żywego
    km_to_pickup zamiast OSRM (którego tu nie wołamy). Haversine NIE jest dostępny
    z shadow → zostaje -1 (jak produkcja gdy brak pozycji), więc to test DEGRADOWANY
    na cechach geometrycznych; sygnał: routing+ranking względny, NIE absolutny.
    """
    pass


def online_top1_agreement(limit: Optional[int] = None, include_archive: bool = True) -> Dict[str, Any]:
    from dispatch_v2 import ml_inference as mi

    base = mi.get_lgbm_inferer()
    tmm = mi.LGBMTwoModelInferer(base_inferer=base)
    if not tmm._loaded:
        return {"checked": False, "reason": "twomodel not loaded"}

    files = [SHADOW_LOG]
    if include_archive and (SHADOW_LOG.parent / "shadow_decisions.jsonl.1").exists():
        files.append(SHADOW_LOG.parent / "shadow_decisions.jsonl.1")

    recs = []
    for f in files:
        try:
            for ln in open(f):
                ln = ln.strip()
                if ln:
                    recs.append(json.loads(ln))
        except Exception:
            continue
    if limit:
        recs = recs[-limit:]

    n_eval = 0
    agree_rule = 0          # two-model top1 == obecny best (rule-based)
    agree_single = 0        # two-model top1 == lgbm_shadow.winner_cid
    n_single_avail = 0
    solo_decisions = 0      # decyzje gdzie pula ma >=1 empty-bag
    confident_total = 0     # decyzje gdzie best ma dużą przewagę score
    confident_kept = 0      # ...i two-model NIE psuje (top1 == best)
    regime_winner = {"solo": 0, "bundle": 0}
    skipped = 0

    for d in recs:
        best = d.get("best")
        alts = d.get("alternatives") or []
        if not best:
            skipped += 1
            continue
        pool = [best] + alts
        if len(pool) < 2:
            skipped += 1
            continue
        if any(c.get("km_to_pickup") is None or c.get("bag_size_before") is None for c in pool):
            skipped += 1
            continue

        cands = [_ShadowCand(c) for c in pool]
        # Podmiana liczenia drogi: użyj km_to_pickup jako dist_to_pickup_km.
        km_by_idx = {i: cands[i]._km for i in range(len(cands))}

        orig = base._compute_road_km
        def _road(lat, lon, plat, plon, _orig=orig):
            return float("nan"), False  # placeholder; nadpisany niżej per-candidate
        # Prościej: zbuduj decision_ctx i podmień _compute_all_candidate_features dystans.
        ctx = {
            "order_id": d.get("order_id"),
            "decision_ts": _parse_ts(d.get("ts")),
            "pickup_lat": None, "pickup_lon": None,
            "pickup_district": best.get("v326_r06_pickup_district") or "Unknown",
            "drop_district": best.get("v326_r06_drop_district") or "Unknown",
        }
        try:
            # monkeypatch: wymuś dist_to_pickup_km = km_to_pickup, haversine=NaN
            rows = _compute_rows_from_shadow(base, ctx, cands, km_by_idx)
            res = _score_rows_twomodel(tmm, cands, rows)
        except Exception:
            skipped += 1
            continue
        if not res or res.get("winner_cid") is None:
            skipped += 1
            continue

        n_eval += 1
        tm_top1 = res["winner_cid"]
        regime_winner[res["winner_regime"]] = regime_winner.get(res["winner_regime"], 0) + 1
        if any(_ShadowCand(c).bag_size == 0 for c in pool):
            solo_decisions += 1

        # obecny rule-based winner = best.courier_id
        cur_top1 = str(best.get("courier_id") or "")
        if tm_top1 == cur_top1:
            agree_rule += 1

        # obecny pojedynczy model
        ls = best.get("lgbm_shadow") or {}
        single_win = ls.get("winner_cid")
        if single_win:
            n_single_avail += 1
            if str(single_win) == tm_top1:
                agree_single += 1

        # „pewne" decyzje: przewaga score best vs 2. miejsce >= 30 pkt
        scores = sorted([c.get("score") for c in pool if c.get("score") is not None], reverse=True)
        if len(scores) >= 2 and (scores[0] - scores[1]) >= 30.0:
            confident_total += 1
            if tm_top1 == cur_top1:
                confident_kept += 1

    return {
        "checked": True,
        "n_records": len(recs),
        "n_evaluated": n_eval,
        "n_skipped": skipped,
        "top1_agreement_vs_rule_based": round(agree_rule / n_eval, 4) if n_eval else None,
        "top1_agreement_vs_single_model": round(agree_single / n_single_avail, 4) if n_single_avail else None,
        "n_single_model_available": n_single_avail,
        "decisions_with_empty_bag_in_pool": solo_decisions,
        "winner_regime_distribution": regime_winner,
        "confident_decisions_total": confident_total,
        "confident_decisions_twomodel_kept_pick": confident_kept,
        "confident_keep_rate": round(confident_kept / confident_total, 4) if confident_total else None,
        "note": (
            "Test DEGRADOWANY na cechach geometrycznych: shadow_decisions nie ma surowych "
            "lat/lon → haversine=-1, dist_to_pickup_km=km_to_pickup. Sygnał = routing po "
            "worku + agreement względny, NIE absolutna jakość. Authoritative pairwise = "
            "warstwa (A) na held-out forward (pełne cechy, real data)."
        ),
    }


def _parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        return None


def _compute_rows_from_shadow(base, ctx, cands, km_by_idx):
    """Policz cechy jak _compute_all_candidate_features, ale dystans = km_to_pickup."""
    # Tymczasowo podmień _compute_road_km, by zwracał km z shadow per kolejność.
    import dispatch_v2.ml_inference as mi  # noqa
    # Zbuduj minimalnie: użyjemy oryginalnej metody, ale z pickup_lat=None → dist NaN,
    # potem nadpiszemy dist_road/dist_hav z km_by_idx. Najprościej: wywołać i poprawić.
    rows = base._compute_all_candidate_features(ctx, cands)
    for i, row in enumerate(rows):
        km = km_by_idx.get(i)
        row["dist_to_pickup_km"] = float(km) if km is not None else -1.0
        # haversine niedostępny z shadow → -1 (jak prod gdy brak pozycji)
        row["dist_to_pickup_haversine_km"] = -1.0
    # przelicz pool stats + delta=pool_mean na bazie podmienionych dystansów
    valid = [r["dist_to_pickup_km"] for r in rows if r["dist_to_pickup_km"] is not None and r["dist_to_pickup_km"] >= 0]
    pool_mean = (sum(valid) / len(valid)) if valid else None
    pool_min = min(valid) if valid else -1.0
    pool_max = max(valid) if valid else -1.0
    order = sorted(
        [(i, rows[i]["dist_to_pickup_km"]) for i in range(len(rows)) if rows[i]["dist_to_pickup_km"] >= 0],
        key=lambda x: x[1],
    )
    rank_map = {idx: r + 1 for r, (idx, _) in enumerate(order)}
    for i, row in enumerate(rows):
        row["pool_min_dist_km"] = pool_min
        row["pool_max_dist_km"] = pool_max
        row["rank_by_dist"] = rank_map.get(i, len(rows) + 1)
        d = row["dist_to_pickup_km"]
        row["delta_dist_km"] = (d - pool_mean) if (pool_mean is not None and d >= 0) else 0.0
    return rows


def _score_rows_twomodel(tmm, cands, rows):
    """Routuj + skoruj jak LGBMTwoModelInferer, ale na gotowych rows."""
    import numpy as np
    solo_idx, bundle_idx = [], []
    for i, row in enumerate(rows):
        from dispatch_v2.ml_inference import _bag_axis_level
        ba = _bag_axis_level(row.get("bag_size"), row.get("bag_drops_pending"), row.get("bag_pickup_pending"))
        row["level"] = ba
        (solo_idx if ba == "B" else bundle_idx).append(i)
    rank, score = {}, {}
    for regime, idxs in (("solo", solo_idx), ("bundle", bundle_idx)):
        if not idxs:
            continue
        X = np.array([tmm._encode_row(rows[i], regime) for i in idxs], dtype=float)
        sc = tmm._models[regime].predict(X)
        order = sorted(range(len(idxs)), key=lambda k: -float(sc[k]))
        for r, k in enumerate(order):
            rank[idxs[k]] = r + 1
            score[idxs[k]] = float(sc[k])

    def key(i):
        grp = 0 if i in solo_idx else 1
        return (grp, rank.get(i, 10**6), -score.get(i, -1e9))
    merged = sorted(range(len(cands)), key=key)
    top = merged[0]
    return {
        "winner_cid": str(getattr(cands[top], "courier_id", "")),
        "winner_regime": "solo" if top in solo_idx else "bundle",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--forward-days", type=int, default=14)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--report", default=str(OUT))
    args = ap.parse_args()

    print("[A2] (A) authoritative pairwise per reżim (held-out forward)...")
    a = authoritative_pairwise(args.forward_days)
    print(f"      solo={a['solo']} bundle={a['bundle']} cutoff={a['cutoff_date']}")

    print("[A2] (B) online top-1 agreement na shadow_decisions.jsonl...")
    b = online_top1_agreement(limit=args.limit)
    if b.get("checked"):
        print(f"      n_eval={b['n_evaluated']} skipped={b['n_skipped']}")
        print(f"      top1 vs rule-based  = {b['top1_agreement_vs_rule_based']}")
        print(f"      top1 vs single-model= {b['top1_agreement_vs_single_model']} (n={b['n_single_model_available']})")
        print(f"      confident keep-rate = {b['confident_keep_rate']} (n={b['confident_decisions_total']})")
        print(f"      winner regime dist  = {b['winner_regime_distribution']}")
    else:
        print(f"      pominięte: {b.get('reason')}")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "authoritative_pairwise_forward": a,
        "online_top1_agreement": b,
    }
    json.dump(report, open(args.report, "w"), indent=2, default=str, ensure_ascii=False)
    print(f"\nraport -> {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
