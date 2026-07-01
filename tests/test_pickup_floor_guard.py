"""Testy strażnika pickup-floor (READ-ONLY detektor INV-FEAS-PICKUP-FLOOR).

Zero I/O na żywych plikach: wszystkie deps (plans / orders_state / fleet_map /
proposals / now / dedup_state) wstrzykiwane wprost do evaluate(). Nie opieramy
logiki na flags.json (conftest wycina flagi decyzyjne).
"""
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from dispatch_v2.tools import pickup_floor_guard as G

WAW = ZoneInfo("Europe/Warsaw")


def _utc(y, mo, d, h, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc)


def _waw(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=WAW)


def _pstop(oid, predicted_iso, status="assigned", typ="pickup", scheduled=None):
    s = {"order_id": oid, "type": typ, "status_at_plan_time": status,
         "predicted_at": predicted_iso}
    if scheduled is not None:
        s["scheduled_at"] = scheduled
    return s


def _plan(stops, invalidated=None):
    p = {"stops": stops}
    if invalidated:
        p["invalidated_at"] = invalidated
    return p


def _fleet(cid, shift_start, name="Kurier", pos_source="pre_shift"):
    return {str(cid): {"shift_start": shift_start, "name": name,
                       "pos_source": pos_source}}


def _prop(ts_iso, oid, cid, pos_source, target_pickup, *, eff_start=None,
          clamp=False, v324=False, ck=None, new_eta=None):
    best = {"courier_id": str(cid), "pos_source": pos_source, "name": "K",
            "target_pickup_at": target_pickup, "new_pickup_eta_iso": new_eta,
            "effective_start_at": eff_start, "pre_shift_clamp_applied": clamp,
            "v324a_pickup_clamped_to_shift_start": v324, "czas_kuriera_warsaw": ck}
    return {"ts": ts_iso, "order_id": str(oid), "best": best}


def _run(*, plans=None, orders_state=None, fleet_map=None, proposals=None,
         now=None, dedup_state=None):
    """Domyślnie: żadnego ledgera (proposals=[]), izolowany dedup ({})."""
    return G.evaluate(plans=plans or {}, orders_state=orders_state or {},
                      fleet_map=fleet_map or {},
                      proposals=[] if proposals is None else proposals,
                      now=now or _utc(2026, 7, 1, 8, 0), write=False,
                      dedup_state={} if dedup_state is None else dedup_state)


# --- plan surface: naruszenie pre-shift-birth ---------------------------------
def test_plan_violation_detected():
    now = _utc(2026, 7, 1, 8, 0)              # 08:00 UTC
    ss = _waw(2026, 7, 1, 11, 0)              # 11:00 Warsaw = 09:00 UTC
    plans = {"457": _plan([_pstop("482715", "2026-07-01T08:45:00+00:00")])}
    res = _run(plans=plans, fleet_map=_fleet("457", ss), now=now)
    s = res["summary"]
    assert s["viol_plan"] == 1
    assert s["viol_recheck_leak"] == 1
    assert len(res["violations"]) == 1
    v = res["violations"][0]
    assert v["surface"] == "recheck_leak"
    assert v["floor_kind"] == "shift_start"
    assert v["cid"] == "457" and v["oid"] == "482715"
    assert abs(v["late_min"] - 15.0) < 0.05
    assert v["parcel"] is False and v["czasowka"] is False
    assert v["shift_start_source"] == "dispatchable_fleet"


# --- recheck_leak tylko gdy ck None (committed → wykluczone) -------------------
def test_committed_czas_kuriera_excluded():
    now = _utc(2026, 7, 1, 8, 0)
    ss = _waw(2026, 7, 1, 11, 0)
    plans = {"457": _plan([_pstop("482715", "2026-07-01T08:45:00+00:00")])}
    orders = {"482715": {"czas_kuriera_warsaw": "2026-07-01T11:00:00+02:00"}}
    res = _run(plans=plans, orders_state=orders, fleet_map=_fleet("457", ss), now=now)
    assert res["summary"]["viol_plan"] == 0
    assert res["summary"]["viol_recheck_leak"] == 0
    assert res["summary"]["committed_skipped_plans"] == 1


def test_czasowka_scheduled_at_excluded():
    now = _utc(2026, 7, 1, 8, 0)
    ss = _waw(2026, 7, 1, 11, 0)
    stop = _pstop("482715", "2026-07-01T08:45:00+00:00",
                  scheduled="2026-07-01T09:00:00+00:00")
    res = _run(plans={"457": _plan([stop])}, fleet_map=_fleet("457", ss), now=now)
    assert res["summary"]["viol_plan"] == 0
    assert res["summary"]["committed_skipped_plans"] == 1


