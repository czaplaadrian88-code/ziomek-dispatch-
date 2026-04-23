"""V3.25 STEP D R-03 core: /stop + /wraca slash commands w manual_overrides.

Tests:
- T1 /stop bartek → exclude Bartek O. (substring fuzzy match)
- T2 /stop xyzNonExistent → unknown
- T3 /stop (no name) → unknown z hint
- T4 /wraca bartek → include (after T1)
- T5 /wrocil bartek → też działa (alias)
- T6 legacy "Mykyta nie pracuje" wciąż działa (regression)
- T7 reset wciąż działa
- T8 case insensitive: /STOP bartek
- T9 idempotent: /stop bartek twice — drugi pasuje (no duplicate excluded)
- T10 fresh courier (Szymon Sa po STEP A) findable via /stop szymon

NIE testuje live integration (wymaga restart dispatch-telegram + Telegram traffic).
"""
import importlib
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import manual_overrides as mo  # noqa: E402


# Use temp file dla testów żeby nie modyfikować production state
TMP_OVERRIDES = "/tmp/test_v325_step_d_overrides.json"


def _reset_state():
    """Clear test override file."""
    with open(TMP_OVERRIDES, "w") as f:
        json.dump({"excluded": [], "updated_at": ""}, f)


def main():
    results = {"pass": 0, "fail": 0}

    def expect(label, cond, detail=""):
        if cond:
            print(f"  ✅ {label}")
            results["pass"] += 1
        else:
            print(f"  ❌ {label}  {detail}")
            results["fail"] += 1

    # Patch OVERRIDES_PATH to test file
    importlib.reload(mo)
    orig = mo.OVERRIDES_PATH
    mo.OVERRIDES_PATH = TMP_OVERRIDES
    _reset_state()

    try:
        # ---------- T1: /stop bartek ----------
        print("\n=== T1: /stop bartek (fuzzy) ===")
        action, response = mo.parse_command("/stop bartek")
        expect("action == 'exclude'", action == "exclude", f"got {action}")
        expect("response zaczyna od '🛑'", "🛑" in response, f"got {response!r}")
        expect("response zawiera 'Bartek O.'", "Bartek O." in response, f"got {response!r}")
        # Verify state file updated
        with open(TMP_OVERRIDES) as f: d = json.load(f)
        expect("excluded list ma 'Bartek O.'", "Bartek O." in d.get("excluded", []),
               f"excluded={d.get('excluded')}")

        # ---------- T2: /stop xyzNonExistent ----------
        print("\n=== T2: /stop xyz (no match) ===")
        action, response = mo.parse_command("/stop xyzNonExistent")
        expect("action == 'unknown'", action == "unknown")
        expect("response 'Nie znalazłem'", "Nie znalazłem" in response, f"got {response!r}")

        # ---------- T3: /stop bez nazwy ----------
        print("\n=== T3: /stop (no name) ===")
        action, response = mo.parse_command("/stop")
        expect("action == 'unknown'", action == "unknown")
        expect("response z 'Użycie:'", "Użycie" in response, f"got {response!r}")

        # ---------- T4: /wraca bartek ----------
        print("\n=== T4: /wraca bartek ===")
        action, response = mo.parse_command("/wraca bartek")
        expect("action == 'include'", action == "include")
        expect("response zawiera '✅' i 'wrócił'", "✅" in response and "wrócił" in response,
               f"got {response!r}")
        with open(TMP_OVERRIDES) as f: d = json.load(f)
        expect("excluded list NIE ma 'Bartek O.'", "Bartek O." not in d.get("excluded", []))

        # ---------- T5: /wrocil bartek (alias) ----------
        print("\n=== T5: /wrocil bartek (alias spelling) ===")
        # First exclude
        mo.parse_command("/stop bartek")
        action, response = mo.parse_command("/wrocil bartek")
        expect("/wrocil action == 'include'", action == "include")

        # ---------- T6: legacy 'Mykyta nie pracuje' ----------
        print("\n=== T6: legacy 'Mykyta nie pracuje' regression ===")
        action, response = mo.parse_command("Mykyta nie pracuje")
        expect("legacy exclude działa", action == "exclude", f"got {action}: {response!r}")

        # ---------- T7: reset ----------
        print("\n=== T7: reset ===")
        action, response = mo.parse_command("reset")
        expect("reset action", action == "reset")
        with open(TMP_OVERRIDES) as f: d = json.load(f)
        expect("excluded empty po reset", d.get("excluded") == [])

        # ---------- T8: case insensitive ----------
        print("\n=== T8: case insensitive /STOP ===")
        action, response = mo.parse_command("/STOP bartek")
        expect("/STOP (uppercase) działa", action == "exclude",
               f"got {action}: {response!r}")

        # ---------- T9: idempotent /stop twice ----------
        print("\n=== T9: idempotent /stop bartek twice ===")
        _reset_state()
        mo.parse_command("/stop bartek")
        with open(TMP_OVERRIDES) as f: d1 = json.load(f)
        mo.parse_command("/stop bartek")
        with open(TMP_OVERRIDES) as f: d2 = json.load(f)
        expect("idempotent — excluded count unchanged",
               d1.get("excluded") == d2.get("excluded"),
               f"d1={d1.get('excluded')} d2={d2.get('excluded')}")

        # ---------- T10: fresh courier post-STEP A (Szymon Sa) ----------
        print("\n=== T10: /stop szymon → 'Szymon Sa' (post STEP A) ===")
        _reset_state()
        action, response = mo.parse_command("/stop szymon")
        expect("/stop szymon match found", action == "exclude",
               f"got {action}: {response!r}")
        with open(TMP_OVERRIDES) as f: d = json.load(f)
        excluded = d.get("excluded", [])
        # Could match 'Szymon Sa' OR 'Szymon P' (whichever first in sorted dedupe)
        szymon_match = [n for n in excluded if "Szymon" in n]
        expect("Szymon match w excluded list", len(szymon_match) > 0,
               f"excluded={excluded}")

    finally:
        mo.OVERRIDES_PATH = orig
        try: os.unlink(TMP_OVERRIDES)
        except OSError: pass

    print(f"\n=== summary: {results['pass']} pass, {results['fail']} fail ===")
    return 0 if results["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
