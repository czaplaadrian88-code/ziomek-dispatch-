"""P-7 (audyt 2026-06-24, CI-BONUS-COVERAGE): bonus_penalty_sum zmontowany z NAZWANEGO
słownika 19 termów (zamiast rozproszonej sumy). Test-strażnik: blokuje ciche zgubienie/
dodanie termu kary i pilnuje, że suma to dokładnie sum(values) (zachowanie 1:1).
"""
import inspect
import re

import dispatch_v2.dispatch_pipeline as DP
from dispatch_v2.core import candidates as _k11c
from dispatch_v2.core import selection as _k12s  # K11: cialo petli per-kurier (skan obu zrodel)

EXPECTED = {
    "r6_soft_pen", "r1_soft_pen", "r5_soft_pen", "r8_soft_pen", "r9_stopover",
    "r9_wait_pen", "bug4_cap_soft", "v325_pre_shift_soft", "d2_stale_soft",
    "v3273_wait_courier", "r1_corridor", "r5_detour", "wave_clean",
    "inter_wave_deadhead", "state_panel_mismatch", "coordinator_idle",
    "r_paczki_flex", "r_return_rest", "carry_chain_penalty",
}


def _dict_block():
    src = (inspect.getsource(DP) + inspect.getsource(_k11c) + inspect.getsource(_k12s))
    m = re.search(r"bonus_penalty_terms\s*=\s*\{(.*?)\n\s*\}", src, re.S)
    assert m, "bonus_penalty_terms dict musi istnieć (P-7 refactor)"
    return src, m.group(1)


def test_exactly_19_named_terms():
    _src, block = _dict_block()
    keys = re.findall(r'"([a-z0-9_]+)":', block)
    assert len(keys) == 19, f"oczekiwano 19 termów kary, jest {len(keys)}"
    assert set(keys) == EXPECTED, f"rozjazd termów: {set(keys) ^ EXPECTED}"


def test_sum_is_dict_values():
    src, _ = _dict_block()
    assert "bonus_penalty_sum = sum(bonus_penalty_terms.values())" in src, \
        "bonus_penalty_sum MUSI być sum(bonus_penalty_terms.values()) — zachowanie 1:1"


def test_only_r6_has_none_guard():
    # tylko r6_soft_pen miał `or 0.0` w oryginale; reszta to czyste floaty.
    _src, block = _dict_block()
    assert '"r6_soft_pen": (bonus_r6_soft_pen or 0.0)' in block
    # żaden inny term nie powinien dostać niespodziewanego `or` (sygnał zmiany semantyki)
    other_or = re.findall(r'"(?!r6_soft_pen)[a-z0-9_]+":\s*[^,]*\bor\b', block)
    assert not other_or, f"nieoczekiwany `or` guard w termach: {other_or}"
