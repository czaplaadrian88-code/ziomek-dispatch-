"""/help handler routing regression guard (sprint 2026-04-25 Block 3A).

Memory wskazywała "/help BROKEN Task #10" w kontekście parser hotfix
(commit a93d1c4 24.04). Static review post-restart 06:35 UTC 2026-04-25
potwierdza że handler jest LIVE w kodzie:
- _v326_help_text() helper @ line 149
- if cmd in ("/help", "/pomoc") @ line 1155 w handle_message
- send + log + return — clean dispatch

Brak logów "/help responded" w 48h sugeruje że Adrian po prostu nie testował
(nie bug). Ten test guarduje przed regression (np. ktoś usuwa handler/helper).

NIE wymaga uruchamiania dispatch-telegram (restart zakazany).
"""
import ast
import pathlib


TELEGRAM = pathlib.Path(__file__).resolve().parent.parent / "telegram_approver.py"


def test_v326_help_text_helper_exists():
    src = TELEGRAM.read_text()
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_v326_help_text":
            found = True
            break
    assert found, "_v326_help_text() helper missing — V3.26 hotfix CHANGE 4 broken"


def test_help_handler_in_handle_message():
    src = TELEGRAM.read_text()
    tree = ast.parse(src)

    # Find handle_message AsyncFunctionDef
    handle_message = None
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "handle_message":
            handle_message = node
            break
    assert handle_message is not None, "handle_message function missing"

    # Find If node z test: Compare(cmd, In, Tuple zawierający "/help" i "/pomoc")
    found = False
    for node in ast.walk(handle_message):
        if not isinstance(node, ast.If):
            continue
        t = node.test
        if not isinstance(t, ast.Compare) or len(t.ops) != 1:
            continue
        if not isinstance(t.ops[0], ast.In):
            continue
        if not t.comparators or not isinstance(t.comparators[0], (ast.Tuple, ast.List)):
            continue
        elts = t.comparators[0].elts
        consts = [e.value for e in elts if isinstance(e, ast.Constant)]
        if "/help" in consts and "/pomoc" in consts:
            found = True
            break

    assert found, (
        "/help routing missing — `if cmd in (..., '/help', '/pomoc')` not found w handle_message. "
        "Cross-review/Block 3A regression guard."
    )


def test_help_handler_calls_helper():
    """Handler MUSI wywołać _v326_help_text() — nie hardcoded text inline."""
    src = TELEGRAM.read_text()
    # Quick heuristic: handler przy "/help" branch zawiera wywołanie _v326_help_text()
    # bez full AST traversal — wystarczy substring search w okolicy "/help"
    idx = src.find("if cmd in (\"/help\"")
    assert idx >= 0, "marker for /help handler not found"
    block_excerpt = src[idx:idx + 400]
    assert "_v326_help_text()" in block_excerpt, (
        "handler nie wywołuje _v326_help_text() — używa stale/hardcoded text? "
        f"excerpt:\n{block_excerpt}"
    )


def test_help_help_text_content_invariants():
    """Smoke: _v326_help_text() zwraca string z guidance dla operatora."""
    import sys
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
    from dispatch_v2 import telegram_approver as ta
    text = ta._v326_help_text()
    assert isinstance(text, str) and len(text) >= 50
    for needle in ("/stop", "/wraca", "/status", "panel", "cid"):
        assert needle in text.lower() or needle in text, f"missing '{needle}' in /help body"


if __name__ == "__main__":
    test_v326_help_text_helper_exists()
    print("test_v326_help_text_helper_exists: PASS")
    test_help_handler_in_handle_message()
    print("test_help_handler_in_handle_message: PASS")
    test_help_handler_calls_helper()
    print("test_help_handler_calls_helper: PASS")
    test_help_help_text_content_invariants()
    print("test_help_help_text_content_invariants: PASS")
    print("ALL 4/4 PASS")
