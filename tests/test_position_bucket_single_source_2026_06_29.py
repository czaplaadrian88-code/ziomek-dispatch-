"""Sprint 3 NO-GPS-EQUAL (Adrian 2026-06-29) — STRAŻNIK przeciw 4. nawrotowi.

Dyskryminacja pozycji (no_gps/pre_shift → gorszy bucket) wracała ≥4× bo logika bucketu
była ZDUPLIKOWANA (selekcja, shadow-key, PLN-arm...). Każda naprawa scalała 1 kopię,
inne zostawały. Ten test pilnuje JEDNEGO źródła: inline-bucket pozycji
(`_is_blind_empty_cand(c) or _is_pre_shift_cand(c)`) MA istnieć tylko w `_selection_bucket`
(equal-treatment-aware). Każda dodatkowa kopia = czerwony test → udokumentuj/scal.
"""
import inspect
from pathlib import Path
from dispatch_v2 import dispatch_pipeline as dp

_SRC = Path(dp.__file__).read_text(encoding="utf-8")
_SIG = "_is_blind_empty_cand(c) or _is_pre_shift_cand(c)"


def test_position_bucket_single_inline_copy():
    hits = _SRC.count(_SIG)
    assert hits == 1, (
        f"{hits} inline-kopii bucketu pozycji w dispatch_pipeline.py — równe traktowanie "
        f"MA iść przez _selection_bucket (jedno źródło, flag-aware). Dodatkowa kopia "
        f"wskrzesza dyskryminację no-GPS/pre_shift (wzorzec #2 'klasa wraca'). Scal na "
        f"_selection_bucket albo świadomie udokumentuj wyjątek.")


def test_the_single_copy_lives_in_selection_bucket():
    assert _SIG in inspect.getsource(dp._selection_bucket), (
        "Jedyna inline-kopia bucketu pozycji powinna być w _selection_bucket.")


def test_selection_bucket_is_equal_treatment_aware():
    # _selection_bucket musi konsultować equal-treatment (no_gps/pre_shift po score gdy ON)
    src = inspect.getsource(dp._selection_bucket)
    assert "_equal_bucket_on" in src, "_selection_bucket musi respektować equal-treatment flagę."
