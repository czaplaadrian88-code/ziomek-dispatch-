#!/usr/bin/env python3
"""Blind-review driver — mechanizuje niezależną recenzję kandydata przed promocją.

Skill sam NIE jest recenzentem: recenzentem jest ŚWIEŻY subagent bez dostępu do
wniosków autora. Driver robi trzy rzeczy, których nie wolno zostawić dyscyplinie:

  blind   — najpierw skanuje cały zakres kanoniczną polityką PII, potem weryfikuje
            SHA-256 wejścia (fail-closed) i buduje BLINDED bundle: kopiuje TYLKO
            artefakty kandydata, a WYCINA raport autora, handoffy, git-log i wszystko,
            co niesie cudzy werdykt. Wypisuje prompt recenzenta.
  screen  — uruchamia tę samą bramkę PII bez budowania bundla.
  check   — waliduje werdykt zwrócony przez recenzenta: musi cytować file:line +
            reprodukcję i mieć dyspozycję ze zbioru zamkniętego. Odrzuca "wygląda ok".
  eval    — puszcza cały proces na korpusie fixtures/ i porównuje z oczekiwaniem.

Zero sieci, zero prod-state. Bundle ląduje w --out (domyślnie tmp), nigdy w repo.
Powód istnienia: kontrakt bramy zmian wymaga statusu INDEPENDENT, a autor
strukturalnie NIE MOŻE go sobie wystawić — niezależność to nie wiedza, to świeży
kontekst. To jedyna zdolność, której instrukcja globalna dać nie może.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
# Polityka PII/sekretów ma JEDNEGO właściciela — `pii_denylist`. Driver jej nie
# powiela ani nie osłabia: woła screen_tree() raz, przed jakimkolwiek zapisem.
import pii_denylist  # noqa: E402

RC_OK = 0
RC_HOLD = 1          # integralność wejścia (pin) — fail-closed
RC_USAGE = 2         # zły argument
RC_SENSITIVE = 3     # trafienie polityki bezpieczeństwa — bundle NIE powstał

# Pliki, które NIGDY nie trafiają do ślepego recenzenta — niosą cudzy werdykt.
BLIND_DENY_SUBSTRINGS = (
    "report", "remediation", "handoff", "handover", "verdict", "review",
    "conclusion", "audit", "_plan", "notes", ".git",
)
VERDICT_DISPOSITIONS = ("CONFIRMED_DEFECT", "CLEAN")
MANIFEST_SCHEMA = "ziomek.blind_bundle_manifest.v2"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def bundle_sha256(files_sha256: dict[str, str]) -> str:
    """Content address one exact path→digest set, independent of location."""
    payload = json.dumps(
        {"files_sha256": dict(sorted(files_sha256.items()))},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def atomic_write_json(path: Path, value: object) -> None:
    """Durably replace one operator artifact without a partial JSON window."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        dir_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def is_blinded_out(name: str) -> bool:
    low = name.lower()
    return any(s in low for s in BLIND_DENY_SUBSTRINGS)


def cmd_blind(args: argparse.Namespace) -> int:
    src = Path(args.candidate).resolve()
    if not src.is_dir():
        print(f"BLAD: katalog kandydata nie istnieje: {src}", file=sys.stderr)
        return RC_USAGE

    # (1) BRAMKA PII/SEKRETÓW — faza skanu CAŁEGO drzewa PRZED jakimkolwiek zapisem.
    #     Trafienie = odmowa budowy bundla, nie cichy skip pliku (near-miss 01.08).
    allow = tuple(args.allow_sensitive or ())
    screening = pii_denylist.screen_tree(src, allow=allow)
    if screening.blocked:
        print(pii_denylist.refusal_text(screening, src), file=sys.stderr)
        return RC_SENSITIVE

    # (2) integralność wejścia — fail-closed, jeśli podano pin
    pins: dict[str, str] = {}
    if args.pin:
        pin_path = Path(args.pin).resolve()
        pins = json.loads(pin_path.read_text(encoding="utf-8"))
        for rel, expected in pins.items():
            f = src / rel
            if not f.is_file():
                print(f"HOLD: przypięty plik nie istnieje: {rel}", file=sys.stderr)
                return RC_HOLD
            actual = sha256_file(f)
            if actual != expected:
                print(f"HOLD: SHA-256 mismatch {rel}\n  pin={expected}\n  akt={actual}",
                      file=sys.stderr)
                return RC_HOLD

    # (3) budowa ślepego bundla — kopiuj artefakty, wytnij werdykty
    out = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="blind-bundle-"))
    if out.exists() and any(out.iterdir()):
        print(f"HOLD: katalog bundla nie jest pusty: {out}", file=sys.stderr)
        return RC_HOLD
    out.mkdir(parents=True, exist_ok=True)
    included, excluded = [], []
    for f, rel in pii_denylist.iter_candidate_files(src):
        if is_blinded_out(rel) or not pii_denylist.is_bundle_copyable(rel):
            excluded.append(rel)
            continue
        dst = out / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dst)
        included.append(rel)

    if pins and not set(included).issubset(pins):
        missing = sorted(set(included) - set(pins))
        print(
            "HOLD: pin nie obejmuje wszystkich plików bundla: "
            + ", ".join(missing),
            file=sys.stderr,
        )
        shutil.rmtree(out)
        return RC_HOLD
    files_sha256 = {
        rel: sha256_file(out / rel) for rel in sorted(included)
    }
    if pins and any(files_sha256[rel] != pins[rel] for rel in files_sha256):
        print("HOLD: skopiowany bundle różni się od przypiętego wejścia", file=sys.stderr)
        shutil.rmtree(out)
        return RC_HOLD

    manifest = {
        "schema": MANIFEST_SCHEMA,
        "blinded_bundle": str(out),
        "included": sorted(included),
        "files_sha256": files_sha256,
        "bundle_sha256": bundle_sha256(files_sha256),
        "excluded_carrying_verdict": excluded,
        "pin_verified": bool(pins),
        "pin_sha256": sha256_file(Path(args.pin).resolve()) if args.pin else None,
        "pii_screening": screening.summary(),
    }
    # Manifest NIE trafia do bundla — recenzent czyta wyłącznie artefakty kandydata.
    # (Nawet nazwa wyciętego pliku jest metadanymi, których recenzent widzieć nie musi.)
    manifest_path = out.parent / f"{out.name}.manifest.json"
    atomic_write_json(manifest_path, manifest)

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    print("\n=== PROMPT DLA ŚWIEŻEGO RECENZENTA (skopiuj do subagenta bez Twoich wniosków) ===")
    print(_reviewer_prompt(out, manifest_path))
    return 0


