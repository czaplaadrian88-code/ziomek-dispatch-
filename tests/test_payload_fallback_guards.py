"""Guard prewencyjny (2026-06-13, klasa bugu B3): zakazuje antywzorca
`X.get(KEY, now_iso())` i wymusza parytet emit==update na ścieżkach delivered.

Antywzorzec: `dict.get(klucz, default)` zwraca None gdy klucz ISTNIEJE z wartością
None. Gdy payload poda {"timestamp": None} (reconcile/panel_diff/packs_ghost podawały
tak po doręczeniu — emit miał `or now_iso()`, update_from_event NIE), delivered_at /
picked_up_at / first_seen = None zamiast defaultu → root B3 (znikanie z "Doręczone" +
utarg 0). Poprawny wzorzec: `.get(K) or now_iso()` (łapie też None-value).

Testy skanują ŹRÓDŁO rekursywnie (jak test_f10) — łapią reintrodukcję w CI / code review.
Standalone: /root/.openclaw/venvs/dispatch/bin/python tests/test_payload_fallback_guards.py
"""
import re
from pathlib import Path

DISPATCH = Path(__file__).resolve().parents[1]        # dispatch_v2/
SCRIPTS = DISPATCH.parent                             # scripts/
COURIER_API = SCRIPTS / "courier_api"

_ANTIPATTERN = re.compile(r"\.get\([^,)]+,\s*now_iso\(\)\s*\)")
_DELIVERED_TS = re.compile(r'"timestamp":\s*\w+\.get\("czas_doreczenia"\)')


def _py_files(base):
    for p in base.rglob("*.py"):
        s = str(p)
        if "/tests/" in s or ".bak" in s or "/eod_drafts/" in s or "__pycache__" in s:
            continue
        yield p


def _code_lines(path):
    """(lineno, kod-bez-komentarza, surowa-linia) — komentarz ucięty na pierwszym #."""
    for i, raw in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        yield i, raw.split("#", 1)[0], raw


def test_no_get_default_now_iso_antipattern():
    """ZERO `.get(KEY, now_iso())` w dispatch_v2 + courier_api (poza komentarzami)."""
    hits = []
    for base in (DISPATCH, COURIER_API):
        for path in _py_files(base):
            for lineno, code, raw in _code_lines(path):
                if _ANTIPATTERN.search(code):
                    hits.append(f"{path.relative_to(SCRIPTS)}:{lineno}: {raw.strip()}")
    assert not hits, (
        "Antywzorzec `.get(KEY, now_iso())` (default martwy dla wartości None) — "
        "użyj `.get(KEY) or now_iso()`:\n  " + "\n  ".join(hits))


def test_delivered_emit_update_timestamp_parity():
    """Każdy delivered `"timestamp": X.get("czas_doreczenia")` w panel_watcher MA `or now_iso()`
    (emit i update_from_event MUSZĄ dostać identyczny timestamp — root B3 to ich rozjazd)."""
    pw = DISPATCH / "panel_watcher.py"
    offenders = []
    for lineno, code, raw in _code_lines(pw):
        if _DELIVERED_TS.search(code) and "or now_iso()" not in code:
            offenders.append(f"panel_watcher.py:{lineno}: {raw.strip()}")
    assert not offenders, (
        "Rozjazd emit==update na ścieżce delivered (timestamp bez fallbacku) — "
        "dodaj `or now_iso()` jak w emit:\n  " + "\n  ".join(offenders))


if __name__ == "__main__":
    import sys
    fails = 0
    for _n, _f in sorted(globals().items()):
        if _n.startswith("test_") and callable(_f):
            try:
                _f(); print(f"  PASS  {_n}")
            except AssertionError as e:
                fails += 1; print(f"  FAIL  {_n}: {e}")
    print("ALL PASS" if not fails else f"{fails} FAIL")
    sys.exit(1 if fails else 0)
