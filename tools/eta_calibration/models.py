#!/usr/bin/env python3
"""eta_calibration.models — korektory kwantylowe per noga, per kurier (z shrinkage).

Dwa poziomy (D — Adrian 2026-07-07):
  L1 EmpiricalQuantile — kwantyle empiryczne per (kontekst) + offset kuriera z EB-shrinkage.
     Interpretowalne, tanie, cold-start = prior kontekstu. Fallback + sanity.
  L2 LGBMQuantile — LightGBM z funkcją kwantylową (pinball); kurier jako cecha (pooling przez
     drzewa) × kontekst × dystans × tempo historyczne. Wpuszczany tylko gdy bije L1 (evaluate).

Cechy historyczne kuriera (tempo/poślizg) liczone WYŁĄCZNIE z train (leakage-safe) — patrz
build_courier_history. Target: ODBIÓR = poślizg vs czas_kuriera; DOSTAWA = czas trwania dostawy.
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Dict, List, Optional

import numpy as np

try:
    import lightgbm as lgb
except Exception:  # pragma: no cover
    lgb = None

PICKUP = "pickup"
DELIVERY = "delivery"

# Kontrakt serwowanego modelu. Pola outcome-only moga zostac w feature-store do
# analiz po fakcie, ale NIE wolno ich wpuscic do _ctx/_row ani artefaktu modelu.
# W szczegolnosci hour/slot/weekday sa liczone z faktycznego pickup, a load /
# is_bundle z zamknietych interwalow pickup..delivery. prep_var_med nie ma
# historycznego, wersjonowanego snapshotu as-of dla holdoutu.
FEATURE_CONTRACT_VERSION = "decision_time_v2"
OUTCOME_ONLY_FIELDS = frozenset({
    "actual_deliver_min",
    "delivered_at",
    "hour",
    "is_bundle",
    "load",
    "pace_deliv",
    "picked_up_at",
    "pickup_slip_koord_min",
    "prep_var_med",
    "slot",
    "weekday",
})
PICKUP_FEATURE_NAMES = ("cid_i", "rest_i", "otype", "hist_slip")
DELIVERY_FEATURE_NAMES = (
    "cid_i", "rest_i", "osrm_km", "osrm_ff", "otype", "hist_pace",
)


def served_feature_names(leg: str) -> tuple[str, ...]:
    """Jawna allowlista cech dostepnych w chwili decyzji."""
    if leg == PICKUP:
        return PICKUP_FEATURE_NAMES
    if leg == DELIVERY:
        return DELIVERY_FEATURE_NAMES
    raise ValueError(f"nieznana noga ETA: {leg!r}")


# ── cechy historyczne kuriera (tylko z train) ──
def build_courier_history(train_rows: List[dict]) -> Dict[str, dict]:
    """{cid: {med_slip, med_pace, n_slip, n_pace}} z rekordów TRAIN. Leakage-safe."""
    slip = defaultdict(list)
    pace = defaultdict(list)
    for r in train_rows:
        cid = r.get("courier_id")
        if cid is None:
            continue
        if r.get("pickup_slip_koord_min") is not None:
            slip[cid].append(r["pickup_slip_koord_min"])
        if r.get("pace_deliv") is not None:
            pace[cid].append(r["pace_deliv"])
    out = {}
    all_cids = set(slip) | set(pace)
    for cid in all_cids:
        s = slip.get(cid, [])
        p = pace.get(cid, [])
        out[cid] = {
            "med_slip": float(np.median(s)) if s else None,
            "med_pace": float(np.median(p)) if p else None,
            "n_slip": len(s), "n_pace": len(p),
        }
    return out


def _median(xs):
    xs = [x for x in xs if x is not None]
    return float(np.median(xs)) if xs else None


# ── L1: empiryczne kwantyle + EB-shrinkage per kurier ──
class EmpiricalQuantileModel:
    """Kwantyle empiryczne per (kontekst), przesunięte offsetem kuriera z EB-shrinkage.

    ODBIÓR:  target=poślizg; kontekst = decision-time typ zlecenia.
    DOSTAWA: target=czas dostawy; kontekst = typ zlecenia + dystans OSRM.
    predict_quantiles(rec) -> {q: wartość skorygowana} (dla ODBIORU: czas_kuriera+offset).
    """

    def __init__(self, leg: str, quantiles: List[float], min_n: int = 30):
        self.leg = leg
        self.qs = quantiles
        self.min_n = min_n
        self.ctx_q: Dict[tuple, Dict[float, float]] = {}
        self.global_q: Dict[float, float] = {}
        self.courier_off: Dict[str, float] = {}
        self.global_mean = 0.0
        self.K = 10.0

    @staticmethod
    def _dist_bucket(km):
        if km is None:
            return "?"
        if km < 2:
            return "s"
        if km < 4:
            return "m"
        return "l"

    def _ctx(self, r) -> tuple:
        otype = 1 if r.get("was_czasowka") else 0
        if self.leg == PICKUP:
            return (otype,)
        return (otype, self._dist_bucket(r.get("osrm_deliv_km")))

    def _target(self, r) -> Optional[float]:
        return r.get("pickup_slip_koord_min") if self.leg == PICKUP else r.get("actual_deliver_min")

    def fit(self, train_rows: List[dict]) -> "EmpiricalQuantileModel":
        vals_by_ctx = defaultdict(list)
        all_vals = []
        by_courier = defaultdict(list)
        for r in train_rows:
            t = self._target(r)
            if t is None:
                continue
            vals_by_ctx[self._ctx(r)].append(t)
            all_vals.append(t)
            if r.get("courier_id"):
                by_courier[r["courier_id"]].append(t)
        if not all_vals:
            return self
        for q in self.qs:
            self.global_q[q] = float(np.quantile(all_vals, q))
        self.global_mean = float(np.mean(all_vals))
        for ctx, vs in vals_by_ctx.items():
            if len(vs) >= 8:
                self.ctx_q[ctx] = {q: float(np.quantile(vs, q)) for q in self.qs}
        # EB shrinkage offset per kurier (na medianie odchylenia od globalnej)
        means = {c: float(np.mean(v)) for c, v in by_courier.items() if len(v) >= 5}
        if len(means) > 1:
            between = float(np.var(list(means.values())))
            within = float(np.mean([np.var(v) for v in by_courier.values() if len(v) >= 5]))
            self.K = max(3.0, min(60.0, within / max(0.1, between)))
        for c, v in by_courier.items():
            nc = len(v)
            mc = float(np.mean(v))
            self.courier_off[c] = (mc - self.global_mean) * nc / (nc + self.K)
        return self

    def predict_quantiles(self, r) -> Dict[float, float]:
        ctx = self._ctx(r)
        base = self.ctx_q.get(ctx) or self.global_q
        off = self.courier_off.get(r.get("courier_id"), 0.0)
        out = {}
        for q in self.qs:
            v = base.get(q, self.global_q.get(q, 0.0)) + off
            if self.leg == PICKUP:
                # skorygowany poślizg → dodaj do czas_kuriera nie tutaj (predict jako poślizg);
                # zwracamy skorygowany POŚLIZG (evaluate dodaje do czas_kuriera bazy).
                out[q] = v
            else:
                out[q] = max(1.0, v)
        return out

    def to_artifact(self) -> dict:
        """Przenosny, JSON-serializable zapis modelu L1."""
        return {
            "kind": "L1_empirical",
            "leg": self.leg,
            "quantiles": [float(q) for q in self.qs],
            "min_n": self.min_n,
            "K": self.K,
            "global_mean": self.global_mean,
            "global_q": {str(q): float(v) for q, v in self.global_q.items()},
            "ctx_q": [
                {
                    "ctx": list(ctx),
                    "values": {str(q): float(v) for q, v in vals.items()},
                }
                for ctx, vals in sorted(self.ctx_q.items(), key=lambda item: repr(item[0]))
            ],
            "courier_offset": {str(k): float(v) for k, v in self.courier_off.items()},
        }

    @classmethod
    def from_artifact(cls, artifact: dict) -> "EmpiricalQuantileModel":
        if artifact.get("kind") != "L1_empirical":
            raise ValueError("artefakt nie jest modelem L1")
        model = cls(
            artifact["leg"],
            [float(q) for q in artifact["quantiles"]],
            int(artifact.get("min_n", 30)),
        )
        model.K = float(artifact.get("K", 10.0))
        model.global_mean = float(artifact.get("global_mean", 0.0))
        model.global_q = {float(q): float(v) for q, v in artifact.get("global_q", {}).items()}
        model.ctx_q = {
            tuple(entry["ctx"]): {
                float(q): float(v) for q, v in entry.get("values", {}).items()
            }
            for entry in artifact.get("ctx_q", [])
        }
        model.courier_off = {
            str(k): float(v) for k, v in artifact.get("courier_offset", {}).items()
        }
        return model


# ── L2: LightGBM kwantylowy ──
class LGBMQuantileModel:
    """LightGBM z pinball loss per kwantyl. Kurier + restauracja jako kategorie (pooling)."""

    CAT_PICKUP = ["cid_i", "rest_i"]
    NUM_PICKUP = ["otype", "hist_slip"]
    CAT_DELIV = ["cid_i", "rest_i"]
    NUM_DELIV = ["osrm_km", "osrm_ff", "otype", "hist_pace"]

    def __init__(self, leg: str, quantiles: List[float], params: dict):
        self.leg = leg
        self.qs = quantiles
        self.params = params
        self.models: Dict[float, object] = {}
        self.cid_map: Dict[str, int] = {}
        self.rest_map: Dict[str, int] = {}
        self.chist: Dict[str, dict] = {}
        self.feat_names: List[str] = []
        self.cat_idx: List[int] = []

    def _target(self, r):
        return r.get("pickup_slip_koord_min") if self.leg == PICKUP else r.get("actual_deliver_min")

    def _row(self, r) -> Optional[list]:
        ch = self.chist.get(r.get("courier_id"), {})
        cid_i = self.cid_map.get(r.get("courier_id"), -1)
        rest_i = self.rest_map.get(_rest_key(r), -1)
        if self.leg == PICKUP:
            hist = ch.get("med_slip")
            return [cid_i, rest_i, r.get("was_czasowka") or 0,
                    hist if hist is not None else 0.0]
        # delivery — wymaga osrm
        if r.get("osrm_deliv_ff_min") is None:
            return None
        hist = ch.get("med_pace")
        return [cid_i, rest_i, r.get("osrm_deliv_km") or 0.0, r.get("osrm_deliv_ff_min") or 0.0,
                r.get("was_czasowka") or 0,
                hist if hist is not None else 3.0]

    def fit(self, train_rows: List[dict], chist: Dict[str, dict]) -> "LGBMQuantileModel":
        if lgb is None:
            raise RuntimeError("lightgbm niedostępny")
        self.chist = chist
        cids = sorted({r["courier_id"] for r in train_rows if r.get("courier_id")})
        self.cid_map = {c: i for i, c in enumerate(cids)}
        rests = sorted({_rest_key(r) for r in train_rows if _rest_key(r)})
        self.rest_map = {c: i for i, c in enumerate(rests)}
        if self.leg == PICKUP:
            self.feat_names = list(served_feature_names(PICKUP))
            self.cat_idx = [0, 1]
        else:
            self.feat_names = list(served_feature_names(DELIVERY))
            self.cat_idx = [0, 1]
        X, y = [], []
        for r in train_rows:
            t = self._target(r)
            row = self._row(r)
            if t is None or row is None:
                continue
            X.append(row)
            y.append(t)
        X = np.array(X, dtype=float)
        y = np.array(y, dtype=float)
        base = dict(objective="quantile", num_leaves=self.params.get("num_leaves", 15),
                    min_child_samples=self.params.get("min_child_samples", 30),
                    learning_rate=self.params.get("learning_rate", 0.05),
                    lambda_l2=self.params.get("lambda_l2", 1.0), verbose=-1)
        n_est = self.params.get("n_estimators", 250)
        for q in self.qs:
            ds = lgb.Dataset(X, label=y, categorical_feature=self.cat_idx, free_raw_data=False)
            self.models[q] = lgb.train({**base, "alpha": q}, ds, num_boost_round=n_est)
        return self

    def predict_quantiles(self, r) -> Optional[Dict[float, float]]:
        row = self._row(r)
        if row is None:
            return None
        X = np.array([row], dtype=float)
        out = {}
        for q in self.qs:
            v = float(self.models[q].predict(X)[0])
            out[q] = v if self.leg == PICKUP else max(1.0, v)
        # monotoniczność kwantyli (sort — LightGBM per-q może się krzyżować)
        sq = sorted(self.qs)
        vals = sorted(out[q] for q in sq)
        return {q: vals[i] for i, q in enumerate(sq)}

    def to_artifact(self) -> dict:
        """Pelny artefakt L2: boostery + mapowania potrzebne do odtworzenia predykcji."""
        if not self.models:
            raise ValueError("nie mozna zapisac niedopasowanego modelu L2")
        return {
            "kind": "L2_lgbm",
            "leg": self.leg,
            "quantiles": [float(q) for q in self.qs],
            "params": dict(self.params),
            "cid_map": dict(self.cid_map),
            "rest_map": dict(self.rest_map),
            "courier_history": self.chist,
            "feature_names": list(self.feat_names),
            "categorical_indices": list(self.cat_idx),
            "boosters": {
                str(q): booster.model_to_string() for q, booster in self.models.items()
            },
        }

    @classmethod
    def from_artifact(cls, artifact: dict) -> "LGBMQuantileModel":
        if lgb is None:
            raise RuntimeError("lightgbm niedostepny")
        if artifact.get("kind") != "L2_lgbm":
            raise ValueError("artefakt nie jest modelem L2")
        leg = artifact["leg"]
        expected = list(served_feature_names(leg))
        if artifact.get("feature_names") != expected:
            raise ValueError("artefakt L2 ma niezgodny kontrakt cech")
        model = cls(
            leg,
            [float(q) for q in artifact["quantiles"]],
            dict(artifact.get("params") or {}),
        )
        model.cid_map = {str(k): int(v) for k, v in artifact.get("cid_map", {}).items()}
        model.rest_map = {str(k): int(v) for k, v in artifact.get("rest_map", {}).items()}
        model.chist = dict(artifact.get("courier_history") or {})
        model.feat_names = expected
        model.cat_idx = [int(v) for v in artifact.get("categorical_indices", [0, 1])]
        model.models = {
            float(q): lgb.Booster(model_str=model_str)
            for q, model_str in artifact.get("boosters", {}).items()
        }
        if set(model.models) != set(model.qs):
            raise ValueError("artefakt L2 nie zawiera wszystkich kwantyli")
        return model


def model_to_artifact(model) -> dict:
    if isinstance(model, EmpiricalQuantileModel):
        return model.to_artifact()
    if isinstance(model, LGBMQuantileModel):
        return model.to_artifact()
    raise TypeError(f"nieobslugiwany model ETA: {type(model).__name__}")


def model_from_artifact(artifact: dict):
    kind = artifact.get("kind")
    if kind == "L1_empirical":
        return EmpiricalQuantileModel.from_artifact(artifact)
    if kind == "L2_lgbm":
        return LGBMQuantileModel.from_artifact(artifact)
    raise ValueError(f"nieznany rodzaj artefaktu ETA: {kind!r}")


def _rest_key(r) -> Optional[str]:
    # Replay moze podstawic anonimowy klucz; normalny store uzywa coords bez tekstu adresu.
    if r.get("restaurant_key"):
        return str(r["restaurant_key"])
    la, lo = r.get("rest_lat"), r.get("rest_lon")
    if la is not None and lo is not None:
        return f"{la:.3f},{lo:.3f}"
    return None
