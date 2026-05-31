"""R-LATE-PICKUP (2026-05-31, Adrian) — max 5 min spóźnienia na ODBIÓR + tiering.

Dwie nienaruszalne reguły: (1) 5 min spóźnienie odbioru, (2) 35 min doręczenie (R6).
Mechanizm = TIERING selekcji (NIE hard-reject) → „zawsze daje propozycje":
  • tier 0: nie psuje umówionego odbioru ORAZ zdąży na nowy ≤5 min (na czas)
  • tier 1: nie psuje umówionego, ale nowy odbiór >5 min → propozycja przedłużonego czasu
  • tier 2: psuje umówiony odbiór committed (>5) — OSTATECZNOŚĆ (przypadek 477237)
Committed vs nowy liczone osobno: committed-breach → demote do tier 2 (kurier nie bierze
zlecenia jeśli jest ktoś lepszy); nowy >5 → przedłużenie czasu + wybór najszybszego.

Pattern = source-regression (jak commit-divergence verdict-gate): logika głęboko
w assess_order, sprawdzamy obecność + inputy + tiering + payload + render + kontrakt.
"""
import importlib
import inspect

from dispatch_v2 import common, dispatch_pipeline, shadow_dispatcher, telegram_approver


# === common contract ===

def test_flag_present_default_on():
    assert hasattr(common, "ENABLE_LATE_PICKUP_HARD_GATE")
    assert common.ENABLE_LATE_PICKUP_HARD_GATE is True


def test_threshold_default_5_min():
    assert hasattr(common, "LATE_PICKUP_HARD_MAX_MIN")
    assert common.LATE_PICKUP_HARD_MAX_MIN == 5.0


def test_flag_off_via_env(monkeypatch):
    monkeypatch.setenv("ENABLE_LATE_PICKUP_HARD_GATE", "0")
    m = importlib.reload(common)
    assert m.ENABLE_LATE_PICKUP_HARD_GATE is False
    monkeypatch.setenv("ENABLE_LATE_PICKUP_HARD_GATE", "1")
    importlib.reload(common)


def test_threshold_override_via_env(monkeypatch):
    monkeypatch.setenv("LATE_PICKUP_HARD_MAX_MIN", "8.0")
    m = importlib.reload(common)
    assert m.LATE_PICKUP_HARD_MAX_MIN == 8.0
    monkeypatch.setenv("LATE_PICKUP_HARD_MAX_MIN", "5.0")
    importlib.reload(common)


# === computation: committed vs new split ===

def test_committed_and_new_split_computed():
    src = inspect.getsource(dispatch_pipeline)
    start = src.find("R-LATE-PICKUP (2026-05-31, Adrian): max 5 min")
    assert start > 0
    section = src[start:start + 4500]
    assert "late_pickup_committed_max" in section
    assert "new_pickup_late_min" in section
    assert "new_pickup_eta_iso" in section
    assert "czas_kuriera_warsaw" in section  # committed ref
    assert "pickup_ready_at" in section      # new ref fallback


def test_breach_and_extension_flags_only_when_enabled():
    src = inspect.getsource(dispatch_pipeline)
    start = src.find("R-LATE-PICKUP (2026-05-31, Adrian): max 5 min")
    section = src[start:start + 4500]
    assert 'getattr(C, "ENABLE_LATE_PICKUP_HARD_GATE", False)' in section
    assert "late_pickup_committed_breach = late_pickup_committed_max > _LP_LIMIT" in section
    assert "new_pickup_needs_extension = new_pickup_late_min > _LP_LIMIT" in section


def test_no_hard_reject_verdict_flip():
    """KLUCZOWE: NIE ma już flipu MAYBE→NO na late-pickup (zastąpione tieringiem)."""
    src = inspect.getsource(dispatch_pipeline)
    assert "late_pickup_hard_reject" not in src
    assert 'late_pickup_hard_reject ({late_pickup_max_min' not in src


# === tiering selection ===

def test_tiering_reorder_present():
    src = inspect.getsource(dispatch_pipeline)
    assert "R-LATE-PICKUP tiering (2026-05-31" in src
    idx = src.find("R-LATE-PICKUP tiering (2026-05-31")
    section = src[idx:idx + 2500]
    assert "_lp_tier" in section
    # Opcja B (2026-05-31): logika tieru w modułowym _late_pickup_tier (testowalny).
    # Inline block aliasuje `_lp_tier = _late_pickup_tier`.
    tier_src = inspect.getsource(dispatch_pipeline._late_pickup_tier)
    assert "late_pickup_committed_breach" in tier_src  # tier 2
    assert "new_pickup_needs_extension" in tier_src     # tier 1
    assert "return 0" in tier_src                       # tier 0


def test_tiering_after_demote_blind_empty():
    """Tiering MUSI być po _demote_blind_empty (final pass, lesson #150)."""
    src = inspect.getsource(dispatch_pipeline)
    demote = src.rfind("feasible = _demote_blind_empty(feasible, order_id)")
    tier = src.find("R-LATE-PICKUP tiering (2026-05-31")
    assert demote > 0 and tier > demote


def test_extension_mode_picks_fastest():
    """Brak tier-0 → sort po najszybszym odbiorze nowego (new_pickup_eta_iso)."""
    src = inspect.getsource(dispatch_pipeline)
    idx = src.find("R-LATE-PICKUP tiering (2026-05-31")
    section = src[idx:idx + 2500]
    assert "_has_lower" in section
    assert "_new_eta_key" in section
    assert "new_pickup_eta_iso" in section


def test_pickup_extension_redirect_payload():
    # Blok urósł (Opcja B + r6_danger shadow) → sprawdzamy w pełnym źródle (markery unikalne),
    # nie w oknie fixed-size (było brittle wobec każdej rozbudowy bloku).
    src = inspect.getsource(dispatch_pipeline)
    assert "R-LATE-PICKUP tiering (2026-05-31" in src
    assert "pickup_extension_redirect = {" in src
    assert "suggested_pickup_iso" in src
    assert "committed_breach_min" in src
    assert "_result_pf.pickup_extension_redirect = pickup_extension_redirect" in src


def test_metric_serialized_to_metrics():
    src = inspect.getsource(dispatch_pipeline)
    assert '"late_pickup_committed_max"' in src
    assert '"new_pickup_late_min"' in src
    assert '"new_pickup_needs_extension"' in src
    assert '"new_pickup_eta_iso"' in src


# === downstream surfacing ===

def test_shadow_serializes_pickup_extension_redirect():
    src = inspect.getsource(shadow_dispatcher)
    assert '"pickup_extension_redirect"' in src
    assert 'getattr(result, "pickup_extension_redirect", None)' in src


def test_telegram_renders_extension_line():
    src = inspect.getsource(telegram_approver)
    assert 'decision.get("pickup_extension_redirect")' in src
    assert "Proponowany czas odbioru" in src
    assert "psuje" in src  # tier-2 ostrzeżenie


def test_compute_fail_soft():
    src = inspect.getsource(dispatch_pipeline)
    start = src.find("R-LATE-PICKUP (2026-05-31, Adrian): max 5 min")
    section = src[start:start + 4500]
    assert "except (TypeError, ValueError, AttributeError):" in section
