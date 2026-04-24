"""V3.26 hotfix — telegram_approver parser BUG 1+2 fix tests.

Covers:
- BUG 1 (telegram_approver pre-check): "adrian cit nie pracuje" → exclude (NIE OPERATOR_COMMENT)
- BUG 2 (manual_overrides _find_courier): "adrian citko" → "Adrian Cit" (NIE "Adrian")
- CHANGE 3: cid w confirmation
- CHANGE 4: /help (_v326_help_text helper)

Style mirrors test_v325_step_d_r03.py (custom main, no pytest classes).
"""
import importlib
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import manual_overrides as mo  # noqa: E402

TMP_OVERRIDES = "/tmp/test_v326_hotfix_overrides.json"


def _reset_state():
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

    importlib.reload(mo)
    orig = mo.OVERRIDES_PATH
    mo.OVERRIDES_PATH = TMP_OVERRIDES
    _reset_state()

    try:
        # === a) "adrian cit nie pracuje" → exclude Adrian Cit (cid=457) ===
        print("\n=== a) 'adrian cit nie pracuje' → Adrian Cit cid=457 ===")
        action, response = mo.parse_command("adrian cit nie pracuje")
        expect("a/action == exclude", action == "exclude", f"got {action}")
        expect("a/response zawiera 'Adrian Cit'", "Adrian Cit" in response,
               f"got {response!r}")
        expect("a/response zawiera '(cid=457)'", "(cid=457)" in response,
               f"got {response!r}")

        # === b) "adrian citko nie pracuje" → exclude Adrian Cit (cid=457) ===
        print("\n=== b) 'adrian citko nie pracuje' → Adrian Cit (BUG 2 fix) ===")
        _reset_state()
        action, response = mo.parse_command("adrian citko nie pracuje")
        expect("b/action == exclude", action == "exclude")
        expect("b/response zawiera 'Adrian Cit' (NIE 'Adrian')",
               "Adrian Cit" in response, f"got {response!r}")
        expect("b/response zawiera '(cid=457)'", "(cid=457)" in response,
               f"got {response!r}")
        expect("b/response NIE zawiera '(cid=21)'", "(cid=21)" not in response,
               f"got {response!r}")

        # === c) "adrian r nie pracuje" → Adrian R (cid=400) ===
        print("\n=== c) 'adrian r nie pracuje' → Adrian R cid=400 ===")
        _reset_state()
        action, response = mo.parse_command("adrian r nie pracuje")
        expect("c/action == exclude", action == "exclude")
        expect("c/response 'Adrian R'", "Adrian R" in response, f"got {response!r}")
        expect("c/response '(cid=400)'", "(cid=400)" in response, f"got {response!r}")

        # === d) "bartek o nie pracuje" → Bartek O. (cid=123) ===
        print("\n=== d) 'bartek o nie pracuje' → Bartek O. cid=123 ===")
        _reset_state()
        action, response = mo.parse_command("bartek o nie pracuje")
        expect("d/action == exclude", action == "exclude")
        expect("d/response 'Bartek O.'", "Bartek O." in response, f"got {response!r}")
        expect("d/response '(cid=123)'", "(cid=123)" in response, f"got {response!r}")

        # === e) "adrian cit wraca" → include Adrian Cit ===
        print("\n=== e) 'adrian cit wraca' → include Adrian Cit ===")
        # First exclude
        mo.parse_command("adrian cit nie pracuje")
        action, response = mo.parse_command("adrian cit wraca")
        expect("e/action == include", action == "include",
               f"got {action}: {response!r}")
        expect("e/response 'Adrian Cit'", "Adrian Cit" in response)
        expect("e/response '(cid=457)'", "(cid=457)" in response)

        # === f) "/stop adrian cit" → exclude Adrian Cit ===
        print("\n=== f) '/stop adrian cit' → exclude Adrian Cit ===")
        _reset_state()
        action, response = mo.parse_command("/stop adrian cit")
        expect("f/action == exclude", action == "exclude")
        expect("f/response 'Adrian Cit'", "Adrian Cit" in response)
        expect("f/response '(cid=457)'", "(cid=457)" in response)

        # === g) "/wraca adrian cit" → include ===
        print("\n=== g) '/wraca adrian cit' → include Adrian Cit ===")
        action, response = mo.parse_command("/wraca adrian cit")
        expect("g/action == include", action == "include")
        expect("g/response 'Adrian Cit'", "Adrian Cit" in response)
        expect("g/response '(cid=457)'", "(cid=457)" in response)

        # === h) /help — test _v326_help_text helper ===
        print("\n=== h) _v326_help_text() helper ===")
        from dispatch_v2 import telegram_approver as ta
        help_body = ta._v326_help_text()
        expect("h/help zawiera /stop", "/stop" in help_body)
        expect("h/help zawiera /wraca", "/wraca" in help_body)
        expect("h/help zawiera 'panel'", "panel" in help_body.lower())
        expect("h/help zawiera 'cid'", "cid" in help_body.lower())

        # === i) "adrian nie pracuje" → exclude (visible cid w response) ===
        print("\n=== i) 'adrian nie pracuje' → visible cid (whichever match) ===")
        _reset_state()
        action, response = mo.parse_command("adrian nie pracuje")
        expect("i/action == exclude", action == "exclude")
        expect("i/response zawiera '(cid=' (visible)",
               "(cid=" in response, f"got {response!r}")
        # NIE wymagamy konkretnego matched cid — visible cid pozwala user weryfikować

        # === R1) regression: "Mykyta nie pracuje" → Mykyta K (cid=426) ===
        print("\n=== R1) regression 'Mykyta nie pracuje' → Mykyta K ===")
        _reset_state()
        action, response = mo.parse_command("Mykyta nie pracuje")
        expect("R1/regression Mykyta",
               action == "exclude" and "Mykyta K" in response,
               f"got {action}: {response!r}")

        # === R2) regression V3.25: "/stop bartek" → Bartek O. ===
        print("\n=== R2) regression V3.25 '/stop bartek' → Bartek O. ===")
        _reset_state()
        action, response = mo.parse_command("/stop bartek")
        expect("R2/regression V3.25 /stop bartek",
               action == "exclude" and "Bartek O." in response,
               f"got {action}: {response!r}")

        # === R3) regression: reset ===
        print("\n=== R3) regression: reset ===")
        action, response = mo.parse_command("reset")
        expect("R3/reset", action == "reset", f"got {action}: {response!r}")

        # === R4) regression: unknown courier ===
        print("\n=== R4) regression: '/stop xyzNonExistent' → unknown ===")
        action, response = mo.parse_command("/stop xyzNonExistent")
        expect("R4/unknown", action == "unknown", f"got {action}: {response!r}")

        # === R5) regression: _parse_courier_time z known_names dla REPLY flow ===
        # Pre-existing helper behavior (NIE dotykane przez V3.26 hotfix):
        # "Bartek 14:30" + known=["Bartek O.","Adrian Cit"] → match przez first-word
        # fallback ("Bartek") bo text_norm "bartek" != "bartek o" startswith.
        # Test waliduje że helper nadal działa (parses, ma czas, ma imię).
        print("\n=== R5) regression _parse_courier_time('Bartek 14:30') ===")
        from dispatch_v2 import telegram_approver as ta
        parsed = ta._parse_courier_time(
            "Bartek 14:30", allow_name_only=False,
            known_names=["Bartek O.", "Adrian Cit"]
        )
        expect("R5/parse_courier_time 'Bartek 14:30' returns tuple",
               parsed is not None and isinstance(parsed, tuple) and len(parsed) == 2,
               f"got {parsed!r}")
        if parsed is not None:
            expect("R5/courier_name starts with 'Bartek'",
                   parsed[0].lower().startswith("bartek"), f"got {parsed[0]!r}")
            expect("R5/time_min > 0 (HH:MM parsed)",
                   parsed[1] is not None and parsed[1] > 0, f"got {parsed!r}")

    finally:
        mo.OVERRIDES_PATH = orig
        try:
            os.unlink(TMP_OVERRIDES)
        except OSError:
            pass

    print(f"\n=== summary: {results['pass']} pass, {results['fail']} fail ===")
    return 0 if results["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
