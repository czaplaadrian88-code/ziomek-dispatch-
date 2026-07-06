"""BUG C verdict-gate (2026-05-27) — gdy plan.pickup_at[oid] (ETA z
route_simulator) odjeżdża od commit `czas_kuriera_warsaw` (z bag_context
bag-orderów lub z order_event dla nowego ordera) o > próg → verdict=KOORD.

Marker `⚠plan~HH:MM` w renderze (telegram_approver._route_lines_v2, BUG C
2026-05-26 commit `e805cdb`) tylko surface'uje rozjazd, ale verdict pozostawał
PROPOSE/AUTO — operator mógł zatwierdzić fikcję jednym kliknięciem. Case #12
27.05 (Uwędzony → Żeromskiego): Retrospekcja commit 14:16, plan 14:32,
divergence 16 min — system PROPOSE'ował zamiast eskalować do koordynatora.

Pattern = source-regression (jak BUG E hotfix + OBJ F3): bramka głęboko
w assess_order, sprawdzamy obecność + pozycję + predykat + werdykt w źródle,
plus kontrakt flagi + stałej w common.
"""
from dispatch_v2.core import selection as _k12s  # K12: selekcja/werdykt (skan obu zrodel)
import inspect

from dispatch_v2 import common, dispatch_pipeline


def test_commit_div_gate_comment_header_present():
    src = (inspect.getsource(dispatch_pipeline) + inspect.getsource(_k12s))
    assert "BUG C verdict-gate (2026-05-27)" in src


def test_commit_div_gate_flag_in_source():
    """Bramka czyta flagę ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE."""
    src = (inspect.getsource(dispatch_pipeline) + inspect.getsource(_k12s))
    assert "ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE" in src


def test_commit_div_gate_uses_threshold_constant():
    """Bramka używa COMMIT_DIVERGENCE_VERDICT_KOORD_MIN_MIN jako progu."""
    src = (inspect.getsource(dispatch_pipeline) + inspect.getsource(_k12s))
    start = src.find("BUG C verdict-gate (2026-05-27)")
    assert start > 0
    section = src[start:start + 4500]
    assert "COMMIT_DIVERGENCE_VERDICT_KOORD_MIN_MIN" in section


def test_commit_div_gate_reads_bag_context_and_plan_pickup_at():
    """Inputy: plan.pickup_at (ETA solver) + bag_context (commit z metryk
    kandydata). Reverse-feed dla nowego ordera: order_event.czas_kuriera_warsaw."""
    src = (inspect.getsource(dispatch_pipeline) + inspect.getsource(_k12s))
    start = src.find("BUG C verdict-gate (2026-05-27)")
    section = src[start:start + 4500]
    assert "plan.pickup_at" in section or "_cd_plan_pickup_at" in section
    assert "bag_context" in section
    assert "czas_kuriera_warsaw" in section
    assert 'order_event.get("czas_kuriera_warsaw")' in section


def test_commit_div_gate_one_sided_plan_later_than_commit():
    """One-sided: plan_eta - commit > próg (plan PÓŹNIEJ niż commit).
    Reverse (plan wcześniej, courier waits) = wait_courier penalty już łapie."""
    src = (inspect.getsource(dispatch_pipeline) + inspect.getsource(_k12s))
    start = src.find("BUG C verdict-gate (2026-05-27)")
    section = src[start:start + 4500]
    # one-sided: plan_dt_norm - commit_dt (not abs())
    assert "_plan_dt_norm - _commit_dt" in section
    # NIE używa abs() (vs marker w renderze, który używa abs dla obu kierunków)
    assert "abs(_diff" not in section


def test_commit_div_gate_emits_koord_verdict():
    """Bramka emituje verdict=KOORD z reason commit_divergence_gate."""
    src = (inspect.getsource(dispatch_pipeline) + inspect.getsource(_k12s))
    start = src.find("BUG C verdict-gate (2026-05-27)")
    section = src[start:start + 4500]
    assert 'verdict="KOORD"' in section
    assert "commit_divergence_gate" in section


def test_commit_div_gate_positioned_before_propose_return():
    """Bramka odpala PRZED finalnym _result_pf (PROPOSE) — KOORD wins."""
    src = (inspect.getsource(dispatch_pipeline) + inspect.getsource(_k12s))
    gate = src.find("BUG C verdict-gate (2026-05-27)")
    # _result_pf jest dwukrotnie — szukamy DRUGIEGO (final feasible path),
    # PRZED którym musi być nasza bramka. Wycinamy od pozycji gate'u w przód.
    propose_after_gate = src.find('_result_pf = PipelineResult(', gate)
    assert gate > 0 and propose_after_gate > 0
    assert gate < propose_after_gate, (
        f"bramka MUSI być przed PROPOSE return: gate={gate} propose={propose_after_gate}")


