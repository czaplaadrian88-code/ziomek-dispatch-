"""Testy behawioralne perf-SLO (finding E audytu 2.0) — C13 (nie teatr).

Zakres:
  * percentyl nearest-rank (ręcznie policzone oczekiwane),
  * segmentacja Warsaw (peak / high-risk / off-peak) — z DST lata,
  * evaluate_slo: breach p50/p95/ceiling na syntetycznych oknach + gating min_n
    (mała próba nie bramkuje percentyli; ceiling bramkuje ZAWSZE),
  * collect: filtr okna + obecność latency_ms (ledger_io zamockowany),
  * edge-trigger `_slo_notify_decision`: breach→alert raz, trwały→cisza,
    eskalacja→alert, recovery→OK raz, steady-OK→cisza, przypomnienie po oknie,
  * MUTATION-GUARD: podniesienie progu p95 (monkeypatch) kasuje breach — dowód,
    że próg jest nośny (nie dekoracja). Odwrotny kierunek (mutate→FAIL) w raporcie.

Rozdzielczość pakietu: pod pytest `dispatch_v2` wskazuje na worktree (symlink +
PYTHONPATH), więc importujemy realny worktree'owy perf_budget_report/canary.
"""
from datetime import datetime, timezone, timedelta

from dispatch_v2.tools import perf_budget_report as PB
from dispatch_v2.tools import objm_lexr6_canary_monitor as M

UTC = timezone.utc


def _ts(hour_utc, minute=0, day=1):
    """Znacznik czasu UTC 2026-07-{day} (lipiec = DST Warsaw = UTC+2)."""
    return datetime(2026, 7, day, hour_utc, minute, 0, tzinfo=UTC)


# ── Percentyl (nearest-rank) — ręcznie policzone ────────────────────────────
def test_pctile_hand_computed_n10():
    vals = [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]
    # i = min(9, int(q*9+0.5)); p50→i=5→600, p95→i=9→1000, p99→i=9→1000
    assert PB._pctile(vals, 0.50) == 600
    assert PB._pctile(vals, 0.95) == 1000
    assert PB._pctile(vals, 0.99) == 1000


def test_pctile_hand_computed_n5():
    vals = [50, 10, 30, 40, 20]  # nieposortowane → _pctile sortuje
    # sorted [10,20,30,40,50]; p50→int(0.5*4+0.5)=2→30; p95→int(0.95*4+0.5)=4→50
    assert PB._pctile(vals, 0.50) == 30
    assert PB._pctile(vals, 0.95) == 50


def test_pctile_empty():
    assert PB._pctile([], 0.5) is None


def test_percentiles_tail_pct():
    vals = [100] * 9 + [2000]  # 1 z 10 ≥1500 → 10%
    m = PB.percentiles(vals)
    assert m["n"] == 10
    assert m["max"] == 2000
    assert m["tail_pct"] == 10.0
    assert m["p50"] == 100


# ── Segmentacja (Warsaw, lato = UTC+2) ──────────────────────────────────────
def test_segment_peak():
    # UTC 10 → Warsaw 12 → peak (11-14); UTC 15 → Warsaw 17 → peak (17-20)
    assert PB.segment_for(_ts(10)) == "peak"
    assert PB.segment_for(_ts(15)) == "peak"


def test_segment_high_risk():
    # UTC 13 → Warsaw 15 → high-risk (14-17)
    assert PB.segment_for(_ts(13)) == "high_risk"


def test_segment_offpeak():
    # UTC 1 → Warsaw 3 → off-peak; UTC 19 → Warsaw 21 → off-peak (poza 17-20)
    assert PB.segment_for(_ts(1)) == "offpeak"
    assert PB.segment_for(_ts(19)) == "offpeak"


def test_segments_partition_all_hours():
    # każda godzina UTC należy dokładnie do jednego segmentu (rozłączny podział)
    segs = {PB.segment_for(_ts(h)) for h in range(24)}
    assert segs <= {"peak", "high_risk", "offpeak"}
    for h in range(24):
        assert PB.segment_for(_ts(h)) in PB.SLO_SEGMENTS


# ── evaluate_slo: breach p50/p95/ceiling ────────────────────────────────────
def _peak(lat, k):
    return [(_ts(10), float(lat)) for _ in range(k)]  # UTC10 = Warsaw12 = peak


def test_slo_clean_no_breach():
    # 30 decyzji peak @400 ms → pod wszystkimi limitami (p50 700 / p95 1500 / ceil 3000)
    assert PB.evaluate_slo(_peak(400, 30), min_n=20) == []