# --- paczka tagowana osobno (oid >= 900M) -------------------------------------
def test_parcel_violation_tagged_separately():
    now = _utc(2026, 7, 1, 8, 0)
    ss = _waw(2026, 7, 1, 11, 0)
    plans = {"61": _plan([_pstop("900138100", "2026-07-01T08:45:00+00:00")])}
    res = _run(plans=plans, fleet_map=_fleet("61", ss), now=now)
    s = res["summary"]
    assert s["viol_plan"] == 0            # nie mieszane w główny licznik
    assert s["viol_plan_parcel"] == 1
    assert res["violations"][0]["parcel"] is True
    assert res["violations"][0]["surface"] == "recheck_leak"


# --- picked_up całkowicie pomijany --------------------------------------------
def test_picked_up_stop_skipped():
    now = _utc(2026, 7, 1, 8, 0)
    ss = _waw(2026, 7, 1, 11, 0)
    stop = _pstop("482715", "2026-07-01T08:45:00+00:00", status="picked_up")
    res = _run(plans={"457": _plan([stop])}, fleet_map=_fleet("457", ss), now=now)
    s = res["summary"]
    assert s["viol_plan"] == 0
    assert s["shift_start_unknown_plans"] == 0
    assert s["committed_skipped_plans"] == 0


# --- shift_start nieznany (cid poza flotą) → bucket, nie naruszenie -----------
def test_shift_start_unknown_bucket():
    now = _utc(2026, 7, 1, 8, 0)
    plans = {"999": _plan([_pstop("482715", "2026-07-01T08:45:00+00:00")])}
    res = _run(plans=plans, fleet_map={}, now=now)     # 999 poza flotą
    assert res["summary"]["viol_plan"] == 0
    assert res["summary"]["shift_start_unknown_plans"] == 1


# --- invalidated plan całkowicie pomijany -------------------------------------
def test_invalidated_plan_skipped():
    now = _utc(2026, 7, 1, 8, 0)
    ss = _waw(2026, 7, 1, 11, 0)
    plans = {"457": _plan([_pstop("482715", "2026-07-01T08:45:00+00:00")],
                          invalidated="2026-07-01T07:00:00+00:00")}
    res = _run(plans=plans, fleet_map=_fleet("457", ss), now=now)
    assert res["summary"]["n_plans_active"] == 0
    assert res["summary"]["viol_plan"] == 0


# --- tolerancja 60 s (parytet _floor_pickups_to_committed) --------------------
def test_tolerance_59s_no_violation():
    now = _utc(2026, 7, 1, 8, 0)
    ss = _waw(2026, 7, 1, 10, 0)                        # 10:00 Warsaw = 08:00 UTC
    stop = _pstop("482715", "2026-07-01T07:59:01+00:00")  # 59 s przed podłogą
    res = _run(plans={"457": _plan([stop])}, fleet_map=_fleet("457", ss), now=now)
    assert res["summary"]["viol_plan"] == 0


def test_tolerance_61s_violation():
    now = _utc(2026, 7, 1, 8, 0)
    ss = _waw(2026, 7, 1, 10, 0)                        # 08:00 UTC
    stop = _pstop("482715", "2026-07-01T07:58:59+00:00")  # 61 s przed podłogą
    res = _run(plans={"457": _plan([stop])}, fleet_map=_fleet("457", ss), now=now)
    assert res["summary"]["viol_plan"] == 1


# --- suspect_midnight (|now - shift_start| > 12 h) → nie naruszenie -----------
def test_suspect_midnight_plan_not_violation():
    now = _utc(2026, 7, 1, 1, 0)                        # 01:00 UTC
    ss = _waw(2026, 7, 1, 23, 0)                        # 23:00 Warsaw = 21:00 UTC
    stop = _pstop("482715", "2026-07-01T20:00:00+00:00")
    res = _run(plans={"457": _plan([stop])}, fleet_map=_fleet("457", ss), now=now)
    assert res["summary"]["viol_plan"] == 0
    assert res["summary"]["suspect_plans"] == 1


# --- proposal surface: clamp zadziałał vs przeciekł ---------------------------
def test_proposal_pre_shift_clamp_applied_no_violation():
    now = _utc(2026, 7, 1, 8, 0)
    p = _prop(now.isoformat(), "483656", "536", "pre_shift",
              "2026-07-01T09:05:00+00:00", eff_start="2026-07-01T11:00:00+02:00",
              clamp=True, v324=True)
    res = _run(proposals=[p], now=now)
    assert res["summary"]["viol_proposal"] == 0
    assert res["summary"]["proposal_clamped"] == 1


