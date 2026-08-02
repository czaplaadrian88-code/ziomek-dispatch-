#!/usr/bin/env python3
"""Negatywny oracle + mutation ratchet bramki PII bundlera blind-review.

Oracle jest NEGATYWNY: buduje syntetyczne WABIKI (zero prawdziwych danych — imiona
z listy oczywistych atrap, klucz „prywatny" bez materiału kryptograficznego) i
żąda, żeby driver ODMÓWIŁ budowy bundla oraz żeby na dysku nie powstał ani jeden
plik bundla. Reprodukuje near-miss 2026-08-01: plik klasy PII w szerokim zakresie
`blind .` przechodził do bundla recenzenta.

Mutation ratchet: kopiuje skill do tmp, po kolei OSŁABIA politykę (kasuje reguły
ścieżkowe / treściowe / obie) i żąda, żeby oracle po każdej mutacji ZCZERWIENIAŁ.
Jeśli mutant przechodzi — polityka jest martwa i selftest ma paść.

Uruchomienie: `python3 pii_oracle.py` (exit 0 = OK). Wywoływany z selftest.sh,
a przez `tests/test_skills_selftest.py` — z nocnej regresji.
Zero sieci, zero prod-state, zapisy wyłącznie do mktemp.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = sys.executable or "python3"
RC_SENSITIVE = 3

# ── WABIKI — w 100% syntetyczne, żadnych prawdziwych osób ani sekretów ────────
DECOY_NAMES = ["Jan Kowalski", "Anna Nowak", "Piotr Wisniewski", "Ewa Zielinska"]

FAKE_TOKEN = "1234567890:" + ("A" * 35)          # kształt tokenu bota, nie token
FAKE_AWS = "AKIA" + ("Q" * 16)                   # kształt klucza AWS, nie klucz


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def build_decoys(root: Path) -> dict[str, Path]:
    """Katalogi-kandydaci, każdy z JEDNYM wabikiem innej klasy + neutralny artefakt."""
    cases: dict[str, Path] = {}

    def case(name: str) -> Path:
        d = root / name
        _write(d / "SKILL.md", "# kandydat\n\nNeutralny artefakt do recenzji.\n")
        cases[name] = d
        return d

    # 1. PII po ŚCIEŻCE — dokładnie klasa pliku z near-missu (nazwa + katalog danych)
    d = case("path-pii-fullnames")
    _write(d / "daily_accounting" / "kurier_full_names.json",
           json.dumps({"101": "AAA", "102": "BBB"}, ensure_ascii=False))

    # 2. PII po TREŚCI — plik nazwany NEUTRALNIE, wartości w kształcie nazwisk
    d = case("content-pii-neutral-name")
    _write(d / "dane.json", json.dumps(
        {str(1000 + i): n for i, n in enumerate(DECOY_NAMES)}, ensure_ascii=False))

    # 3. PII po TREŚCI — klucze pól osobowych
    d = case("content-pii-keys")
    _write(d / "rekordy.json", json.dumps(
        [{"pesel": "00000000000", "telefon": "000000000"}], ensure_ascii=False))

    # 4. SEKRET po ŚCIEŻCE — rozszerzenie klucza
    d = case("path-secret-pem")
    _write(d / "deploy.pem", "-----BEGIN NOTHING-----\nnie-jest-kluczem\n")

    # 5. SEKRET po TREŚCI — plik .md z materiałem w kształcie tokenu
    d = case("content-secret-token")
    _write(d / "konfiguracja.md", f"Ustaw zmienną:\n\n    BOT={FAKE_TOKEN}\n    AWS={FAKE_AWS}\n")

    # 6. SEKRET po TREŚCI — przypisany literał w kodzie (atrapa o kształcie sekretu),
    #    z ATRAPĄ-PLACEHOLDEREM w tym samym pliku: jedna atrapa nie może unieważnić reguły
    d = case("content-secret-assigned")
    _write(d / "settings.py",
           'API_KEY = "<your_token_here>"\n'
           'password = "Qx7' + "Zk29" + 'Lm4Tb8Rv"\n')

    # 7. UCIECZKA ZAKRESU — dowiązanie do pliku PII spoza katalogu kandydata
    #    (nazwa dowiązania neutralna: sam path-matching by go nie złapał)
    outside = root / "poza-kandydatem"
    _write(outside / "kurier_full_names.json", json.dumps({"1": "AAA"}))
    d = case("scope-escape-symlink")
    (d / "dane_wejsciowe.json").symlink_to(outside / "kurier_full_names.json")

    # 8. KONTROLA FAŁSZYWIE-POZYTYWNA — realistyczny kandydat MUSI przejść
    d = case("clean-control")
    _write(d / "config.json", json.dumps(
        {"peak_start": "17:00", "tier": "gold", "api_key": "${DISPATCH_API_KEY}",
         "adres_bazy": "Bialystok, ul. Testowa 1"}, ensure_ascii=False))
    _write(d / "notes.txt", "Zmiana dotyczy Definition Of Done oraz Blind Review.\n")
    # dokładnie te dwa kształty dawały fałszywy alarm na realnym repo (01.08 survey)
    _write(d / "test_zakres.py", 'secret = "sensitive-order-and-courier-data"\n')
    _write(d / "bezpieczenstwo.md", "Ustaw `BOT_TOKEN=<token_bota>` w drop-inie.\n")
    return cases


def run_driver(skill_dir: Path, candidate: Path, out: Path,
               allow: tuple[str, ...] = ()) -> subprocess.CompletedProcess:
    cmd = [PY, str(skill_dir / "driver.py"), "blind", str(candidate), "--out", str(out)]
    for a in allow:
        cmd += ["--allow-sensitive", a]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=180,
                          env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1",
                               "HOME": str(out.parent)})


def oracle(skill_dir: Path, workdir: Path, verbose: bool = True) -> list[str]:
    """Zwraca listę FAILÓW (pusta = oracle zielony)."""
    fails: list[str] = []
    cases = build_decoys(workdir / "candidates")
    outs = workdir / "out"

    for name, cand in sorted(cases.items()):
        out = outs / name
        r = run_driver(skill_dir, cand, out)
        clean_expected = name == "clean-control"
        if clean_expected:
            if r.returncode != 0:
                fails.append(f"{name}: kontrola czysta ODRZUCONA (rc={r.returncode}) "
                             f"— polityka za szeroka\n{r.stderr.strip()[:400]}")
            elif not (out / "config.json").is_file():
                fails.append(f"{name}: bundle nie zawiera artefaktu kandydata")
            continue
        if r.returncode != RC_SENSITIVE:
            fails.append(f"{name}: oczekiwano odmowy rc={RC_SENSITIVE}, jest rc={r.returncode}")
        # KLUCZOWE: przy odmowie na dysku nie ma ANI JEDNEGO pliku bundla
        leaked = sorted(p.name for p in out.rglob("*")) if out.exists() else []
        if leaked:
            fails.append(f"{name}: przy odmowie powstały pliki bundla: {leaked}")
        if out.parent.exists():
            manifest = out.parent / f"{out.name}.manifest.json"
            if manifest.exists():
                fails.append(f"{name}: przy odmowie powstał manifest")

    # allowlist per plik: ten sam wabik przechodzi, gdy operator go jawnie odblokuje
    cand = cases["path-pii-fullnames"]
    out = outs / "allowlisted"
    r = run_driver(skill_dir, cand, out, allow=("daily_accounting/kurier_full_names.json",))
    if r.returncode != 0:
        fails.append(f"allowlist: jawny --allow-sensitive nie przepuścił (rc={r.returncode})\n"
                     f"{r.stderr.strip()[:400]}")

    # literówka w allowliście NIE może cicho przejść
    out = outs / "allow-typo"
    r = run_driver(skill_dir, cases["clean-control"], out, allow=("nie/ma/takiego.json",))
    if r.returncode != RC_SENSITIVE:
        fails.append(f"allowlist-typo: oczekiwano odmowy, rc={r.returncode}")

    # komunikat odmowy nie może cytować wabika
    r = run_driver(skill_dir, cases["content-pii-neutral-name"], outs / "leaktest")
    if any(n in r.stdout + r.stderr for n in DECOY_NAMES):
        fails.append("komunikat odmowy cytuje dopasowaną treść (wyciek do raportu)")

    if verbose:
        for f in fails:
            print(f"  FAIL {f}")
    return fails


# ── MUTATION RATCHET ─────────────────────────────────────────────────────────
MUTANTS: dict[str, tuple[tuple[str, str], ...]] = {
    "wykasowane-reguly-sciezkowe": (
        ("SECRET_GLOBS = (", "SECRET_GLOBS = ()\n_DEAD_SECRET_GLOBS = ("),
        ("PII_NAME_TOKENS_ANY = (", "PII_NAME_TOKENS_ANY = ()\n_DEAD_PII_ANY = ("),
        ("PII_DATA_DIRS = (", "PII_DATA_DIRS = ()\n_DEAD_PII_DIRS = ("),
        ("SECRET_NAME_TOKENS = (", "SECRET_NAME_TOKENS = ()\n_DEAD_SECRET_TOKENS = ("),
    ),
    "wykasowane-reguly-tresciowe": (
        ("SECRET_CONTENT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (",
         "SECRET_CONTENT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = ()\n_DEAD_CONTENT = ("),
        ("NAME_VALUE_MIN_HITS = 3", "NAME_VALUE_MIN_HITS = 10_000"),
        ("PHONE_MIN_HITS = 3", "PHONE_MIN_HITS = 10_000"),
        ('PII_KEY_RE = re.compile(', 'PII_KEY_RE = re.compile(r"(?!x)x")\n_DEAD_KEY_RE = re.compile('),
    ),
    "wylaczona-detekcja-ucieczki-zakresu": (
        ("    for rel in iter_scope_escapes(root):", "    for rel in ():"),
    ),
    "odwrocony-fail-closed": (
        ("    if screening.blocked:", "    if False:"),
    ),
    "cichy-skip-zamiast-odmowy": (
        ("        return RC_SENSITIVE", "        pass"),
    ),
}


def mutate(skill_dir: Path, edits: tuple[tuple[str, str], ...]) -> bool:
    for target in ("pii_denylist.py", "driver.py"):
        p = skill_dir / target
        text = p.read_text(encoding="utf-8")
        changed = False
        for old, new in edits:
            if old in text:
                text = text.replace(old, new, 1)
                changed = True
        if changed:
            p.write_text(text, encoding="utf-8")
    return True


def mutation_ratchet(workdir: Path) -> list[str]:
    fails: list[str] = []
    for name, edits in sorted(MUTANTS.items()):
        mdir = workdir / "mutants" / name
        mdir.mkdir(parents=True, exist_ok=True)
        skill = mdir / "skill"
        shutil.copytree(HERE, skill, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        sources = {t: (skill / t).read_text(encoding="utf-8")
                   for t in ("pii_denylist.py", "driver.py") if (skill / t).is_file()}
        applied = sum(1 for old, _new in edits if any(old in s for s in sources.values()))
        if applied != len(edits):
            fails.append(f"mutant {name}: kotwica mutacji nie pasuje do kodu "
                         f"({applied}/{len(edits)}) — ratchet stracił kontakt ze źródłem")
            continue
        mutate(skill, edits)
        residual = oracle(skill, mdir / "work", verbose=False)
        if not residual:
            fails.append(f"mutant {name}: oracle POZOSTAŁ ZIELONY po osłabieniu polityki")
        else:
            print(f"  PASS mutant {name} → oracle czerwony ({len(residual)} fail)")
    return fails


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="blind-pii-oracle-") as tmp:
        work = Path(tmp)
        print("# negatywny oracle PII (wabiki syntetyczne)")
        fails = oracle(HERE, work / "base")
        if not fails:
            print("  PASS oracle: wszystkie wabiki odrzucone, kontrola czysta przeszła")
        print("# mutation ratchet")
        fails += mutation_ratchet(work / "mut")
        print("")
        if fails:
            print(f"PII-ORACLE FAILED ({len(fails)})")
            return 1
        print("PII-ORACLE OK")
        return 0


if __name__ == "__main__":
    sys.exit(main())