def test_slo_peak_p50_breach_only():
    # 30 @800 → p50 800>700 (breach), p95 800≤1500 (ok), brak ceiling
    b = PB.evaluate_slo(_peak(800, 30), min_n=20)
    metrics = {(x["segment"], x["metric"]) for x in b}
    assert metrics == {("peak", "p50")}
    only = [x for x in b if x["metric"] == "p50"][0]
    assert only["value"] == 800 and only["limit"] == 700 and only["n"] == 30


def test_slo_peak_p95_and_p50_breach():
    # 30 @1600 → p50 1600>700 i p95 1600>1500 (oba), 1600<3000 brak ceiling
    b = PB.evaluate_slo(_peak(1600, 30), min_n=20)
    metrics = {(x["segment"], x["metric"]) for x in b}
    assert metrics == {("peak", "p50"), ("peak", "p95")}


def test_slo_ceiling_counts_over_limit():
    # 25 @500 + 5 @4000 (n=30): ceiling=5>3000; p95→i=int(0.95*29+0.5)=28→4000>1500
    rows = _peak(500, 25) + _peak(4000, 5)
    b = PB.evaluate_slo(rows, min_n=20)
    ceil = [x for x in b if x["metric"] == "ceiling"]
    assert len(ceil) == 1 and ceil[0]["value"] == 5.0 and ceil[0]["limit"] == 3000
    assert ("peak", "p95") in {(x["segment"], x["metric"]) for x in b}


def test_slo_min_n_suppresses_percentiles():
    # 5 @2000 peak: percentyle powyżej limitu, ale n=5<min_n(20) → BEZ p50/p95 breach;
    # 2000<3000 → brak ceiling → zero breachy (mała próba = szum, nie SLO)
    assert PB.evaluate_slo(_peak(2000, 5), min_n=20) == []


def test_slo_min_n_low_reveals_breach():
    # ta sama próba, min_n=1 → percentyle bramkują → p50+p95 breach (dowód, że
    # to min_n wyciszył, nie brak sygnału)
    b = PB.evaluate_slo(_peak(2000, 5), min_n=1)
    assert {(x["segment"], x["metric"]) for x in b} == {("peak", "p50"), ("peak", "p95")}


def test_slo_ceiling_fires_below_min_n():
    # 3 @3500 peak (n=3<min_n): ceiling NIE zależy od min_n → fires; percentyle nie
    b = PB.evaluate_slo(_peak(3500, 3), min_n=20)
    assert b == [{"segment": "peak", "metric": "ceiling", "value": 3.0, "limit": 3000, "n": 3}]


def test_slo_offpeak_tighter_limits():
    # off-peak limit p50 450: 30 @600 → p50 600>450 breach; p95 600≤900 ok
    rows = [(_ts(1), 600.0) for _ in range(30)]  # UTC1=Warsaw3=offpeak
    b = PB.evaluate_slo(rows, min_n=20)
    assert {(x["segment"], x["metric"]) for x in b} == {("offpeak", "p50")}


def test_slo_multi_segment():
    rows = _peak(1600, 25) + [(_ts(13), 2000.0) for _ in range(25)]  # peak + high_risk
    b = PB.evaluate_slo(rows, min_n=20)
    segs = {x["segment"] for x in b}
    assert segs == {"peak", "high_risk"}


# ── collect: filtr okna + obecność latency_ms (ledger_io zamockowany) ────────
def test_collect_window_and_latency_filter(monkeypatch):
    recs = [
        {"ts": "2026-07-01T10:00:00+00:00", "latency_ms": 500},   # w oknie
        {"ts": "2026-07-01T10:05:00Z", "latency_ms": 700},         # w oknie (Z)
        {"ts": "2026-07-01T09:00:00+00:00", "latency_ms": 999},    # przed since
        {"ts": "2026-07-01T10:10:00+00:00"},                       # brak latency_ms
        {"ts": "2026-07-01T23:00:00+00:00", "latency_ms": 400},    # po until
        {"ts": "bad-ts", "latency_ms": 400},                       # nieparsowalny ts
    ]
    monkeypatch.setattr(PB.ledger_io, "iter_shadow_decisions", lambda since, **kw: iter(recs))
    since = datetime(2026, 7, 1, 9, 30, tzinfo=UTC)
    until = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    rows = PB.collect(since, until)
    assert [lat for _, lat in rows] == [500.0, 700.0]


