#!/usr/bin/env python3
"""Driver skilla ziomek-cto — cykl pracy CTO nad Ziomkiem (dispatch_v2).

Podkomendy:
  scope <temat>     mapa kompletności planowanej zmiany (automatyzacja ETAP 3 #0):
                    klasyfikacja tematu → wszystkie miejsca klasy z rejestru
                    bliźniaków (references/twins-registry.json) → tabela
                    miejsce→weryfikacja-grep→[dotknięte? wypełnia sesja].
  dod <diff|ref>    mechaniczna checklista Definition-of-Done na diffie:
                    flaga ON≠OFF? metryka serializowana? parytet bliźniaków?
                    dowody (regresja/replay/rollback) w pliku --evidence?
                    exit 1 gdy cokolwiek FAIL. To brama na OCZYWISTĄ
                    niekompletność — NIE zastępuje pełnego DoD ani ślepej
                    recenzji (ziomek-blind-review).
  brief             rozpoznanie stanu na start sesji CTO: zdrowie usług/strażnika
                    DELEGOWANE do run-dispatch-v2 (zero własnej kopii) + delty
                    CTO: otwarte HOLD/P0 z todo_master, shadow-joby vs rejestr,
                    recon multi-sesji, wskaźnik priorytetów.
  handoff           szablon wpisu handoff (sprint_timeline + 1 linia MEMORY.md)
                    wypełniony faktami sesji; NICZEGO nie zapisuje (stdout).

KONTRAKT BEZPIECZEŃSTWA: całość READ-ONLY. Driver nie deployuje, nie flipuje
flag, nie restartuje usług, nie pisze do memory ani do żywego stanu. Wszystko,
co mutuje, idzie protokołem #0 (memory/ziomek-change-protocol.md) za ACK
właściciela — ODR-002: żaden skill nie nadaje execution authority.

Env (na potrzeby testów hermetycznych / kompozycji):
  ZIOMEK_CTO_REGISTRY    ścieżka rejestru bliźniaków (default references/twins-registry.json)
  ZIOMEK_CTO_RUN_DRIVER  ścieżka drivera run-dispatch-v2 (default ../run-dispatch-v2/driver.sh)
  ZIOMEK_CTO_TODO        ścieżka todo_master.md
  ZIOMEK_CTO_SHADOW_REG  ścieżka shadow-jobs-registry.md
  ZIOMEK_CTO_NO_LIVE=1   pomiń odczyty żywego hosta (atq/systemctl/tmux/git) — do testów
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]  # .../dispatch_v2
REG_PATH = Path(os.environ.get("ZIOMEK_CTO_REGISTRY", HERE / "references" / "twins-registry.json"))
RUN_DRIVER = Path(os.environ.get("ZIOMEK_CTO_RUN_DRIVER", HERE.parent / "run-dispatch-v2" / "driver.sh"))
TODO_PATH = Path(os.environ.get("ZIOMEK_CTO_TODO", "/root/.claude/projects/-root/memory/todo_master.md"))
SHADOW_REG = Path(os.environ.get("ZIOMEK_CTO_SHADOW_REG", "/root/.claude/projects/-root/memory/shadow-jobs-registry.md"))
NO_LIVE = os.environ.get("ZIOMEK_CTO_NO_LIVE") == "1"
PROTOKOL = "/root/.claude/projects/-root/memory/ziomek-change-protocol.md"

_DIA = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")


def _norm(s: str) -> str:
    return s.translate(_DIA).casefold()


def _die(msg: str, code: int = 2) -> None:
    print(f"BLAD: {msg}", file=sys.stderr)
    sys.exit(code)


def _load_registry() -> dict:
    # Fail-closed: uszkodzony/nieobecny rejestr = STOP, nie pusta mapa
    # (pusta mapa kompletności wygląda jak "nic do sprawdzenia" = kłamiący przyrząd, C9).
    if not REG_PATH.is_file():
        _die(f"brak rejestru bliźniaków: {REG_PATH}")
    try:
        reg = json.loads(REG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        _die(f"rejestr bliźniaków nieczytelny ({REG_PATH}): {e}")
    if not isinstance(reg.get("klasy"), dict) or not reg["klasy"]:
        _die(f"rejestr bliźniaków bez sekcji 'klasy': {REG_PATH}")
    return reg


def _resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else (REPO / p)


def _verify_place(place: dict) -> str:
    fp = _resolve(place["plik"])
    if not fp.exists():
        return "POZA-HOSTEM/BRAK — sprawdź ręcznie" if Path(place["plik"]).is_absolute() else "BRAK-PLIKU (DRYF mapy!)"
    if fp.is_dir():
        return "KATALOG-OK"
    sym = place.get("symbol")
    if not sym:
        return "PLIK-OK (bez symbolu)"
    try:
        n = fp.read_text(encoding="utf-8", errors="replace").count(sym)
    except OSError:
        return "NIECZYTELNY"
    return f"OK (×{n})" if n else "DRYF-SYMBOLU (kod się przeniósł?)"


# ---------------------------------------------------------------- scope

def cmd_scope(args: argparse.Namespace) -> int:
    reg = _load_registry()
    klasy = reg["klasy"]
    temat = " ".join(args.temat).strip()
    if args.klasa:
        if args.klasa not in klasy:
            _die(f"nieznana klasa '{args.klasa}'; dostępne: {', '.join(sorted(klasy))}", 3)
        matched = [args.klasa]
    else:
        if not temat:
            _die("podaj temat zmiany, np.: driver.py scope \"równe traktowanie no-GPS\"", 3)
        tn = _norm(temat)
        matched = [k for k, v in klasy.items() if any(_norm(kw) in tn for kw in v.get("keywords", []))]
    if not matched:
        print(f"# scope: temat '{temat}' nie pasuje do żadnej klasy rejestru.")
        print("# To NIE znaczy 'brak bliźniaków' — to znaczy: sklasyfikuj ręcznie (ETAP 3 protokołu #0).")
        print("# Dostępne klasy:")
        for k, v in sorted(klasy.items()):
            print(f"#   --klasa {k:<28} {v.get('opis','')[:90]}")
        print("# Klasa nieobjęta rejestrem? → dopisz ją do references/twins-registry.json (dane, nie kod)")
        return 3

    print(f"# MAPA KOMPLETNOŚCI (ETAP 3 protokołu #0) — temat: {temat or args.klasa}")
    print(f"# rejestr: {REG_PATH} (wersja {reg.get('_meta', {}).get('wersja', '?')}, seed {reg.get('_meta', {}).get('data_seed', '?')})")
    dryf = 0
    for k in matched:
        v = klasy[k]
        print(f"\n== KLASA: {k} [{v.get('typ','?')}] ==")
        print(f"   {v.get('opis','')}")
        if v.get("przypomnienie"):
            print(f"   ⚠ {v['przypomnienie']}")
        print(f"   {'LP':<3} {'MIEJSCE':<52} {'WERYFIKACJA (żywy grep)':<34} DOTKNIĘTE?")
        for i, place in enumerate(v.get("miejsca", []), 1):
            st = _verify_place(place)
            if "DRYF" in st or "BRAK-PLIKU" in st:
                dryf += 1
            print(f"   {i:<3} {place['nazwa']:<52} {st:<34} [ TAK / N-D + powód ]")
            print(f"       plik: {place['plik']}" + (f"  symbol: {place['symbol']}" if place.get("symbol") else ""))
            if place.get("uwaga"):
                print(f"       {place['uwaga']}")
    print("\n# DoD mapy: KAŻDE miejsce dotknięte albo jawne N-D+powód. Bliźniacze ścieżki RAZEM.")
    print("# Nic 'na wszelki wypadek'. Zmiana częściowa = NIEZAKOŃCZONA.")
    print(f"# Pełny protokół: {PROTOKOL} (ETAP 0→7) + ZIOMEK_ARCHITECTURE.md §4.")
    if dryf:
        print(f"# ⚠ DRYF mapy: {dryf} pozycji nie znalezionych grepem — popraw rejestr (kod się przeniósł); "
              f"NIE ufaj tej tabeli w ciemno.")
    return 0


# ---------------------------------------------------------------- dod

def _read_diff(target: str) -> str:
    p = Path(target)
    if p.is_file():
        try:
            return p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            _die(f"nie mogę odczytać diffa {p}: {e}")
    if NO_LIVE:
        _die(f"'{target}' nie jest plikiem, a ZIOMEK_CTO_NO_LIVE=1 blokuje git", 2)
    r = subprocess.run(["git", "-C", str(REPO), "diff", f"master...{target}"],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0 or not r.stdout.strip():
        _die(f"'{target}' to ani plik diff, ani ref gitowy z niepustym diffem vs master "
             f"(git: rc={r.returncode} {r.stderr.strip()[:200]})")
    return r.stdout


def _parse_diff(text: str) -> dict[str, dict[str, list[str]]]:
    files: dict[str, dict[str, list[str]]] = {}
    cur: str | None = None
    last_minus: str | None = None
    for line in text.splitlines():
        if line.startswith("--- "):
            p = line[4:].strip()
            last_minus = None if p == "/dev/null" else (p[2:] if p.startswith("a/") else p)
        elif line.startswith("+++ "):
            p = line[4:].strip()
            if p == "/dev/null":
                cur = last_minus  # plik usuwany — rejestrujemy po starej ścieżce
            else:
                cur = p[2:] if p.startswith("b/") else p
            if cur:
                files.setdefault(cur, {"added": [], "removed": []})
        elif cur and line.startswith("+") and not line.startswith("+++"):
            files[cur]["added"].append(line[1:])
        elif cur and line.startswith("-") and not line.startswith("---"):
            files[cur]["removed"].append(line[1:])
    return files


def _is_test_path(path: str) -> bool:
    return path.startswith("tests/") or "/tests/" in path


def _is_engine_path(path: str) -> bool:
    if _is_test_path(path) or path.endswith(".md"):
        return False
    for pre in ("docs/", "eod_drafts/", ".claude/", "memory/"):
        if path.startswith(pre):
            return False
    return True


def _grep_repo_tests(needle: str) -> bool:
    tdir = REPO / "tests"
    if not tdir.is_dir():
        return False
    try:
        r = subprocess.run(["grep", "-rlF", "--include=*.py", needle, str(tdir)],
                           capture_output=True, text=True, timeout=30)
        return bool(r.stdout.strip())
    except Exception:
        return False


def _grep_files(needle: str, rel_paths: list[str]) -> bool:
    for rp in rel_paths:
        fp = _resolve(rp)
        try:
            if fp.is_file() and needle in fp.read_text(encoding="utf-8", errors="replace"):
                return True
        except OSError:
            continue
    return False


def cmd_dod(args: argparse.Namespace) -> int:
    reg = _load_registry()
    diff_text = _read_diff(args.cel)
    files = _parse_diff(diff_text)
    if not files:
        _die("diff pusty albo nieparsowalny (oczekuję unified diff)")
    evidence = ""
    if args.evidence:
        ep = Path(args.evidence)
        if not ep.is_file():
            _die(f"plik dowodów nie istnieje: {ep}")
        evidence = ep.read_text(encoding="utf-8", errors="replace")
    ev: dict[str, str] = {}
    for line in evidence.splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            k, _, val = line.partition(":")
            ev[_norm(k.strip())] = val.strip()

    added_engine = [(p, l) for p, d in files.items() if _is_engine_path(p) for l in d["added"]]
    added_tests = [l for p, d in files.items() if _is_test_path(p) for l in d["added"]]
    engine_changed = any(_is_engine_path(p) for p in files)
    py_changed = any(p.endswith(".py") for p in files)
    # N-D liczy się PER PLIK: linia z markerem N-D musi wymieniać dany plik (basename
    # wystarczy). Goły token "N-D" bez plików NIE wyłącza parytetu bliźniaków
    # (obserwacja ślepej recenzji 17.07: jeden token gasił wszystkie FAIL-e).
    nd_lines = [l for l in (diff_text + "\n" + evidence).splitlines()
                if re.search(r"\bN-?D\b", l)]

    def _nd_covers(path: str) -> bool:
        base = Path(path).name
        return any(base in l or path in l for l in nd_lines)

    rows: list[tuple[str, str, str]] = []  # (check, PASS/FAIL/N-D, detal)

    # R1+R2: nowe/dotykane flagi decyzyjne
    flags = sorted({m for _, l in added_engine for m in re.findall(r"ENABLE_[A-Z0-9_]{3,}", l)})
    if not flags:
        rows.append(("flaga: test ON≠OFF", "N-D", "diff nie dodaje/nie dotyka linii z ENABLE_* w silniku"))
        rows.append(("flaga: w rejestrze decyzyjnym", "N-D", "j.w."))
    else:
        for fl in flags:
            has_test = any(fl in l for l in added_tests) or _grep_repo_tests(fl)
            rows.append((f"flaga {fl}: test ON≠OFF", "PASS" if has_test else "FAIL",
                         "test w diffie/istniejącej suicie" if has_test
                         else "flaga zmienia decyzję → MUSI istnieć test ON≠OFF (ETAP 4 #0)"))
            reg_files = ["common.py", "../flags.json", "tools/flag_lifecycle_registry.json"]
            in_reg = (any(fl in l for p, d in files.items() if p in ("common.py", "flags.json", "tools/flag_lifecycle_registry.json") for l in d["added"])
                      or _grep_files(fl, reg_files))
            rows.append((f"flaga {fl}: w rejestrze (ETAP4/flags.json/lifecycle)", "PASS" if in_reg else "FAIL",
                         "znaleziona" if in_reg else "flaga-widmo poza rejestrem = wzorzec #1/#9 i leak w conftest"))

    # R3: nowe metryki serializowane
    mkeys = sorted({m for _, l in added_engine for m in re.findall(r"metrics\[\s*[\"']([A-Za-z0-9_]+)", l)})
    if not mkeys:
        rows.append(("metryka: w shadow_decisions.jsonl", "N-D", "diff nie dodaje kluczy metrics[...]"))
    else:
        for mk in mkeys:
            seen = (any(mk in l for l in added_tests) or _grep_repo_tests(mk)
                    or any("_METRICS_EXCLUDE" in l and mk in l for _, l in added_engine))
            rows.append((f"metryka {mk}: test obecności w jsonl / jawne wykluczenie", "PASS" if seen else "FAIL",
                         "jest" if seen else "metryka liczona ≠ serializowana (wzorzec #4/#16) — test grep -c albo _METRICS_EXCLUDE z powodem"))

    # R4: parytet bliźniaków (tylko klasy plikowe)
    changed_paths = set(files)
    for kname, klass in reg["klasy"].items():
        if klass.get("typ") != "blizniaki-plikowe":
            continue
        places = [pl for pl in klass.get("miejsca", []) if not Path(pl["plik"]).is_absolute()]
        touched = {pl["plik"] for pl in places if pl["plik"] in changed_paths}
        if not touched:
            continue
        untouched = [pl for pl in places if pl["plik"] not in changed_paths
                     and pl["plik"] not in touched and not pl["plik"].startswith("tests/")]
        # bliźniak może dzielić plik (np. 2 miejsca w dispatch_pipeline.py) — liczymy per plik
        untouched_files = sorted({pl["plik"] for pl in untouched})
        uncovered = [f for f in untouched_files if not _nd_covers(f)]
        if uncovered:
            rows.append((f"bliźniaki [{kname}]", "FAIL",
                         f"dotknięto {sorted(touched)}, NIE dotknięto {uncovered} bez linii 'N-D: <plik> — powód' — wzorzec #2 (fix w 1 z N)"))
        else:
            det = ("wszystkie miejsca klasy dotknięte" if not untouched_files
                   else f"niedotknięte {untouched_files} objęte jawnym N-D per plik")
            rows.append((f"bliźniaki [{kname}]", "PASS", det))

    # R5-R8: dowody (evidence)
    def _ev_row(name: str, keys: list[str], wymagane: bool, brak_detal: str) -> None:
        val = next((ev[k] for k in keys if k in ev and ev[k]), "")
        if val:
            rows.append((name, "PASS", val[:100]))
        elif wymagane:
            rows.append((name, "FAIL", brak_detal))
        else:
            rows.append((name, "N-D", "zmiana nie dotyka silnika/testów — mimo to zalecane"))

    reg_val = next((ev[k] for k in ("regresja", "regression") if k in ev), "")
    if reg_val and re.search(r"\b0 fail|failed[ =:]*0", reg_val):
        rows.append(("pełna regresja vs baseline", "PASS", reg_val[:100]))
    elif reg_val:
        rows.append(("pełna regresja vs baseline", "FAIL", f"dowód nie pokazuje 0 failed: '{reg_val[:80]}'"))
    elif py_changed:
        rows.append(("pełna regresja vs baseline", "FAIL",
                     "brak dowodu (evidence 'regresja: N passed, 0 failed'); pełna suita OBOWIĄZKOWA (ETAP 4 #0)"))
    else:
        rows.append(("pełna regresja vs baseline", "N-D", "zmiana bez .py — mimo to zalecana"))

    _ev_row("e2e przez dotknięte warstwy", ["e2e"], engine_changed,
            "brak dowodu e2e (assess_order/replay przez warstwy, nie tylko unit klastra)")
    _ev_row("dowód POZYTYWNEGO wpływu ON↔OFF", ["pozytywny-wplyw", "replay", "bajt-identycznosc"], engine_changed,
            "brak dowodu (replay ON↔OFF metryka lepsza / refaktor: bajt-identyczność) — ETAP 5 #0")
    _ev_row("rollback gotowy", ["rollback"], engine_changed,
            "brak planu rollbacku (flaga=false / .bak / git revert) — ETAP 7 #0")

    print(f"# DoD mechaniczny — cel: {args.cel}  (plików w diffie: {len(files)}, "
          f"silnik dotknięty: {'TAK' if engine_changed else 'NIE'})")
    fails = 0
    for name, st, det in rows:
        if st == "FAIL":
            fails += 1
        print(f"  {st:<5} {name}")
        if det:
            print(f"        {det}")
    if fails:
        print(f"\nWYNIK: FAIL ({fails}) — zmiana częściowa = NIEZAKOŃCZONA (protokół #0).")
        return 1
    print("\nWYNIK: PASS mechaniczny. To brama na oczywistą niekompletność — pełny DoD = "
          "ZIOMEK_DEFINITION_OF_DONE.md (7 ptaszków + anty-entropia), a niezależną ocenę robi "
          "ziomek-blind-review (świeży recenzent, nie autor).")
    return 0


# ---------------------------------------------------------------- brief

def _run(cmd: list[str], timeout: int = 180) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + (("\n" + r.stderr) if r.stderr.strip() else "")).strip()
    except FileNotFoundError:
        return 127, f"(brak komendy: {cmd[0]})"
    except subprocess.TimeoutExpired:
        return 124, f"(timeout {timeout}s: {' '.join(cmd)})"


def _indent(text: str, pre: str = "    ", limit: int = 400) -> str:
    lines = text.splitlines()
    out = [pre + l for l in lines[:limit]]
    if len(lines) > limit:
        out.append(pre + f"... (ucięte, {len(lines) - limit} linii więcej)")
    return "\n".join(out)


def cmd_brief(args: argparse.Namespace) -> int:
    delegation_failed = False
    print("# ===== BRIEF CTO — rozpoznanie stanu (read-only) =====")
    print(f"# {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")

    print("\n== 1. ZDROWIE USŁUG + STRAŻNIK + PRZECIEKI — delegacja do run-dispatch-v2 ==")
    if RUN_DRIVER.is_file():
        rc, out = _run(["bash", str(RUN_DRIVER), "health"], timeout=240)
        print(f"  $ {RUN_DRIVER} health  (rc={rc})")
        print(_indent(out))
        if rc == 127:
            delegation_failed = True
    else:
        delegation_failed = True
        print(f"  BLAD: brak drivera run-dispatch-v2 pod {RUN_DRIVER} — zdrowia NIE reimplementuję "
              f"(celowo; napraw instalację skilla run-dispatch-v2)")

    print("\n== 2. OTWARTE HOLD / P0 / CURRENT (todo_master.md — JEDEN punkt wejścia zadań) ==")
    if TODO_PATH.is_file():
        try:
            open_rows = []
            for ln, line in enumerate(TODO_PATH.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if not line.startswith("## "):
                    continue
                if any(m in line for m in ("✅", "⚪")):
                    continue
                if any(m in line for m in ("🔴", "🟡", "🟠", "🔵")) or "P0" in line or "HOLD" in line or "CURRENT" in line:
                    open_rows.append((ln, line[3:].strip()))
            print(f"  otwartych nagłówków: {len(open_rows)} (plik: {TODO_PATH})")
            for ln, t in open_rows[:20]:
                print(f"    l.{ln:<5} {t[:110]}")
            if len(open_rows) > 20:
                print(f"    ... (+{len(open_rows) - 20} dalszych — czytaj plik)")
        except OSError as e:
            print(f"  BLAD odczytu {TODO_PATH}: {e}")
    else:
        print(f"  BLAD: brak {TODO_PATH}")

    print("\n== 3. SHADOW-JOBY / AT-REVIEW vs REJESTR (rekoncyliacja = część ETAP 0) ==")
    if NO_LIVE:
        print("  [pominięte: ZIOMEK_CTO_NO_LIVE=1]")
    else:
        rc, out = _run(["atq"])
        print(f"  atq (rc={rc}):")
        print(_indent(out or "(pusto)", "    ", 30))
        rc, out = _run(["bash", "-c", "systemctl list-timers --all 2>/dev/null | grep -iE 'review|verdict' || true"])
        print("  timery review/verdict:")
        print(_indent(out or "(brak)", "    ", 30))
    if SHADOW_REG.is_file():
        mtime = datetime.fromtimestamp(SHADOW_REG.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        print(f"  rejestr: {SHADOW_REG} (ostatnia zmiana {mtime})")
        print("  → zrekoncyliuj wg dyscypliny rejestru: at-job nieobecny w atq = ODPALONY → odczytaj werdykt")
        print("    → wpisz do notatki tematycznej → oznacz DONE. Werdykt ufny TYLKO po oracle-case (C9/C10).")
    else:
        print(f"  BLAD: brak rejestru {SHADOW_REG}")

    print("\n== 4. RECON MULTI-SESJI (C1: wspólne repo+deploy — UZGODNIJ, nie nadpisuj) ==")
    if NO_LIVE:
        print("  [pominięte: ZIOMEK_CTO_NO_LIVE=1]")
    else:
        rc, out = _run(["git", "-C", str(REPO), "log", "--oneline", "-10"])
        print("  git log -10:")
        print(_indent(out, "    ", 12))
        rc, out = _run(["git", "-C", str(REPO), "status", "--short"])
        print("  git status --short (cudzy WIP? NIE commituj tego, jawny pathspec!):")
        print(_indent(out or "(czysto)", "    ", 15))
        rc, out = _run(["tmux", "ls"])
        print("  tmux ls (inne sesje):")
        print(_indent(out or "(brak)", "    ", 15))
        rc, out = _run(["bash", "-c",
                        f"find {REPO} -name '*.bak*' -newermt '-48 hours' -not -path '*/.git/*' 2>/dev/null | head -10"])
        print("  świeże .bak (48h — czyjś deploy w toku?):")
        print(_indent(out or "(brak)", "    ", 12))

    print("\n== 5. PRIORYTETY (skąd brać zadanie) ==")
    print("  kolejka P1→P6 (stabilność·jakość·skala; Adrian 03.07): "
          "memory/priorytety-stabilnosc-jakosc-skala-2026-07-03.md")
    print("  ⚠ nowe tematy NIE wyprzedzają kolejki bez jawnej decyzji Adriana; "
          "otwarte: czy ODR-002 zawiesza P1→P6 (sprawdź MEMORY.md/todo_master, czy już rozstrzygnięte)")
    print("  konflikt reguł → memory/ZIOMEK_REGULY_KANON.md (tabela §1); intencja właściciela → ODR-001/ODR-002")

    print("\n# Brief niczego nie orzeka (C10): liczby czytaj z przyrządów PO ich kalibracji, "
          "statusy z dokumentów traktuj jako hipotezy z chwili T (C15).")
    return 4 if delegation_failed else 0


# ---------------------------------------------------------------- handoff

def cmd_handoff(args: argparse.Namespace) -> int:
    now = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        waw = now.astimezone(ZoneInfo("Europe/Warsaw")).strftime("%Y-%m-%d %H:%M")
    except Exception:
        waw = "?"
    head = branch = "?"
    dirty = "?"
    if not NO_LIVE:
        _, head = _run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"])
        _, branch = _run(["git", "-C", str(REPO), "branch", "--show-current"])
        _, st = _run(["git", "-C", str(REPO), "status", "--short"])
        dirty = str(len([l for l in st.splitlines() if l.strip()]))
    t = lambda v, ph: v if v else ph  # noqa: E731

    print("# ===== HANDOFF (szablon — driver NICZEGO nie zapisuje; wklej ręcznie) =====")
    print(f"# fakty: {now.strftime('%Y-%m-%d %H:%M UTC')} ({waw} Warsaw) | branch {branch} | HEAD {head} | working-tree: {dirty} plików nie-czystych")
    print("\n--- BLOK 1: sprint_timeline.md → sekcja CURRENT HANDOFF (na górze) ---")
    print(f"## HANDOFF {now.strftime('%Y-%m-%d')} — {t(args.temat, '{TEMAT — co robiła sesja}')}")
    print(f"- ZROBIONE: {t(args.wynik, '{WYNIK z dowodami: commit(y), liczby testów, metryki — fakty nie deklaracje}')}")
    print(f"- TESTY: {t(args.testy, '{np. pełna regresja N passed / 0 failed vs baseline M (kanoniczna ścieżka)}')}")
    print(f"- ROLLBACK: {t(args.rollback, '{jak cofnąć: flaga=false / git revert <sha> / rm -rf <dir>}')}")
    print("- OTWARTE: {co zostało + kto ma decyzję (ACK Adriana? werdykt at-jobu? okno 2 dni?)}")
    print("- NASTĘPNY KROK: {pierwsza czynność następnej sesji}")
    print("\n--- BLOK 2: MEMORY.md → 1 linia (meta-zasada: ≤~200 znaków, link do notatki) ---")
    linia = args.memory_line or (f"- **[{t(args.temat, '{TEMAT}')} ({now.strftime('%d.%m')})](plik-notatki.md)** — "
                                 f"{t(args.wynik, '{1-zdaniowy wynik+dowód}')}")
    print(linia)
    n = len(linia)
    print(f"    ↳ długość: {n} znaków" + (" ⚠ PRZYTNIJ (>200: łamie meta-zasadę MEMORY.md)" if n > 200 else " (OK ≤200)"))
    print("\n--- CHECKLISTA DOMKNIĘCIA (zanim zamkniesz sesję) ---")
    print("  [ ] todo_master.md — status zadania zaktualizowany (skończone → odhacz)")
    print("  [ ] luka znaleziona w SAMYM protokole #0? → DOPISZ do ziomek-change-protocol.md "
          "(protokół żywy; sesja bez wpisu = NIEZAKOŃCZONA)")
    print("  [ ] ustawiłeś at/timer review? → wpis do shadow-jobs-registry.md (inaczej job 'zniknie w tle')")
    print("  [ ] cudzy WIP nietknięty; commit był z jawnym pathspec (C1-git); git show HEAD --stat sprawdzone")
    print("  [ ] deploy/flip/restart — TYLKO jeśli był ACK; wpis co dokładnie zrestartowano")
    return 0


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(prog="driver.py", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("scope", help="mapa kompletności dla planowanej zmiany (ETAP 3 #0)")
    sp.add_argument("temat", nargs="*", help="temat zmiany prostym językiem")
    sp.add_argument("--klasa", help="wymuś klasę rejestru zamiast klasyfikacji po słowach")
    sp.set_defaults(fn=cmd_scope)

    dp = sub.add_parser("dod", help="mechaniczna checklista DoD na diffie/branchu (exit 1 przy FAIL)")
    dp.add_argument("cel", help="ścieżka do pliku .diff ALBO ref gitowy (diff liczony master...ref)")
    dp.add_argument("--evidence", help="plik dowodów: linie 'regresja:/e2e:/pozytywny-wplyw:/rollback:/N-D:'")
    dp.set_defaults(fn=cmd_dod)

    bp = sub.add_parser("brief", help="rozpoznanie stanu na start sesji CTO (deleguje zdrowie do run-dispatch-v2)")
    bp.set_defaults(fn=cmd_brief)

    hp = sub.add_parser("handoff", help="szablon wpisu handoff wypełniony faktami sesji (stdout, zero zapisu)")
    hp.add_argument("--temat")
    hp.add_argument("--wynik")
    hp.add_argument("--testy")
    hp.add_argument("--rollback")
    hp.add_argument("--memory-line", dest="memory_line")
    hp.set_defaults(fn=cmd_handoff)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
