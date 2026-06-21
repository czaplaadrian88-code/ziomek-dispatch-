#!/usr/bin/env python3
"""[C2 live-shadow] Ciągły monitor prep-bias na żywym backfillu (ACK Adrian 21.06:
p70/median + live-shadow, STOP przed flipem).

Po co: pojedynczy replay (21.06) dał NO-GO dla p80 i ~rzut-monetą dla median/p70,
ale na MAŁYM czystym podzbiorze (matched_only proposed==final, n≈200). Ten monitor
re-mierzy codziennie na rolling-14d backfillu (odświeżanym przez dispatch-faza7-kpi
~04:00 UTC) i dopisuje kompaktowy rekord metryk → akumulacja dowodu czy median/p70
precyzja stabilnie >0.5 i jak wygląda opt/pess w czasie/regime'ach.

KRYTYCZNE: to NIE jest flip. Flaga ENABLE_PREP_BIAS_TABLE pozostaje OFF — korekta
NIE wpływa na realne decyzje R6. Read-only na logach; zapis WYŁĄCZNIE do
dispatch_state/prep_bias_shadow_metrics.jsonl (plik nieczytany przez żaden
decydujący kod). Hot-path (feasibility_v2) NIETKNIĘTY, zero restartu.

Uwaga metodologiczna: opt/pess (czy redirect dojechałby na czas) jest NIEREDUKOWALNE
offline — rozstrzyga je dopiero realny flip. Monitor zawęża szum małego n i łapie
dryf; decyzja o flipie pozostaje ACK-gated.

python3. Uruchamiany przez prep-bias-shadow-monitor.timer (dziennie po backfillu).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_PKG_PARENT = os.path.dirname(os.path.dirname(_HERE))  # .../scripts
if _PKG_PARENT not in sys.path:
    sys.path.insert(0, _PKG_PARENT)

from dispatch_v2.tools import prep_bias_decision_time_replay as rep  # noqa: E402

OUT = "/root/.openclaw/workspace/dispatch_state/prep_bias_shadow_metrics.jsonl"
HIGHLIGHT = ("p70", "median")  # warianty ACK-owane do śledzenia


def _compact(res: dict) -> dict:
    cm = res.get("pred_quality_vs_real_breach", {})
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "framing": res.get("framing"),
        "matched_only": res.get("matched_only"),
        "n_decisions": res.get("n_decisions_total"),
        "n_with_outcome": res.get("n_with_outcome"),
        "pred_precision": cm.get("precision_pred_gt35"),
        "pred_recall": cm.get("recall_pred_gt35"),
        "table_global_bias": res.get("table_global_bias"),
        "variants": {},
    }
    for v, d in res.get("variants", {}).items():
        a = d.get("agg", {})
        rec["variants"][v] = {
            "flips": a.get("n_flips"),
            "correct": a.get("n_flip_correct"),
            "false": a.get("n_flip_false"),
            "unknown": a.get("n_flip_unknown"),
            "precision": a.get("flip_precision"),
            "ontime_before": a.get("on_time_before"),
            "ontime_opt": a.get("on_time_after_optimistic"),
            "ontime_pess": a.get("on_time_after_pessimistic"),
        }
    return rec


def _append_jsonl(path: str, rec: dict) -> None:
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    # append atomowo: read-modify nie potrzebny (append-only), ale piszemy przez
    # temp+concat aby uniknąć częściowego wpisu przy crashu.
    prev = ""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            prev = f.read()
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(prev + line)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main() -> int:
    res = rep.run(framing="table", matched_only=True)
    rec = _compact(res)
    _append_jsonl(OUT, rec)
    # zwięzły stdout (journal)
    print(f"[{rec['ts']}] prep-bias shadow (matched_only): "
          f"n={rec['n_with_outcome']} pred_prec={rec['pred_precision']}")
    for v in HIGHLIGHT:
        vd = rec["variants"].get(v, {})
        print(f"  {v}: flips={vd.get('flips')} prec={vd.get('precision')} "
              f"ontime {vd.get('ontime_before')}→opt {vd.get('ontime_opt')}/"
              f"pess {vd.get('ontime_pess')}")
    print(f"  → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