def test_proposal_pre_shift_leak_violation():
    now = _utc(2026, 7, 1, 8, 0)
    # podłoga 09:00 UTC (11:00 Warsaw), odbiór 08:50 UTC, clamp NIE zadziałał
    p = _prop(now.isoformat(), "483656", "536", "pre_shift",
              "2026-07-01T08:50:00+00:00", eff_start="2026-07-01T11:00:00+02:00",
              clamp=False, v324=False)
    res = _run(proposals=[p], now=now)
    s = res["summary"]
    assert s["viol_proposal"] == 1
    v = res["violations"][0]
    assert v["surface"] == "proposal" and v["floor_kind"] == "shift_start"
    assert v["pos_source"] == "pre_shift" and v["shift_start_source"] == "record"
    assert abs(v["late_min"] - 10.0) < 0.05


def test_proposal_no_gps_floor_now_violation():
    now = _utc(2026, 7, 1, 9, 0)
    # no_gps: podłoga = now (ts decyzji); odbiór 08:55 < 09:00 → przeciek
    p = _prop(now.isoformat(), "483657", "540", "no_gps",
              "2026-07-01T08:55:00+00:00", clamp=False, v324=False)
    res = _run(proposals=[p], now=now)
    assert res["summary"]["viol_proposal"] == 1
    assert res["violations"][0]["floor_kind"] == "now"


def test_proposal_committed_excluded():
    now = _utc(2026, 7, 1, 8, 0)
    p = _prop(now.isoformat(), "483657", "540", "no_gps",
              "2026-07-01T07:55:00+00:00", ck="2026-07-01T10:00:00+02:00")
    res = _run(proposals=[p], now=now)
    assert res["summary"]["viol_proposal"] == 0
    assert res["summary"]["proposal_committed"] == 1


def test_proposal_ok_when_pickup_after_floor():
    now = _utc(2026, 7, 1, 8, 0)
    p = _prop(now.isoformat(), "483656", "536", "pre_shift",
              "2026-07-01T09:30:00+00:00", eff_start="2026-07-01T11:00:00+02:00")
    res = _run(proposals=[p], now=now)
    assert res["summary"]["viol_proposal"] == 0


# --- dedup między tickami: podsumowanie liczy zawsze, rekord raz na 24 h ------
def test_dedup_across_ticks():
    ss = _waw(2026, 7, 1, 11, 0)
    plans = {"457": _plan([_pstop("482715", "2026-07-01T08:45:00+00:00")])}
    shared = {}
    r1 = _run(plans=plans, fleet_map=_fleet("457", ss),
              now=_utc(2026, 7, 1, 8, 0), dedup_state=shared)
    r2 = _run(plans=plans, fleet_map=_fleet("457", ss),
              now=_utc(2026, 7, 1, 8, 3), dedup_state=shared)
    assert r1["appended"] == 1 and r2["appended"] == 0     # rekord tylko raz
    assert r1["summary"]["viol_plan"] == 1                 # ale census...
    assert r2["summary"]["viol_plan"] == 1                 # ...liczy oba ticki


# --- summary ZAWSZE zapisany (nawet przy zerach = baseline) -------------------
def test_summary_always_written(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "GUARD_LOG", str(tmp_path / "g.jsonl"))
    monkeypatch.setattr(G, "DEDUP_STATE", str(tmp_path / "st.json"))
    now = _utc(2026, 7, 1, 8, 0)
    G.evaluate(plans={}, orders_state={}, fleet_map={}, proposals=[],
               now=now, write=True)
    lines = (tmp_path / "g.jsonl").read_text().strip().splitlines()
    assert len(lines) == 1                                 # sam summary, 0 naruszeń
    rec = json.loads(lines[0])
    assert rec["tick_summary"] is True
    assert rec["viol_plan"] == 0 and rec["viol_proposal"] == 0
    assert rec["invariant"] == G.INVARIANT


# --- degradacja gdy ledger_io niedostępny (głośno, nie cicho) -----------------
def test_degraded_when_ledger_missing(monkeypatch):
    monkeypatch.setattr(G, "_ledger_proposals", lambda now: (iter(()), True))
    now = _utc(2026, 7, 1, 8, 0)
    res = G.evaluate(plans={}, orders_state={}, fleet_map={},
                     now=now, write=False, dedup_state={})   # proposals=_UNSET
    assert res["degraded"] is True
    assert res["summary"]["degraded_proposal"] is True
    assert res["summary"]["viol_proposal"] == 0