def test_collect_deterministic(monkeypatch):
    recs = [{"ts": "2026-07-01T10:00:00+00:00", "latency_ms": i} for i in range(50)]
    monkeypatch.setattr(PB.ledger_io, "iter_shadow_decisions", lambda since, **kw: iter(list(recs)))
    since = datetime(2026, 7, 1, 0, tzinfo=UTC)
    until = datetime(2026, 7, 1, 23, tzinfo=UTC)
    r1 = PB.collect(since, until)
    r2 = PB.collect(since, until)
    assert r1 == r2 and len(r1) == 50


# ── Edge-trigger SLO (_slo_notify_decision) — mirror objm edge tests ─────────
T0 = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)
REMIND = timedelta(hours=2)
BREACH = [{"segment": "peak", "metric": "p95", "value": 1847.0, "limit": 1500.0, "n": 1893}]
BREACH2 = BREACH + [{"segment": "offpeak", "metric": "p50", "value": 638.0, "limit": 450.0, "n": 583}]


def test_slo_first_breach_sends():
    send, msg, st = M._slo_notify_decision(BREACH, {}, T0, REMIND)
    assert send is True
    assert "breach" in msg and "peak" in msg
    assert st["level"] == "BREACH"


def test_slo_same_breach_within_window_silent():
    prev = {"signature": M._slo_verdict_signature(BREACH)[1], "level": "BREACH",
            "last_sent": T0.isoformat()}
    send, msg, st = M._slo_notify_decision(BREACH, prev, T0 + timedelta(minutes=10), REMIND)
    assert send is False and msg is None
    assert st["last_sent"] == T0.isoformat()  # zegar nieprzesunięty


def test_slo_persistent_reminded_after_window():
    prev = {"signature": M._slo_verdict_signature(BREACH)[1], "level": "BREACH",
            "last_sent": T0.isoformat()}
    send, msg, st = M._slo_notify_decision(BREACH, prev, T0 + timedelta(hours=2, minutes=1), REMIND)
    assert send is True and "nadal" in msg


def test_slo_escalation_new_segment_sends():
    prev = {"signature": M._slo_verdict_signature(BREACH)[1], "level": "BREACH",
            "last_sent": T0.isoformat()}
    send, msg, st = M._slo_notify_decision(BREACH2, prev, T0 + timedelta(minutes=10), REMIND)
    assert send is True  # inna sygnatura (doszedł off-peak) → alert mimo okna


def test_slo_recovery_to_ok_sends_once():
    prev = {"signature": M._slo_verdict_signature(BREACH)[1], "level": "BREACH",
            "last_sent": T0.isoformat()}
    send, msg, st = M._slo_notify_decision([], prev, T0 + timedelta(minutes=10), REMIND)
    assert send is True and "OK" in msg and st["level"] == "OK"
    send2, _, _ = M._slo_notify_decision([], st, T0 + timedelta(minutes=20), REMIND)
    assert send2 is False  # kolejny OK → cisza


def test_slo_steady_ok_silent():
    prev = {"signature": "OK|", "level": "OK", "last_sent": None}
    send, msg, _ = M._slo_notify_decision([], prev, T0, REMIND)
    assert send is False and msg is None


def test_slo_reminder_disabled_stays_silent():
    prev = {"signature": M._slo_verdict_signature(BREACH)[1], "level": "BREACH",
            "last_sent": T0.isoformat()}
    send, _, _ = M._slo_notify_decision(BREACH, prev, T0 + timedelta(hours=99), timedelta(hours=0))
    assert send is False  # remind_after=0 → tylko zmiana wyzwala


def test_slo_signature_order_independent():
    # sygnatura sortuje segment:metryka → kolejność breachy nie zmienia sygnatury
    a = M._slo_verdict_signature(BREACH2)[1]
    b = M._slo_verdict_signature(list(reversed(BREACH2)))[1]
    assert a == b


# ── MUTATION-GUARD: próg p95 jest nośny (nie dekoracja) ──────────────────────
def test_p95_threshold_is_load_bearing(monkeypatch):
    rows = _peak(1600, 30)  # p95 1600 > limit 1500 → breach przy realnym progu
    assert ("peak", "p95") in {(x["segment"], x["metric"]) for x in PB.evaluate_slo(rows, min_n=20)}
    # podnieś próg p95 → ten sam sygnał NIE jest już breachem (próg realnie bramkuje)
    seg = dict(PB.SLO_SEGMENTS["peak"]); seg["p95"] = 999999.0
    monkeypatch.setitem(PB.SLO_SEGMENTS, "peak", seg)
    assert ("peak", "p95") not in {(x["segment"], x["metric"]) for x in PB.evaluate_slo(rows, min_n=20)}
