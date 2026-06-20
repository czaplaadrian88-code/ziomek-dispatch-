import os, sys
os.environ.setdefault("OMP_NUM_THREADS", "2")
SCRIPTS = "/root/.openclaw/workspace/scripts"; MLP = SCRIPTS + "/ml_data_prep"
for p in (SCRIPTS, MLP, SCRIPTS + "/dispatch_v2/ml_data_prep"):
    if p not in sys.path: sys.path.insert(0, p)
import numpy as np, pandas as pd, lightgbm as lgb
import train_two_models as tm
from twomodel_common import load_split, apply_prod_feature_shaping, solo_mask
from src.feature_engineering import district_adjacent

ws = pd.read_parquet(MLP + "/data/world_state.parquet")
wsmap = {}
for r in ws.itertuples(index=False):
    cs = getattr(r, "courier_states", None)
    if isinstance(cs, dict):
        m = {}
        for name, st in cs.items():
            if isinstance(st, dict) and st.get("bag_districts") is not None:
                try: m[str(name)] = [x for x in list(st.get("bag_districts")) if isinstance(x, str) and x]
                except Exception: pass
        wsmap[(getattr(r, "order_id"), getattr(r, "T0"))] = m
v1 = pd.concat([pd.read_parquet(MLP + f"/data/datasets/v1.0/{s}.parquet") for s in ("train", "val", "test")], ignore_index=True)
dec2ot = {row.decision_id: (row.order_id, row.T0) for row in v1.drop_duplicates("decision_id").itertuples(index=False)}
GEO = ["g_in_bag", "g_n_adj", "g_frac_adj", "g_n_distant", "g_all_adj"]
def gf(bd, pdd):
    if not bd or not pdd or pdd == "Unknown": return (0, 0, 0.0, 0, 0)
    s = list(dict.fromkeys(bd)); inb = 1 if pdd in s else 0
    na = sum(1 for d in s if d == pdd or district_adjacent(d, pdd)); nd = len(s) - na
    return (inb, na, na / len(s), nd, 1 if nd == 0 else 0)
def attach(pw):
    pw = pw.copy(); pds = pw["pickup_district"].astype(str).tolist(); dids = pw["decision_id"].tolist(); cns = pw["courier_name"].astype(str).tolist()
    rows = [gf(wsmap.get(dec2ot.get(d), {}).get(c), p) for d, c, p in zip(dids, cns, pds)]
    a = np.array(rows, dtype=float)
    for i, g in enumerate(GEO): pw[g] = a[:, i]
    return pw
allp = pd.concat([load_split(s) for s in ("train", "val", "test")], ignore_index=True)
allp = apply_prod_feature_shaping(allp); allp["_date"] = pd.to_datetime(allp["date"], errors="coerce").dt.normalize()
days = sorted(allp["_date"].dropna().unique()); nd = len(days)
def mats(pw, fo):
    pw = pw.sort_values("decision_id").reset_index(drop=True); y = pw["label"].values; g = pw.groupby("decision_id", sort=False).size().values
    X = pw.reindex(columns=fo)
    for c in X.columns:
        if X[c].dtype == "object": X[c] = pd.to_numeric(X[c], errors="coerce")
        if X[c].dtype == bool: X[c] = X[c].astype(np.int8)
    return X.fillna(-1), y, g
def scored(pw, b, fo):
    pw = pw.sort_values("decision_id").reset_index(drop=True); X = pw.reindex(columns=fo)
    for c in X.columns:
        if X[c].dtype == "object": X[c] = pd.to_numeric(X[c], errors="coerce")
        if X[c].dtype == bool: X[c] = X[c].astype(np.int8)
    return pw.assign(score=b.predict(X.fillna(-1)))
def metrics(pw):
    cor = tot = t1 = ndec = 0
    for _, g in pw.groupby("decision_id"):
        w = g[g.label == 1]["score"]; l = g[g.label == 0]["score"]
        if len(w) != 1 or len(l) == 0: continue
        wv = float(w.iloc[0]); cor += int((l < wv).sum()); tot += len(l)
        ndec += 1; t1 += int(wv >= g["score"].max())
    return cor / tot, t1 / ndec, ndec
def run(eo):
    cutoff = days[nd - 14 - eo]
    if eo > 0:
        fend = days[nd - eo]; fwd_m = (allp["_date"] >= cutoff) & (allp["_date"] < fend)
    else:
        fwd_m = allp["_date"] >= cutoff
    btr = allp[(allp["_date"] < cutoff) & (~solo_mask(allp))].reset_index(drop=True)
    bfw = allp[fwd_m & (~solo_mask(allp))].reset_index(drop=True)
    pw_tr = attach(tm.build_pointwise(btr, drop_bundle=False)); pw_fw = attach(tm.build_pointwise(bfw, drop_bundle=False))
    le = tm.fit_label_encoders(pw_tr); tc = tm.fit_tier_categories(pw_tr)
    pw_tr = tm.apply_tier_onehot(tm.apply_label_encoders(pw_tr, le), tc); pw_fw = tm.apply_tier_onehot(tm.apply_label_encoders(pw_fw, le), tc)
    fb = [c for c in tm.feature_columns_of(pw_tr) if c not in GEO]; fe = fb + GEO
    dby = btr.drop_duplicates("decision_id").set_index("decision_id")["_date"]; pw_tr["_d"] = pw_tr["decision_id"].map(dby)
    td = sorted(btr["_date"].dropna().unique()); vd = set(td[-max(1, int(len(td) * 0.10)):]); isv = pw_tr["_d"].isin(vd)
    trp, vap = pw_tr[~isv], pw_tr[isv]
    def tr(fo):
        Xt, yt, gt = mats(trp, fo); Xv, yv, gv = mats(vap, fo); hp = dict(tm.HYPERPARAMS); hp["num_threads"] = 2
        m = lgb.LGBMRanker(**hp); m.fit(Xt, yt, group=gt, eval_set=[(Xv, yv)], eval_group=[gv], eval_at=[5], callbacks=[lgb.early_stopping(50, verbose=False)]); return m.booster_
    pab, t1b, ndd = metrics(scored(pw_fw, tr(fb), fb)); pae, t1e, _ = metrics(scored(pw_fw, tr(fe), fe))
    print("eo=%2d cutoff=%s ndec=%4d | pairwise %.4f->%.4f (%+.4f) | bundle-top1 %.4f->%.4f (%+.4f)" % (
        eo, str(pd.Timestamp(cutoff).date()), ndd, pab, pae, pae - pab, t1b, t1e, t1e - t1b), flush=True)
for eo in [0, 28]:
    run(eo)
print("done", flush=True)
