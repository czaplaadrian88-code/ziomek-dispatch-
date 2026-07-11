#!/usr/bin/env python3
"""eta_calibration.calibrate — GŁÓWNY JOB DZIENNY (idempotentny, deterministyczny, tryb CIEŃ).

Kroki:
  1. (opcja --rebuild-features) zbuduj feature-store z logów Ziomka (READ-ONLY).
  2. Walidacja walk-forward (evaluate) → metryki + istotność.
  3. Champion/challenger: odtworz championa i challengera na identycznym,
     zamrozonym supporcie; wymagaj progu poprawy + paired CI/Wilcoxon.
  4. Zawsze zapisz osobny artifact kandydata. Mape championa podmien tylko po
     pelnym gate; brak/legacy artifact = HOLD.
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
from dispatch_v2.tools.eta_calibration import promotion as P

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


def _map_path(cfg: dict, leg: str, candidate: bool = False) -> str:
    suffix = "_candidate_map" if candidate else "_map"
    return cfg["paths"][f"{leg}{suffix}"]


def _public_decision(decision: dict) -> dict:
    return {k: v for k, v in decision.items() if not k.startswith("_")}


def champion_challenger(new_metrics: dict, cfg: dict, rows: list[dict]) -> dict:
    """Exact-support paired gate vs odtwarzalny artefakt obecnego championa.

    Legacy mapa, brak mapy albo dowolna niespojnosc artefaktu oznacza HOLD.
    Kandydat jest wtedy nadal zapisywany osobno, ale nigdy nie podmienia mapy
    championa.
    """
    _, rolling_hold, rolling_cut = E.time_split(
        rows, cfg["window"]["holdout_days"],
    )
    decision = {}
    for leg, metrics in new_metrics["legs"].items():
        challenger_name = metrics["champion"]
        incumbent, artifact_reason = P.load_model_payload(_map_path(cfg, leg), leg)
        if incumbent is None:
            dec = P.hold_decision(leg, challenger_name, artifact_reason)
            evaluation_model = metrics["_models"][challenger_name]
            evidence_rows = rolling_hold
            evidence_cut = rolling_cut
        else:
            evidence = incumbent["promotion_evidence"]
            evidence_cut = evidence["holdout_cut_day"]
            frozen_train = [
                row for row in rows if row.get("day") and row["day"] < evidence_cut
            ]
            if not frozen_train:
                dec = P.hold_decision(leg, challenger_name, "frozen_train_missing")
                evaluation_model = metrics["_models"][challenger_name]
                evidence_rows = rolling_hold
                evidence_cut = rolling_cut
            else:
                evaluation_model = E.fit_model(
                    frozen_train, leg, cfg, challenger_name,
                )
                dec = P.compare_on_frozen_support(
                    evaluation_model, rows, incumbent, cfg, leg, challenger_name,
                )
                expected_keys = {
                    rec["key"] for rec in evidence.get("predictions", [])
                }
                evidence_rows = [
                    row for row in rows if P.support_key(row) in expected_keys
                ]
        dec["rolling_challenger_mae"] = metrics["models"][challenger_name]["mae"]
        dec["rolling_n_holdout"] = metrics.get("n_holdout")
        dec["_evaluation_model"] = evaluation_model
        dec["_evidence_rows"] = evidence_rows
        dec["_holdout_cut_day"] = evidence_cut
        decision[leg] = dec
    return decision


def write_runtime_maps(
    train_full: list[dict], cfg: dict, decision: dict, now_iso: str,
) -> dict:
    """Zapisz kandydata; championa podmien tylko po pelnym paired gate."""
    writes = {}
    for leg, dec in decision.items():
        evidence_model = dec.get("_evaluation_model")
        evidence_rows = dec.get("_evidence_rows") or []
        evidence_cut = dec.get("_holdout_cut_day")
        if evidence_model is None or not evidence_rows or not evidence_cut:
            writes[leg] = {"candidate_written": False, "champion_written": False}
            log.warning("noga %s: brak kompletnego evidence, zero zapisu map", leg)
            continue
        challenger_name = dec["challenger"]
        runtime_model = E.fit_model(train_full, leg, cfg, challenger_name)
        payload = P.build_model_payload(
            leg=leg,
            model_name=challenger_name,
            runtime_model=runtime_model,
            evaluation_model=evidence_model,
            evidence_rows=evidence_rows,
            holdout_cut_day=evidence_cut,
            generated_at=now_iso,
            operational_quantile=cfg["model"]["operational_quantile"],
        )
        candidate_path = _map_path(cfg, leg, candidate=True)
        encoded = json.dumps(payload, ensure_ascii=False, indent=2)
        _atomic_write(candidate_path, encoded)
        champion_written = False
        if dec["promote"]:
            _atomic_write(_map_path(cfg, leg), encoded)
            champion_written = True
        else:
            log.info("noga %s: HOLD, champion zostaje bez zmian", leg)
        writes[leg] = {
            "candidate_written": True,
            "champion_written": champion_written,
            "artifact_sha256": payload["artifact_sha256"],
            "n_frozen_support": payload["promotion_evidence"]["n_support"],
        }
    return writes


def write_shadow(hold, cfg: dict, metrics: dict, now_iso: str):
    """Shadow-predykcje championa na holdoucie (pred vs real) → eta_calib_shadow.jsonl."""
    opq = cfg["model"]["operational_quantile"]
    n = 0
    for leg in (M.PICKUP, M.DELIVERY):
        leg_metrics = metrics["legs"][leg]
        model = leg_metrics["_models"][leg_metrics["champion"]]
        for r in hold:
            q = model.predict_quantiles(r)
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
    rows = E.load_store(cfg["paths"]["db"])
    _, hold, _ = E.time_split(rows, cfg["window"]["holdout_days"])
    metrics = E.run(cfg)
    decision = champion_challenger(metrics, cfg, rows)
    map_writes = write_runtime_maps(rows, cfg, decision, now_iso)
    write_shadow(hold, cfg, metrics, now_iso)
    record = {
        "logged_at": now_iso, "holdout_cut_day": metrics["holdout_cut_day"],
        "instrument_status": (
            "PROMOTE" if all(d["promote"] for d in decision.values()) else "HOLD"
        ),
        "promoted": all(d["promote"] for d in decision.values()),
        "decision": {leg: _public_decision(d) for leg, d in decision.items()},
        "map_writes": map_writes,
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
