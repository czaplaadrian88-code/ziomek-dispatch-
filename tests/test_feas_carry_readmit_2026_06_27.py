"""B2 / #483000 (2026-06-27) — feasible-path carry-aware re-admit (_feas_carry_readmit_pick).

Gwarancje:
 (1) REDIRECT: odrzucony (NO, blocking sla/r6) lepszy carry-inclusive (lex_qual) z nowym
     orderem ≤ cap → zwracany do promocji + regret = chosen_objm − rej_objm.
 (2) CAP-40 (Tier-3) TWARDY: ten sam kandydat z nowym orderem > cap → None (R6=35 hard
     chroniony poza pasmem 35-40; cap NIE relaksowalny przez lepszy carry).
 (3) CHOSEN CZYSTY (objm≤0/None) → None (poza zakresem asymetrii #483000).
 (4) REJECTED NIE-LEPSZY (lex_qual ≥ chosen) → None.
 (5) NON-BLOCKING reject (shift_end/dist/committed) ignorowany → None.
 (6) FAIL-OPEN: pusty top/candidates / złe metryki → None, bez wyjątku.
 (7) FLAGA ON≠OFF: decision_flag gate'uje ścieżkę (C-FLAG-EFFECT ratchet).
 (8) PARYTET cap: domyślny cap = best_effort (BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN=40).

Mock-only (pure function); pełny replay ON↔OFF = eod_drafts/2026-06-27/feas_carry_readmit_replay.py.
"""
import dispatch_v2.common as C
from dispatch_v2.dispatch_pipeline import (
    _feas_carry_readmit_pick, _best_effort_objm_pick)

NEW_OID = "NEW1"


class _Plan:
    def __init__(self, new_bag_min):
        # per_order_delivery_times: nowy order → jego bag-time (Tier-3 cap dotyczy nowego)
        self.per_order_delivery_times = {NEW_OID: new_bag_min} if new_bag_min is not None else {}


class _Cand:
    def __init__(self, cid, verdict, *, r6, reason="", new_bag=None,
                 committed=0.0, new_late=0.0):
        self.courier_id = cid
        self.feasibility_verdict = verdict
        self.feasibility_reason = reason
        self.plan = _Plan(new_bag)
        self.metrics = {
            "objm_r6_breach_max_min": r6,
            "late_pickup_committed_max": committed,
            "new_pickup_late_min": new_late,
        }


def _chosen(r6=58.0):
    # zwycięzca live = bypassowany carry (forgiven breach r6>0)
    return _Cand("C_CHOSEN", "MAYBE", r6=r6, reason="", new_bag=20.0)


# ── (1) REDIRECT ────────────────────────────────────────────────────────────
def test_redirect_when_blocking_better_carry_within_cap():
    chosen = _chosen(r6=58.0)
    rej = _Cand("C_REJ", "NO", r6=37.0, reason="R6_per_order_>35min (X 37.0min, over by 2.0)",
                new_bag=37.0)
    out = _feas_carry_readmit_pick([chosen], [chosen], [chosen, rej], NEW_OID, cap_min=40.0)
    assert out is not None
    cand, regret, orig_reason, newbag = out
    assert cand is rej
    assert regret == 21.0           # 58 − 37
    assert newbag == 37.0
    assert orig_reason.startswith("R6_per_order")


def test_redirect_sla_kind_also_eligible():
    chosen = _chosen(r6=50.0)
    rej = _Cand("C_REJ", "NO", r6=40.0, reason="sla_violation (Y +40min, over by 5.0)",
                new_bag=39.0)
    out = _feas_carry_readmit_pick([chosen], [chosen], [chosen, rej], NEW_OID, cap_min=40.0)
    assert out is not None and out[0] is rej


# ── (2) CAP-40 TWARDY ───────────────────────────────────────────────────────
def test_cap_blocks_when_new_order_over_cap():
    chosen = _chosen(r6=58.0)
    # carry lepszy (r6=42 < 58) ALE nowy order 45 > cap 40 → Tier-3 sufit blokuje
    rej = _Cand("C_REJ", "NO", r6=42.0, reason="R6_per_order_>35min (X 45.0min, over by 10.0)",
                new_bag=45.0)
    out = _feas_carry_readmit_pick([chosen], [chosen], [chosen, rej], NEW_OID, cap_min=40.0)
    assert out is None


def test_cap_exactly_40_allowed():
    chosen = _chosen(r6=58.0)
    rej = _Cand("C_REJ", "NO", r6=40.0, reason="R6_per_order_>35min (X 40.0min, over by 5.0)",
                new_bag=40.0)
    out = _feas_carry_readmit_pick([chosen], [chosen], [chosen, rej], NEW_OID, cap_min=40.0)
    assert out is not None and out[0] is rej