def test_commit_div_gate_surfaces_redirect_dict_for_telegram():
    """Wynik niesie dict commit_divergence_redirect z max divergence + worst_oid + threshold."""
    src = (inspect.getsource(dispatch_pipeline) + inspect.getsource(_k12s))
    start = src.find("BUG C verdict-gate (2026-05-27)")
    section = src[start:start + 5000]
    assert "commit_divergence_redirect" in section
    assert "max_divergence_min" in section
    assert "worst_oid" in section
    assert "threshold_min" in section


def test_commit_div_gate_fail_soft_on_unparseable_timestamps():
    """Defense-in-depth: nieparseowalny ISO commit/plan → skip oid (continue),
    nie crash całej bramki."""
    src = (inspect.getsource(dispatch_pipeline) + inspect.getsource(_k12s))
    start = src.find("BUG C verdict-gate (2026-05-27)")
    section = src[start:start + 5000]
    assert "except (TypeError, ValueError, AttributeError):" in section
    assert "continue" in section


def test_commit_div_common_contract_flag_default_off():
    """common: const default OFF (env unset → '0') — L0.1 D.5 2026-07-01.

    Historia: kontrakt 27.05 miał default ON („strict safety"), ale kanon
    ALWAYS-PROPOSE (werdykt Adriana) trzyma flags.json=False jako stan żywy.
    Const o PRZECIWNEJ intencji niż json = mina: utrata klucza json →
    decision_flag spada na const → gate CICHO flipuje ON (KOORD-redirect
    wraca wbrew kanonowi). Audyt spójności 30.06 (L0.1 D.5) → const
    wyrównany do intencji json. Efektywny stan produkcji BEZ ZMIAN
    (json wygrywa w decision_flag). Env override dla replay zostaje.
    """
    assert hasattr(common, "ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE")
    assert common.ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE is False


def test_commit_div_common_contract_threshold_default_10_min():
    """common: próg default 10 min (midpoint sprint planu 10/15/20)."""
    assert hasattr(common, "COMMIT_DIVERGENCE_VERDICT_KOORD_MIN_MIN")
    assert common.COMMIT_DIVERGENCE_VERDICT_KOORD_MIN_MIN == 10.0


def test_commit_div_flag_on_via_env(monkeypatch):
    """Env override (replay/kalibracja): =1 → True; unset wraca do default False."""
    monkeypatch.setenv("ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE", "1")
    import importlib
    _common_mod = importlib.reload(common)
    assert _common_mod.ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE is True
    # Przywróć stan default (env unset → "0") — reload, żeby moduł nie został
    # w stanie override dla kolejnych testów suity.
    monkeypatch.delenv("ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE", raising=False)
    importlib.reload(common)
    assert common.ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE is False


def test_commit_div_threshold_override_via_env(monkeypatch):
    """Env override: COMMIT_DIVERGENCE_VERDICT_KOORD_MIN_MIN=15.0 → próg 15."""
    monkeypatch.setenv("COMMIT_DIVERGENCE_VERDICT_KOORD_MIN_MIN", "15.0")
    import importlib
    _common_mod = importlib.reload(common)
    assert _common_mod.COMMIT_DIVERGENCE_VERDICT_KOORD_MIN_MIN == 15.0
    # Restore default
    monkeypatch.setenv("COMMIT_DIVERGENCE_VERDICT_KOORD_MIN_MIN", "10.0")
    importlib.reload(common)


# === Serialize KOORD-redirect dicts do shadow log ===
# KOORD verdicts NIE idą do Telegrama (shadow_tailer filtruje PROPOSE only).
# Strukturalny payload (best_effort_r6_redirect + commit_divergence_redirect)
# musi być w shadow_decisions.jsonl bo to JEDYNE miejsce persystencji dla
# analytics + replay. Pre-fix: payload tylko in-memory na PipelineResult.

def test_shadow_serialize_includes_commit_divergence_redirect():
    """_serialize_result emituje commit_divergence_redirect z PipelineResult."""
    from dispatch_v2 import shadow_dispatcher
    src = inspect.getsource(shadow_dispatcher)
    assert '"commit_divergence_redirect"' in src
    assert 'getattr(result, "commit_divergence_redirect", None)' in src


def test_shadow_serialize_includes_best_effort_r6_redirect():
    """_serialize_result emituje best_effort_r6_redirect (freebie BUG E gap)."""
    from dispatch_v2 import shadow_dispatcher
    src = inspect.getsource(shadow_dispatcher)
    assert '"best_effort_r6_redirect"' in src
    assert 'getattr(result, "best_effort_r6_redirect", None)' in src
