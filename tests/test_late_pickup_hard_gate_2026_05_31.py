"""R-LATE-PICKUP hard gate (2026-05-31, Adrian) — max 5 min spóźnienia na ODBIÓR.

Dwie nienaruszalne reguły dyspozytorskie:
  (1) max 5 min spóźnienia na odbiór [ta bramka],
  (2) max 35 min doręczenie [R6 BAG_TIME_HARD_MAX_MIN — istniejące].

Bramka post-solve: per-pickup w plan.pickup_at liczy late = plan_pickup_eta - ref,
gdzie ref = committed czas_kuriera_warsaw (bag-order / nowy firm-commit) lub
pickup_ready_at (nowy bez commitu). late > LATE_PICKUP_HARD_MAX_MIN → kandydat
infeasible (verdict MAYBE→NO, wypada z feasible + z best_effort).

NIE okno TSP (lekcja E3 17.05: zaciśnięcie okien → 7.5k INFEASIBLE/dzień → ślepy
greedy). Metryka late_pickup_max_min liczona ZAWSZE (shadow); reject tylko gdy flag ON.

Pattern = source-regression (jak commit-divergence verdict-gate + BUG E hotfix):
bramka głęboko w assess_order, sprawdzamy obecność + inputy + predykat + werdykt
w źródle, plus kontrakt flagi/stałej + env override + interakcję z best_effort.
"""
import importlib
import inspect

from dispatch_v2 import common, dispatch_pipeline


# === common contract ===

def test_late_pickup_flag_present_default_on():
    """common: flaga ENABLE_LATE_PICKUP_HARD_GATE default ON (Adrian 2026-05-31)."""
    assert hasattr(common, "ENABLE_LATE_PICKUP_HARD_GATE")
    assert common.ENABLE_LATE_PICKUP_HARD_GATE is True


def test_late_pickup_threshold_default_5_min():
    """common: próg default 5 min."""
    assert hasattr(common, "LATE_PICKUP_HARD_MAX_MIN")
    assert common.LATE_PICKUP_HARD_MAX_MIN == 5.0


def test_late_pickup_flag_off_via_env(monkeypatch):
    """Env override: ENABLE_LATE_PICKUP_HARD_GATE=0 → flaga False (kill-switch)."""
    monkeypatch.setenv("ENABLE_LATE_PICKUP_HARD_GATE", "0")
    _common_mod = importlib.reload(common)
    assert _common_mod.ENABLE_LATE_PICKUP_HARD_GATE is False
    monkeypatch.setenv("ENABLE_LATE_PICKUP_HARD_GATE", "1")
    importlib.reload(common)


def test_late_pickup_threshold_override_via_env(monkeypatch):
    """Env override: LATE_PICKUP_HARD_MAX_MIN=8.0 → próg 8."""
    monkeypatch.setenv("LATE_PICKUP_HARD_MAX_MIN", "8.0")
    _common_mod = importlib.reload(common)
    assert _common_mod.LATE_PICKUP_HARD_MAX_MIN == 8.0
    monkeypatch.setenv("LATE_PICKUP_HARD_MAX_MIN", "5.0")
    importlib.reload(common)


# === source-regression: bramka w assess_order ===

def test_gate_comment_header_present():
    src = inspect.getsource(dispatch_pipeline)
    assert "R-LATE-PICKUP (HARD, 2026-05-31" in src


def test_gate_reads_flag_and_threshold():
    """Bramka czyta ENABLE_LATE_PICKUP_HARD_GATE + LATE_PICKUP_HARD_MAX_MIN."""
    src = inspect.getsource(dispatch_pipeline)
    start = src.find("R-LATE-PICKUP (HARD, 2026-05-31")
    section = src[start:start + 4500]
    assert "ENABLE_LATE_PICKUP_HARD_GATE" in section
    assert "LATE_PICKUP_HARD_MAX_MIN" in section


def test_gate_reference_committed_then_pickup_ready():
    """Referencja: committed czas_kuriera_warsaw (bag + nowy firm-commit),
    fallback pickup_ready_at dla nowego ordera bez commitu."""
    src = inspect.getsource(dispatch_pipeline)
    start = src.find("R-LATE-PICKUP (HARD, 2026-05-31")
    section = src[start:start + 4500]
    assert "czas_kuriera_warsaw" in section
    assert "pickup_ready_at" in section
    assert "plan.pickup_at" in section or "_plan_pickup_at_lp" in section


def test_gate_one_sided_plan_later_than_ref():
    """One-sided: late = plan_pickup_eta - ref (dodatnie = spóźnienie). Bez abs()."""
    src = inspect.getsource(dispatch_pipeline)
    start = src.find("R-LATE-PICKUP (HARD, 2026-05-31")
    section = src[start:start + 4500]
    assert "_pat_dt_lp - _ref_dt_lp" in section
    assert "abs(_late" not in section


def test_gate_metric_always_computed_reject_only_when_flag():
    """Metryka late_pickup_max_min liczona zawsze; hard_reject za flagą."""
    src = inspect.getsource(dispatch_pipeline)
    start = src.find("R-LATE-PICKUP (HARD, 2026-05-31")
    section = src[start:start + 4500]
    # reject ustawiany dopiero w bloku z flagą
    assert "late_pickup_hard_reject = True" in section
    assert 'getattr(C, "ENABLE_LATE_PICKUP_HARD_GATE", False)' in section


def test_gate_flips_verdict_maybe_to_no():
    """MAYBE → NO z reason late_pickup_hard_reject (nie przebija wcześniejszego NO)."""
    src = inspect.getsource(dispatch_pipeline)
    assert "if late_pickup_hard_reject and verdict == \"MAYBE\":" in src
    idx = src.find("if late_pickup_hard_reject and verdict == \"MAYBE\":")
    section = src[idx:idx + 600]
    assert 'verdict = "NO"' in section
    assert "late_pickup_hard_reject" in section


def test_gate_metric_serialized_to_metrics():
    """late_pickup_* serializowane do metrics (shadow log visibility)."""
    src = inspect.getsource(dispatch_pipeline)
    assert '"late_pickup_max_min"' in src
    assert '"late_pickup_hard_reject"' in src
    assert '"late_pickup_worst_oid"' in src


def test_best_effort_excludes_late_pickup_reject():
    """best_effort pool NIE resuscytuje kandydata z late_pickup_hard_reject
    (twarda reguła — best_effort PROPOSE nie może jej łamać)."""
    src = inspect.getsource(dispatch_pipeline)
    assert "_late_pickup_reject" in src
    idx = src.find("def _late_pickup_reject")
    section = src[idx:idx + 400]
    assert "late_pickup_hard_reject" in section
    assert "not _late_pickup_reject(c)" in src


def test_gate_fail_soft_on_unparseable_timestamps():
    """Defense-in-depth: nieparseowalny ISO ref/ETA → skip oid (continue), nie crash."""
    src = inspect.getsource(dispatch_pipeline)
    start = src.find("R-LATE-PICKUP (HARD, 2026-05-31")
    section = src[start:start + 4500]
    assert "except (TypeError, ValueError):" in section
    assert "continue" in section
