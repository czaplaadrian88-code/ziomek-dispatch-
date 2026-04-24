"""C1 regression guard (sprint 2026-04-25):
solo_fallback path w dispatch_pipeline MUSI przekazać shift_start/shift_end
do check_feasibility_v2. V3.25 Schedule Hardening bez tych kwargs zwraca
NO_ACTIVE_SHIFT → solo_best=None → KOORD override.

Test nie uruchamia pełnego pipeline — dekoduje AST dispatch_pipeline.py
i sprawdza że fallback call site ma oba kwargi.
"""
import ast
import pathlib


DISPATCH_PIPELINE = pathlib.Path(__file__).resolve().parent.parent / "dispatch_pipeline.py"


def test_solo_fallback_passes_shift_params():
    src = DISPATCH_PIPELINE.read_text()
    tree = ast.parse(src)

    # Znajdź wszystkie Call do check_feasibility_v2
    cfv2_calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id == "check_feasibility_v2":
                cfv2_calls.append(node)

    assert len(cfv2_calls) >= 2, (
        f"Oczekiwano >=2 wywołań check_feasibility_v2 (main + solo_fallback), "
        f"znaleziono {len(cfv2_calls)}"
    )

    # KAŻDE wywołanie musi mieć oba kwargi — inaczej V3.25 hardening zwróci
    # NO_ACTIVE_SHIFT dla candidate'a z tym call site.
    missing = []
    for call in cfv2_calls:
        kw_names = {kw.arg for kw in call.keywords if kw.arg}
        if "shift_start" not in kw_names or "shift_end" not in kw_names:
            missing.append((call.lineno, sorted(kw_names)))

    assert not missing, (
        f"check_feasibility_v2 call sites bez shift_start/shift_end (V3.25 bug C1): "
        f"{missing}"
    )


if __name__ == "__main__":
    test_solo_fallback_passes_shift_params()
    print("test_c1_solo_fallback_shift_params: PASS")