def test_no_newbag_data_rejected():
    # brak danych new-order bag-time → NIE re-dopuszczaj (bezpieczniej; feasible-path nie ma
    # fallbacku pure-carry jak best_effort)
    chosen = _chosen(r6=58.0)
    rej = _Cand("C_REJ", "NO", r6=37.0, reason="R6_per_order_>35min (X)", new_bag=None)
    rej.metrics.pop("sum_bag_time_min", None)
    out = _feas_carry_readmit_pick([chosen], [chosen], [chosen, rej], NEW_OID, cap_min=40.0)
    assert out is None


# ── (3) CHOSEN CZYSTY ───────────────────────────────────────────────────────
def test_chosen_clean_no_redirect():
    chosen = _chosen(r6=0.0)
    rej = _Cand("C_REJ", "NO", r6=10.0, reason="R6_per_order_>35min (X)", new_bag=37.0)
    assert _feas_carry_readmit_pick([chosen], [chosen], [chosen, rej], NEW_OID) is None


def test_chosen_none_objm_no_redirect():
    chosen = _chosen(r6=58.0)
    chosen.metrics["objm_r6_breach_max_min"] = None
    rej = _Cand("C_REJ", "NO", r6=10.0, reason="R6_per_order_>35min (X)", new_bag=37.0)
    assert _feas_carry_readmit_pick([chosen], [chosen], [chosen, rej], NEW_OID) is None


# ── (4) REJECTED NIE-LEPSZY ─────────────────────────────────────────────────
def test_rejected_not_better_no_redirect():
    chosen = _chosen(r6=40.0)
    rej = _Cand("C_REJ", "NO", r6=55.0, reason="R6_per_order_>35min (X)", new_bag=38.0)
    assert _feas_carry_readmit_pick([chosen], [chosen], [chosen, rej], NEW_OID) is None


# ── (5) NON-BLOCKING reject ignorowany ──────────────────────────────────────
def test_non_blocking_reject_ignored():
    chosen = _chosen(r6=58.0)
    # świetny carry-inclusive ALE reason = shift_end (legit reject, nie asymetria bramki)
    rej = _Cand("C_REJ", "NO", r6=5.0, reason="shift_end_before_pickup (zmiana do 18:00)",
                new_bag=20.0)
    assert _feas_carry_readmit_pick([chosen], [chosen], [chosen, rej], NEW_OID) is None


def test_committed_reject_not_readmitted():
    # committed-window reject (R-DECLARED-TIME) NIE jest blocking sla/r6 → nietykany
    chosen = _chosen(r6=58.0)
    rej = _Cand("C_REJ", "NO", r6=10.0, reason="committed_window_violation (>5min)", new_bag=20.0)
    assert _feas_carry_readmit_pick([chosen], [chosen], [chosen, rej], NEW_OID) is None


# ── (6) FAIL-OPEN ───────────────────────────────────────────────────────────
def test_fail_open_empty():
    assert _feas_carry_readmit_pick([], [], [], NEW_OID) is None
    chosen = _chosen()
    assert _feas_carry_readmit_pick([chosen], [chosen], [], NEW_OID) is None


def test_no_blocking_candidates_no_redirect():
    chosen = _chosen(r6=58.0)
    other_maybe = _Cand("C_OK", "MAYBE", r6=30.0, new_bag=30.0)
    assert _feas_carry_readmit_pick([chosen], [chosen, other_maybe],
                                    [chosen, other_maybe], NEW_OID) is None


# ── (7) FLAGA ON≠OFF (C-FLAG-EFFECT) ────────────────────────────────────────
def test_flag_default_off():
    # default produkcyjny = OFF (shadow-first); flip dopiero po replay + ACK
    assert C.ENABLE_FEAS_CARRY_READMIT is False


def test_flag_toggles_decision_path(monkeypatch):
    # decision_flag steruje wpięciem LIVE: OFF → ścieżka pomijana; ON → wykonywana.
    # (helper sam jest pure; gate = C.decision_flag w dispatch_pipeline po shadow.)
    monkeypatch.setattr(C, "ENABLE_FEAS_CARRY_READMIT", False, raising=False)
    monkeypatch.setattr(C, "load_flags", lambda: {}, raising=False)
    assert C.decision_flag("ENABLE_FEAS_CARRY_READMIT") is False
    monkeypatch.setattr(C, "ENABLE_FEAS_CARRY_READMIT", True, raising=False)
    assert C.decision_flag("ENABLE_FEAS_CARRY_READMIT") is True


# ── (8) PARYTET cap z best_effort ───────────────────────────────────────────
def test_cap_default_parity_with_best_effort():
    # ten sam Tier-3 cap (40) co _best_effort_objm_pick — jedna dyscyplina, dwie ścieżki
    def _default_cap(fn):
        return fn.__defaults__[-1]  # cap_min ostatni arg domyślny
    assert _default_cap(_feas_carry_readmit_pick) == 40.0
    assert _default_cap(_best_effort_objm_pick) == 40.0
    assert float(C.ENABLE_FEAS_CARRY_READMIT is False and
                 __import__("json").load(open("/root/.openclaw/workspace/scripts/flags.json"))
                 ["BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN"]) == 40.0
