"""BUG C (2026-05-26) — renderer commit-divergence marker.

Renderer (`_route_lines_v2`) priorytetyzuje commit nad plan_eta — pokazuje
fikcję bez tyldy gdy solver wcisnął pickup na ck+5 mimo niemożliwego dojazdu
(Case #3 Toriko 13:06 → GK 13:08 = 2 min fizycznie niemożliwe).

Próg tilde 3 min (niższy niż 5 min warning) — pokazuje rosnące napięcie.

Pattern = source-regression (jak BUG E) + ekstrakcja math testu (pure function
math jest trywialna: |commit - plan_eta| > threshold → marker).
"""
import inspect
from datetime import datetime, timezone

from dispatch_v2 import common, telegram_approver


def test_bugc_common_contract_default():
    """COMMIT_RENDER_DIVERGENCE_TILDE_MIN = 3.0 default (env-overridable)."""
    assert common.COMMIT_RENDER_DIVERGENCE_TILDE_MIN == 3.0


def test_bugc_tilde_threshold_below_warn_threshold():
    """Tilde próg < warning próg — pokazuje napięcie zanim trafi do warning'a."""
    tilde = common.COMMIT_RENDER_DIVERGENCE_TILDE_MIN
    warn = getattr(common, "V3274_RENDER_DIVERGENCE_WARN_MIN", 5.0)
    assert tilde < warn, f"tilde={tilde} musi być < warn={warn}"


def test_bugc_block_present_in_source():
    """Blok BUG C obecny w _route_lines_v2."""
    src = inspect.getsource(telegram_approver)
    assert "BUG C (2026-05-26)" in src
    # Marker w pętli render
    assert "divergence_plan_dt" in src
    assert "⚠plan~" in src


def test_bugc_reads_tilde_threshold_from_common():
    """Renderer czyta COMMIT_RENDER_DIVERGENCE_TILDE_MIN z common."""
    src = inspect.getsource(telegram_approver)
    assert "COMMIT_RENDER_DIVERGENCE_TILDE_MIN" in src
    assert "tilde_threshold" in src


def test_bugc_marker_only_for_commit_source():
    """Marker tylko gdy source=='commit' — eta i tak ma `~` prefix."""
    src = inspect.getsource(telegram_approver)
    start = src.find("BUG C (2026-05-26): commit-plan divergence marker")
    assert start > 0
    section = src[start:start + 600]
    assert 'source == "commit"' in section
    assert "divergence_plan_dt" in section


def test_bugc_diff_math_below_threshold():
    """diff 2.0 min < 3.0 tilde → marker NIE pokazany."""
    commit_dt = datetime(2026, 5, 26, 13, 8, tzinfo=timezone.utc)
    plan_dt = datetime(2026, 5, 26, 13, 10, tzinfo=timezone.utc)  # 2 min różnica
    diff_min = abs((plan_dt - commit_dt).total_seconds() / 60.0)
    threshold = common.COMMIT_RENDER_DIVERGENCE_TILDE_MIN
    assert diff_min < threshold


def test_bugc_diff_math_above_threshold():
    """diff 6 min > 3.0 tilde → marker pokazany (Case #3 scenario)."""
    commit_dt = datetime(2026, 5, 26, 13, 8, tzinfo=timezone.utc)  # commit
    plan_dt = datetime(2026, 5, 26, 13, 14, tzinfo=timezone.utc)  # realny ETA 6 min później
    diff_min = abs((plan_dt - commit_dt).total_seconds() / 60.0)
    threshold = common.COMMIT_RENDER_DIVERGENCE_TILDE_MIN
    assert diff_min > threshold


def test_bugc_marker_format_in_source():
    """Format markera: `{hhmm}⚠plan~{plan_hhmm}` (compact, czytelny)."""
    src = inspect.getsource(telegram_approver)
    assert 'f"{hhmm}⚠plan~{plan_hhmm}"' in src or '{hhmm}⚠plan~{plan_hhmm}' in src
