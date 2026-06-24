#!/usr/bin/env python3
"""READ-ONLY higiena flag: wykrywa flagi-sieroty w flags.json (klucz, którego NIKT nie czyta).

Geneza: audyt 2026-06-24 (W-6) — `feasibility_check` był zombie (0 odczytów), a dokumentacja
twierdziła że LIVE-ON. Ten checker łapie KLASĘ takich bugów na przyszłość (CI-ORPHAN-FLAG):
po każdym dodaniu/usunięciu flagi odpal, żeby nie zostawić martwego klucza ani nie usunąć
czytanego. Świadomy o dynamicznych czytelnikach (flag(f"..."), environ.get(prefix+...)).

Użycie: python3 tools/flag_hygiene_check.py   (exit 0 = czysto, 1 = znaleziono sieroty)
"""
import json, os, re, sys

SCRIPTS = "/root/.openclaw/workspace/scripts"
FLAGS = os.path.join(SCRIPTS, "flags.json")

def _py_files():
    out = []
    for root, _, fs in os.walk(SCRIPTS):
        if "/.git" in root or "/venv" in root or "site-packages" in root:
            continue
        for f in fs:
            if f.endswith(".py") and ".bak" not in f:
                out.append(os.path.join(root, f))
    return out

def main():
    flags = json.load(open(FLAGS))
    keys = [k for k in flags if not k.startswith("_comment")]
    blob = {}
    for f in _py_files():
        try:
            blob[f] = open(f, encoding="utf-8", errors="ignore").read()
        except Exception:
            pass
    alltext = "\n".join(blob.values())

    # 1) literalne odwołania ("KEY" lub 'KEY') — łapie flag("KEY"), load_flags().get("KEY"), os.environ.get("KEY")
    orphans = [k for k in keys if ('"' + k + '"') not in alltext and ("'" + k + "'") not in alltext]

    # 2) dynamiczni czytelnicy — flagi mogą być budowane z prefiksu; raportuj jako OSTRZEŻENIE
    dyn = []
    for f, t in blob.items():
        for m in re.finditer(r'(flag|environ\.get)\(\s*(f["\']|[A-Za-z_]+\s*[+%]|[A-Za-z_.]+\.format)', t):
            ln = t[:m.start()].count("\n") + 1
            dyn.append(f"{os.path.relpath(f, SCRIPTS)}:{ln}")

    print(f"flags.json: {len(keys)} kluczy | odwoływane: {len(keys)-len(orphans)} | SIEROTY: {len(orphans)}")
    if orphans:
        print("\n⚠ FLAGI-SIEROTY (klucz w flags.json, 0 odczytów literalnych — usuń lub sprawdź dynamicznych czytelników):")
        for k in sorted(orphans):
            print(f"   {k} = {flags[k]}")
    if dyn:
        print(f"\nℹ Dynamiczni czytelnicy flag ({len(set(dyn))} miejsc) — sieroty mogą być czytane przez nie, zweryfikuj ręcznie:")
        for d in sorted(set(dyn)):
            print(f"   {d}")
    return 1 if orphans else 0

if __name__ == "__main__":
    sys.exit(main())