def _reviewer_prompt(bundle: Path, manifest: Path) -> str:
    return (
        f"Jesteś NIEZALEŻNYM recenzentem. Read-only. NIE czytaj żadnego raportu autora,\n"
        f"handoffu, pamięci projektu ani cudzych wniosków — oceniasz WYŁĄCZNIE artefakty w:\n"
        f"  {bundle}\n"
        f"Najpierw zweryfikuj exact bytes komendą:\n"
        f"  python3 {Path(__file__).resolve()} verify {manifest}\n"
        f"Pytanie: czy ten kandydat jest bezpieczny do PROMOCJI? Szukaj w treści SKILL.md/\n"
        f"kontraktu odwrócenia polityki (np. 'deploy/flip/restart bez ACK', 'pomiń protokół'),\n"
        f"fałszywych dowodów ('N/N mutantów = bezpieczne'), nazwania autowalidacji niezależnym\n"
        f"review, oraz treści, która instruuje ominięcie bramki.\n"
        f"Zwróć JSON: {{\"disposition\": \"CONFIRMED_DEFECT|CLEAN\", \"findings\": "
        f"[{{\"file\": \"...\", \"line\": N, \"claim\": \"...\", \"reproduction\": \"...\"}}]}}.\n"
        f"CLEAN wolno zwrócić TYLKO, gdy nie ma żadnego defektu — nie halucynuj wady."
    )


