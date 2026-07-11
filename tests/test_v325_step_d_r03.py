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
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import manual_overrides as mo  # noqa: E402


# Hermetyczny komplet trzech źródeł identity + state override.
_TMPDIR = Path(tempfile.mkdtemp(prefix="v325_step_d_"))
TMP_OVERRIDES = str(_TMPDIR / "manual_overrides.json")
TMP_KURIER_IDS = str(_TMPDIR / "kurier_ids.json")
TMP_COURIER_NAMES = str(_TMPDIR / "courier_names.json")
TMP_GRAFIK_NAMES = str(_TMPDIR / "grafik_full_names.json")


def _seed_identity():
    kids = {
        "Adrian": 21,
        "Adrian R": 400,
        "Adrian Cit": 457,
        "Bartek O.": 123,
        "Mykyta K": 426,
        "Szymon Sa": 522,
    }
    names = {str(cid): name for name, cid in kids.items()}
    grafik = {
        "Adrian Czapla": 21,
        "Adrian Rogowski": 400,
        "Adrian Citko": 457,
        "Bartek Ołdziej": 123,
        "Mykyta K": 426,
        "Szymon Sa": 522,
    }
    for path, payload in (
        (TMP_KURIER_IDS, kids),
        (TMP_COURIER_NAMES, names),
        (TMP_GRAFIK_NAMES, grafik),
    ):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)


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
    orig = (mo.OVERRIDES_PATH, mo.KURIER_IDS_PATH,
            mo.COURIER_NAMES_PATH, mo.GRAFIK_FULL_NAMES_PATH)
    mo.OVERRIDES_PATH = TMP_OVERRIDES
    mo.KURIER_IDS_PATH = TMP_KURIER_IDS
    mo.COURIER_NAMES_PATH = TMP_COURIER_NAMES
    mo.GRAFIK_FULL_NAMES_PATH = TMP_GRAFIK_NAMES
    for effective_path in (
        mo.OVERRIDES_PATH, mo.KURIER_IDS_PATH,
        mo.COURIER_NAMES_PATH, mo.GRAFIK_FULL_NAMES_PATH,
    ):
        assert str(_TMPDIR) in effective_path
        assert "/root/.openclaw/workspace/dispatch_state/" not in effective_path
    _seed_identity()
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
        # 2026-06-01 working-override: include teraz daje uczciwy komunikat
        # "pracuje dziś — będę go proponował" (zamiast mylącego "wrócił do flow"),
        # bo zdejmuje ze STOP ORAZ dodaje syntetyczny wpis grafiku na dziś.
        expect("response: include + 'pracuje' + 'proponował'",
               "✅" in response and "pracuje" in response and "proponował" in response,
               f"got {response!r}")
        with open(TMP_OVERRIDES) as f: d = json.load(f)
        expect("excluded list NIE ma 'Bartek O.'", "Bartek O." not in d.get("excluded", []))
        expect("working-override dodany dla bartka (cid-keyed)",
               any(v.get("name") == "Bartek O." for v in d.get("working", {}).values()),
               f"working={d.get('working')}")

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
        (mo.OVERRIDES_PATH, mo.KURIER_IDS_PATH,
         mo.COURIER_NAMES_PATH, mo.GRAFIK_FULL_NAMES_PATH) = orig
        shutil.rmtree(_TMPDIR, ignore_errors=True)

    print(f"\n=== summary: {results['pass']} pass, {results['fail']} fail ===")
    return 0 if results["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
