"""PICKUP-SLIP MODEL v3 — „wzór spóźnień" (Adrian 2026-07-06).

Nocny trener modelu poślizgu obietnicy: LGBM kwantylowy (mediana) na cechach
znanych W CHWILI DECYZJI + osobista korekta per-kurier ze shrinkage.

AUTONOMIA NOWEGO KURIERA (wymóg Adriana 06.07): zero kroków ręcznych —
model globalny obejmuje nowego od 1. propozycji (tier "new" + cechy decyzji),
a jego osobisty offset startuje od 0 i uczy się sam z każdą dostawą przy
kolejnym nocnym treningu: offset = (n/(n+K)) * mediana_reszty_kuriera.
Kurier spoza artefaktu (świeżo wpięty autopairem) => offset 0 z definicji.

CECHY (leak-free; pred_age ŚWIADOMIE wykluczony — w chwili obietnicy = 0):
bag_size (cap 4), hour_warsaw, weekday, is_weekend, tier (kategoryczny),
r6_max_bag_time_min, total_duration_min. Bliźniak #15: konsument silnika
musi podać TE SAME pola (mapa parytetu w artefakcie: "features").

Populacja treningu: matched_courier & nie-czasówka & |err|<=120 (jak analiza
06.07). Target: eta_error_min (proxy poślizgu odbioru — dekompozycja 29.06;
TODO v3.1: bezpośredni poślizg odbioru z picked_up_at).

Artefakt: dispatch_state/pickup_slip_model.json
  {trained_at, n_train, n_test, window_days, features, tier_levels,
   oos: {mae_const, mae_model, medae_const, medae_model},
   courier_offsets: {cid: min}, shrinkage_k, model_txt (lgbm)}

Uruchomienie: nocny timer dispatch-pickup-slip-model (22:45 UTC) albo ręcznie
`python -m dispatch_v2.tools.pickup_slip_model [--days 60] [--dry-run]`.
Read-only wobec silnika; jedyny zapis = artefakt (atomic temp+rename).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from collections import defaultdict
from datetime import datetime
from statistics import median
from typing import Dict, List, Optional

_SCRIPTS = "/root/.openclaw/workspace/scripts"
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from dispatch_v2.common import setup_logger

ETA_CAL = "/root/.openclaw/workspace/dispatch_state/eta_calibration_log.jsonl"
TIERS_PATH = "/root/.openclaw/workspace/dispatch_state/courier_tiers.json"
ARTIFACT = "/root/.openclaw/workspace/dispatch_state/pickup_slip_model.json"
LOG_DIR = _SCRIPTS + "/logs/"
_log = setup_logger("pickup_slip_model", LOG_DIR + "pickup_slip_model.log")

FEATURES = ["bag", "hour", "weekday", "weekend", "tier_idx", "r6max", "total_dur"]
SHRINKAGE_K = 30.0   # offset kuriera = n/(n+K) * mediana reszty (nowy => ~0)
MIN_TRAIN = 800      # poniżej — nie nadpisuj artefaktu (za mało danych)


def _tier_map() -> Dict[str, str]:
    try:
        raw = json.load(open(TIERS_PATH))
    except Exception:
        return {}
    out = {}
    for cid, v in raw.items():
        if cid == "_meta" or not isinstance(v, dict):
            continue
        out[str(cid)] = (v.get("bag") or {}).get("tier") or "unknown"
    return out


def load_rows(days: int) -> List[dict]:
    tier_of = _tier_map()
    rows: List[dict] = []
    try:
        lines = open(ETA_CAL).read().splitlines()
    except FileNotFoundError:
        return rows
    now = datetime.now().astimezone()
    for line in lines:
        try:
            d = json.loads(line)
        except Exception:
            continue
        err = d.get("eta_error_min")
        if not isinstance(err, (int, float)) or abs(err) > 120:
            continue
        if not d.get("matched_courier") or d.get("was_czasowka"):
            continue
        bag = d.get("bag_size")
        hour = d.get("hour_warsaw")
        if not isinstance(bag, int) or bag < 1 or not isinstance(hour, int):
            continue
        try:
            dt = datetime.fromisoformat(d.get("logged_at") or "")
        except Exception:
            continue
        if dt.tzinfo is None:
            dt = dt.astimezone()
        if (now - dt).days > days:
            continue
        cid = str(d.get("real_courier_id") or "")
        rows.append({
            "err": float(err),
            "bag": min(bag, 4),
            "hour": hour,
            "weekday": d.get("weekday") if isinstance(d.get("weekday"), int) else -1,
            "weekend": 1 if d.get("is_weekend") else 0,
            "tier": tier_of.get(cid, "unknown"),
            "r6max": float(d.get("r6_max_bag_time_min") or 0.0),
            "total_dur": float(d.get("total_duration_min") or 0.0),
            "cid": cid,
            "day": dt.date(),
        })
    return rows


def train(days: int = 60) -> Optional[dict]:
    import numpy as np
    import lightgbm as lgb

    rows = load_rows(days)
    if len(rows) < MIN_TRAIN:
        _log.warning(f"za mało danych: n={len(rows)} < {MIN_TRAIN} — artefakt NIE nadpisany")
        return None
    day_list = sorted({r["day"] for r in rows})
    tier_levels = sorted({r["tier"] for r in rows})
    t_idx = {t: i for i, t in enumerate(tier_levels)}

    def feats(r):
        return [r["bag"], r["hour"], r["weekday"], r["weekend"],
                t_idx[r["tier"]], r["r6max"], r["total_dur"]]

    X = np.array([feats(r) for r in rows], dtype=float)
    y = np.array([r["err"] for r in rows])
    dts = np.array([day_list.index(r["day"]) for r in rows])
    split = int(len(day_list) * 0.7)
    tr, te = dts < split, dts >= split

    params = dict(objective="quantile", alpha=0.5, n_estimators=300,
                  learning_rate=0.05, num_leaves=15, min_child_samples=60,
                  random_state=42, verbose=-1)
    m = lgb.LGBMRegressor(**params)
    m.fit(X[tr], y[tr], categorical_feature=[2, 4])

    const = float(np.median(y[tr]))
    p_te = m.predict(X[te])
    oos = {
        "mae_const": round(float(np.mean(np.abs(y[te] - const))), 2),
        "mae_model": round(float(np.mean(np.abs(y[te] - p_te))), 2),
        "medae_const": round(float(np.median(np.abs(y[te] - const))), 2),
        "medae_model": round(float(np.median(np.abs(y[te] - p_te))), 2),
    }

    # finalny model na CAŁOŚCI (artefakt produkcyjny); OOS z time-splitu wyżej
    mf = lgb.LGBMRegressor(**params)
    mf.fit(X, y, categorical_feature=[2, 4])

    # osobiste korekty per-kurier ze shrinkage (reszty względem finalnego modelu)
    res = defaultdict(list)
    p_all = mf.predict(X)
    for i, r in enumerate(rows):
        if r["cid"]:
            res[r["cid"]].append(float(y[i] - p_all[i]))
    offsets = {}
    for cid, v in res.items():
        n = len(v)
        off = (n / (n + SHRINKAGE_K)) * median(v)
        if abs(off) >= 0.5:
            offsets[cid] = round(off, 2)

    art = {
        "trained_at": datetime.now().astimezone().isoformat(),
        "window_days": days,
        "n_train": int(tr.sum()), "n_test": int(te.sum()), "n_total": len(rows),
        "features": FEATURES,
        "tier_levels": tier_levels,
        "shrinkage_k": SHRINKAGE_K,
        "oos": oos,
        "courier_offsets": offsets,
        "model_txt": mf.booster_.model_to_string(),
    }
    return art


def save_artifact(art: dict) -> None:
    d = os.path.dirname(ARTIFACT)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-psm-")
    with os.fdopen(fd, "w") as f:
        json.dump(art, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, ARTIFACT)


def predict_from_artifact(art: dict, *, bag: int, hour: int, weekday: int,
                          weekend: int, tier: str, r6max: float,
                          total_dur: float, cid: Optional[str] = None
                          ) -> Optional[float]:
    """Predykcja poślizgu (min) z artefaktu — używane też przez silnik (twin).

    Nowy kurier: brak w courier_offsets => offset 0 (model globalny działa
    od pierwszej propozycji; offset nauczy się sam kolejnymi nocami).
    """
    try:
        import lightgbm as lgb
        booster = lgb.Booster(model_str=art["model_txt"])
        t_levels = art.get("tier_levels") or []
        t_i = t_levels.index(tier) if tier in t_levels else (
            t_levels.index("unknown") if "unknown" in t_levels else 0)
        x = [[min(int(bag), 4), int(hour), int(weekday), int(weekend),
              t_i, float(r6max), float(total_dur)]]
        pred = float(booster.predict(x)[0])
        off = float((art.get("courier_offsets") or {}).get(str(cid), 0.0))
        return pred + off
    except Exception as e:  # noqa: BLE001 — fail-open (konsument da fallback)
        _log.warning(f"predict_from_artifact fail: {type(e).__name__}: {e}")
        return None


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    days = 60
    if "--days" in argv:
        days = int(argv[argv.index("--days") + 1])
    art = train(days)
    if art is None:
        return 0  # za mało danych — poprzedni artefakt zostaje
    if "--dry-run" in argv:
        _log.info(f"[dry] n={art['n_total']} oos={art['oos']} offsets={len(art['courier_offsets'])}")
        print(json.dumps({k: art[k] for k in ("n_total", "oos")}, indent=1))
        return 0
    save_artifact(art)
    _log.info(
        f"artefakt zapisany: n={art['n_total']} oos={art['oos']} "
        f"offsets={len(art['courier_offsets'])} -> {ARTIFACT}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