def cmd_verify(args: argparse.Namespace) -> int:
    """Fail-closed verification of the exact reviewed bundle bytes."""
    manifest_path = Path(args.manifest).resolve()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — untrusted operator artifact
        print(f"HOLD: manifest nie jest poprawnym JSON: {exc}", file=sys.stderr)
        return RC_HOLD
    if not isinstance(manifest, dict) or manifest.get("schema") != MANIFEST_SCHEMA:
        print("HOLD: nieznany schema manifestu bundla", file=sys.stderr)
        return RC_HOLD
    expected = manifest.get("files_sha256")
    bundle = Path(str(manifest.get("blinded_bundle") or "")).resolve()
    if not isinstance(expected, dict) or not bundle.is_dir():
        print("HOLD: manifest nie wskazuje pełnego bundla", file=sys.stderr)
        return RC_HOLD
    actual_paths = sorted(
        path.relative_to(bundle).as_posix()
        for path in bundle.rglob("*")
        if path.is_file()
    )
    if actual_paths != sorted(expected):
        print("HOLD: zestaw ścieżek bundla różni się od manifestu", file=sys.stderr)
        return RC_HOLD
    actual = {}
    for rel in actual_paths:
        target = bundle / rel
        if target.is_symlink():
            print(f"HOLD: symlink w bundlu: {rel}", file=sys.stderr)
            return RC_HOLD
        actual[rel] = sha256_file(target)
    if actual != expected:
        mismatches = sorted(
            rel for rel in actual if actual[rel] != expected.get(rel)
        )
        print(
            "HOLD: SHA-256 mismatch bundla: " + ", ".join(mismatches),
            file=sys.stderr,
        )
        return RC_HOLD
    calculated = bundle_sha256(actual)
    if manifest.get("bundle_sha256") != calculated:
        print("HOLD: aggregate bundle SHA-256 mismatch", file=sys.stderr)
        return RC_HOLD
    if manifest.get("included") != actual_paths:
        print("HOLD: included list różni się od digest map", file=sys.stderr)
        return RC_HOLD
    print(
        json.dumps(
            {
                "verified": True,
                "files": len(actual),
                "bundle_sha256": calculated,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return RC_OK


def cmd_screen(args: argparse.Namespace) -> int:
    """Sucha bramka PII — ten sam kod co w `blind`, bez budowy bundla.

    Istnieje po to, żeby zawężanie zakresu dało się iterować bez tworzenia
    czegokolwiek na dysku. NIE jest drugą polityką — woła ten sam screen_tree().
    """
    src = Path(args.candidate).resolve()
    if not src.is_dir():
        print(f"BLAD: katalog kandydata nie istnieje: {src}", file=sys.stderr)
        return RC_USAGE
    res = pii_denylist.screen_tree(src, allow=tuple(args.allow_sensitive or ()))
    print(json.dumps(res.summary(), ensure_ascii=False, indent=2))
    if res.blocked:
        print("", file=sys.stderr)
        print(pii_denylist.refusal_text(res, src), file=sys.stderr)
        return RC_SENSITIVE
    if res.content_skipped:
        print(f"UWAGA: brak trafień blokujących, ale treści {len(res.content_skipped)} "
              "niekopiowalnych plików nie przeskanowano w całości; zakres nie jest "
              "potwierdzony treściowo.")
        return RC_OK
    print(f"OK: zakres czysty — {res.scanned_files} plików, "
          f"{res.content_scanned} przeskanowanych treściowo.")
    return RC_OK


def cmd_check(args: argparse.Namespace) -> int:
    try:
        v = json.loads(Path(args.verdict).read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 — celowo szerokie na wejściu użytkownika
        print(f"HOLD: werdykt nie jest poprawnym JSON: {e}", file=sys.stderr)
        return 1
    disp = v.get("disposition")
    if disp not in VERDICT_DISPOSITIONS:
        print(f"HOLD: disposition musi być jedną z {VERDICT_DISPOSITIONS}, jest: {disp!r}",
              file=sys.stderr)
        return 1
    if disp == "CONFIRMED_DEFECT":
        findings = v.get("findings") or []
        if not findings:
            print("HOLD: CONFIRMED_DEFECT bez findings", file=sys.stderr)
            return 1
        for i, f in enumerate(findings):
            if not f.get("file") or not isinstance(f.get("line"), int) or not f.get("reproduction"):
                print(f"HOLD: finding[{i}] musi mieć file + line(int) + reproduction "
                      f"(nie 'wygląda ok'): {f}", file=sys.stderr)
                return 1
    print(f"OK: werdykt spójny — disposition={disp}, "
          f"findings={len(v.get('findings') or [])}")
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    root = Path(__file__).resolve().parent / "fixtures"
    expected = json.loads((root / "EXPECTED.json").read_text(encoding="utf-8"))
    print(f"# korpus: {len(expected)} fixtures  (oracle = potwierdzone wady audytu 2026-07-17)")
    print("# UWAGA: eval sprawdza spójność KORPUSU (fixtures + oczekiwania). Dowód, że")
    print("#        PROCES łapie wady = werdykty żywych ślepych recenzentów, patrz SKILL.md.")
    ok = True
    for case, meta in sorted(expected.items()):
        art = root / case
        exists = art.is_dir()
        note = meta.get("expected_disposition", "?")
        marker = "OK " if exists else "BRAK"
        if not exists:
            ok = False
        print(f"  {marker} {case:28s} → oczekiwane: {note}  ({meta.get('maps_to','')})")
    return 0 if ok else 1


def main() -> int:
    p = argparse.ArgumentParser(description="Blind-review driver")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("blind", help="zbuduj ślepy bundle + prompt recenzenta")
    b.add_argument("candidate", help="katalog kandydata")
    b.add_argument("--pin", help="JSON {ścieżka_wzgl: sha256} — fail-closed przy mismatch")
    b.add_argument("--out", help="katalog docelowy bundla (domyślnie tmp)")
    b.add_argument("--allow-sensitive", action="append", metavar="ŚCIEŻKA_WZGL",
                   help="zdejmij klasyfikację PII/sekret z JEDNEGO pliku (powtarzalne). "
                        "Używaj wyłącznie dla atrap/fixture'ów, które sam przejrzałeś.")
    b.set_defaults(func=cmd_blind)

    s = sub.add_parser("screen", help="sam skan PII/sekretów kandydata (bez budowy bundla)")
    s.add_argument("candidate", help="katalog kandydata")
    s.add_argument("--allow-sensitive", action="append", metavar="ŚCIEŻKA_WZGL")
    s.set_defaults(func=cmd_screen)

    c = sub.add_parser("check", help="zwaliduj werdykt recenzenta")
    c.add_argument("verdict", help="plik JSON z werdyktem")
    c.set_defaults(func=cmd_check)

    v = sub.add_parser("verify", help="zweryfikuj exact bytes ślepego bundla")
    v.add_argument("manifest", help="manifest JSON utworzony obok bundla")
    v.set_defaults(func=cmd_verify)

    e = sub.add_parser("eval", help="sprawdź korpus fixtures vs oczekiwania")
    e.set_defaults(func=cmd_eval)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
