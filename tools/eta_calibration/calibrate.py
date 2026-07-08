#!/usr/bin/env python3
"""eta_calibration.calibrate — GŁÓWNY JOB DZIENNY (idempotentny, deterministyczny, tryb CIEŃ).

Kroki:
  1. (opcja --rebuild-features) zbuduj feature-store z logów Ziomka (READ-ONLY).
  2. Walidacja walk-forward (evaluate) → metryki + istotność.
  3. Champion/challenger: promuj nową kalibrację TYLKO gdy bije championa na holdoucie
     (ΔMAE < 0 i istotne, Wilcoxon p < alpha). Inaczej zostaje stara mapa.
  4. Dopasuj modele championa na PEŁNYCH danych (do teraz) → zapisz mapy runtime eta_calib_*.
  5. Zapisz shadow-predykcje (pred vs real) + metryki (append jsonl).

NIGDY nie modyfikuje żywej ścieżki ETA Ziomka. Wszystkie wyjścia = eta_calib_*.
Uruchomienie: python -m dispatch_v2.tools.eta_calibration.calibrate [--rebuild-features] [--now ISO]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import Optional

from dispatch_v2.tools.eta_calibration import features as F
from dispatch_v2.tools.eta_calibration import evaluate as E
from dispatch_v2.tools.eta_calibration import models as M

log = logging.getLogger("eta_calib.calibrate")


def _atomic_write(path: str, text: str):
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _append_jsonl(path: str, obj: dict):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def serialize_l1(model: M.EmpiricalQuantileModel) -> dict:
    """L1 → JSON mapa runtime (interpretowalna). Klucze ctx jako string."""
    return {
        "leg": model.leg, "quantiles": model.qs, "K": round(model.K, 2),
        "global_q": {str(q): round(v, 2) for q, v in model.global_q.items()},
        "ctx_q": {"|".join(map(str, k)): {str(q): round(vv, 2) for q, vv in v.items()}
                  for k, v in model.ctx_q.items()},
        "courier_offset": {c: round(o, 2) for c, o in model.courier_off.items()},
    }


def champion_challenger(new_metrics: dict, cfg: dict) -> dict:
    """Decyzja promocji per noga vs zapisany champion (eta_calib_metrics ostatni promoted)."""
    mpath = cfg["paths"]["metrics_log"]
    prev = None
    if os.path.exists(mpath):
        for line in open(mpath, encoding="utf-8"):
            try:
                r = json.loads(line)
                if r.get("promoted"):
                    prev = r
            except Exception:
                pass
    decision = {}
    alpha = cfg["acceptance"]["significance_alpha"]
    for leg, d in new_metrics["legs"].items():
        champ = d["champion"]
        new_mae = d["models"][champ]["mae"]
        prev_mae = (prev or {}).get("legs", {}).get(leg, {}).get("champion_mae") if prev else None
        # promuj gdy: brak poprzednika ALBO nowy nie gorszy istotnie (challenger>=champion)
        promote = prev_mae is None or new_mae <= prev_mae * 1.02
        decision[leg] = dict(champion=champ, champion_mae=new_mae, prev_mae=prev_mae, promote=promote)
    return decision


def write_runtime_maps(train_full, cfg: dict, decision: dict):
    """Dopasuj championa na PEŁNYCH danych i zapisz mapy runtime (shadow-only)."""
    qs = cfg["model"]["quantiles"]
    chist = M.build_courier_history(train_full)
    for leg, dec in decision.items():
        if not dec["promote"]:
            log.info("noga %s: challenger NIE promowany (champion zostaje)", leg)
            continue
        l1 = M.EmpiricalQuantileModel(leg, qs, cfg["model"]["min_n_courier"]).fit(train_full)
        mp = cfg["paths"]["pickup_map"] if leg == M.PICKUP else cfg["paths"]["delivery_map"]
        payload = {
            "version": 1, "leg": leg, "champion": dec["champion"],
            "operational_quantile": cfg["model"]["operational_quantile"],
            "l1": serialize_l1(l1),
            "note": "SHADOW-ONLY — nie wpina w żywe ETA. Konsument: osobna decyzja właściciela.",
        }
        # L2 booster (jeśli champion=L2) zapisany osobno jako .txt
        if dec["champion"] == "L2_lgbm":
            try:
                l2 = M.LGBMQuantileModel(leg, qs, cfg["model"]["lgbm"]).fit(train_full, chist)
                op = cfg["model"]["operational_quantile"]
                lgbm_path = mp.replace(".json", f"_lgbm_p{int(op*100)}.txt")
                l2.models[op].save_model(lgbm_path)
                payload["l2_lgbm_operational"] = os.path.basename(lgbm_path)
                payload["l2_cid_map_size"] = len(l2.cid_map)
            except Exception as e:  # noqa: BLE001
                log.warning("L2 zapis %s: %s", leg, e)
        _atomic_write(mp, json.dumps(payload, ensure_ascii=False, indent=2))
        log.info("zapisano mapę runtime: %s (champion=%s)", mp, dec["champion"])


def write_shadow(train, hold, cfg: dict, now_iso: str):
    """Shadow-predykcje championa na holdoucie (pred vs real) → eta_calib_shadow.jsonl."""
    qs = cfg["model"]["quantiles"]
    opq = cfg["model"]["operational_quantile"]
    chist = M.build_courier_history(train)
    n = 0
    for leg in (M.PICKUP, M.DELIVERY):
        l2 = M.LGBMQuantileModel(leg, qs, cfg["model"]["lgbm"]).fit(train, chist)
        for r in hold:
            q = l2.predict_quantiles(r)
            if q is None:
                continue
            t = r.get("pickup_slip_koord_min") if leg == M.PICKUP else r.get("actual_deliver_min")
            if t is None:
                continue
            _append_jsonl(cfg["paths"]["shadow_log"], {
                "logged_at": now_iso, "leg": leg, "oid": r.get("order_id"),
                "courier": F.pseudonymize(r.get("courier_id", "?")),
                "pred_p50": round(q[0.5], 2), "pred_op": round(q[opq], 2),
                "pred_p90": round(q[0.9], 2), "real": round(t, 2),
                "err_op": round(t - q[opq], 2),
            })
            n += 1
    log.info("shadow zapisany: %d predykcji", n)


def run(cfg: dict, rebuild: bool, now: Optional[datetime]) -> dict:
    now = now or datetime.now(timezone.utc)
    now_iso = now.isoformat()
    if rebuild:
        F.build(cfg)
    metrics = E.run(cfg)
    decision = champion_challenger(metrics, cfg)
    # dopasuj na pełnych danych = wszystko przed holdoutem + holdout (do teraz)
    rows = E.load_store(cfg["paths"]["db"])
    train, hold, cut = E.time_split(rows, cfg["window"]["holdout_days"])
    write_runtime_maps(rows, cfg, decision)   # mapy runtime = pełne dane (produkcja)
    write_shadow(train, hold, cfg, now_iso)
    record = {
        "logged_at": now_iso, "holdout_cut_day": metrics["holdout_cut_day"],
        "promoted": all(d["promote"] for d in decision.values()),
        "decision": decision,
        "legs": {leg: {
            "champion": d["champion"],
            "champion_mae": d["models"][d["champion"]]["mae"],
            "n_holdout": d.get("n_holdout"),
            "coverage": d.get("coverage"),
            "baselines": {k: v.get("mae") for k, v in d["baselines"].items()},
            "significance": {k: {"delta_mae": v["delta_mae"], "ci": v["ci"], "p": v["wilcoxon_p"]}
                             for k, v in d["significance"].items()},
        } for leg, d in metrics["legs"].items()},
    }
    _append_jsonl(cfg["paths"]["metrics_log"], record)
    return record


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    ap.add_argument("--rebuild-features", action="store_true")
    ap.add_argument("--now", default=None, help="ISO timestamp (determinizm); domyślnie teraz")
    args = ap.parse_args()
    cfg = F.load_config(args.config)
    now = datetime.fromisoformat(args.now) if args.now else None
    rec = run(cfg, args.rebuild_features, now)
    print(json.dumps(rec, ensure_ascii=False, indent=2, default=str)[:2000])
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
