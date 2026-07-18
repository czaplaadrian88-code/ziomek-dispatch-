#!/usr/bin/env python3
"""flag_registry — inwentarz flag z prowieniencją (F3, audyt 03.06 „effective_flags").

Flagi żyją w 3 miejscach i łatwo o rozjazd (incydent ETAP4: czasówka liczyła
innym silnikiem niż shadow przez env-only flagi):
  1. common.py — definicja + default (env-overridable przy imporcie),
  2. env unitów systemd (dispatch-*.service + drop-iny *.conf) — wartość
     ZAMROŻONA per proces przy starcie,
  3. flags.json — kanon hot-reload dla flag decyzyjnych (ETAP4_DECISION_FLAGS
     + FLAGS_JSON_NUMERIC_OVERRIDES czytane przez decision_flag/load_flags).

Tool READ-ONLY: skanuje wszystkie trzy źródła i wypisuje per flaga efektywną
wartość per proces + prowieniencję + WYKRYTE ROZJAZDY:
  - env-frozen flaga ustawiona w CZĘŚCI unitów silnika (cross-proces divergence),
  - klucz flags.json przykrywający env (env martwy dla flag decyzyjnych),
  - klucz flags.json bez definicji w common.py (literówka / sierota).

Użycie:
  python3 -m dispatch_v2.tools.flag_registry            # tabela rozjazdów + statystyki
  python3 -m dispatch_v2.tools.flag_registry --all      # pełny inwentarz
  python3 -m dispatch_v2.tools.flag_registry --md PLIK  # raport markdown
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
DISPATCH_V2 = os.path.dirname(_HERE)
COMMON_PY = os.path.join(DISPATCH_V2, "common.py")
FLAGS_JSON = "/root/.openclaw/workspace/scripts/flags.json"
SYSTEMD_DIR = "/etc/systemd/system"

# Unity liczące silnikiem dispatch_v2 (cross-proces spójność = wymóg ETAP4).
ENGINE_UNITS = (
    "dispatch-shadow.service",
    "dispatch-panel-watcher.service",
    "dispatch-czasowka.service",
    "dispatch-plan-recheck.service",
    "dispatch-telegram.service",
)

_DEF_RE = re.compile(
    r'^(?P<name>[A-Z][A-Z0-9_]*)\s*=\s*(?:float\(|int\()?(?:_os|os)\.environ\.get\(\s*'
    r'"(?P<env>[A-Z0-9_]+)"\s*,\s*"(?P<default>[^"]*)"\s*\)\s*\)?'
    r'(?P<cmp>\s*==\s*"1")?', re.M)


def scan_common(path: str = COMMON_PY) -> dict:
    """Definicje env-overridable z common.py: {nazwa: {default, bool}}.
    Łapie `(_os|os).environ.get("NAME", "default")` z opcjonalnym `int(`/`float(`
    i sufiksem `== "1"` (boolowska). NIE łapie flag definiowanych LITERAŁEM
    (`ENABLE_X = False`, `CAP = 6`) — te uzupełnia scan_literal_defaults dla
    ZNANYCH flag decyzyjnych/numerycznych (inaczej wciągnęlibyśmy setki stałych)."""
    out = {}
    src = open(path, encoding="utf-8").read()
    for m in _DEF_RE.finditer(src):
        name = m.group("name")
        raw = m.group("default")
        is_bool = bool(m.group("cmp"))
        out[name] = {
            "default": (raw == "1") if is_bool else raw,
            "bool": is_bool,
        }
    return out


_LITERAL_DEF_RE = r"^{name}\s*=\s*(True|False|-?\d+(?:\.\d+)?)\s*(?:#.*)?$"


def scan_literal_defaults(names, path: str = COMMON_PY) -> dict:
    """Domyślne wartości flag definiowanych LITERAŁEM top-level w common.py
    (`ENABLE_O2_CAPZ_RESEQ = False`, `AUTO_ASSIGN_MAX_PER_HOUR = 6`). Ograniczone
    do PODANYCH nazw (znane flagi decyzyjne/numeryczne z ETAP4) — nie skanuje
    dowolnych stałych. {nazwa: {default, bool}} jak scan_common."""
    src = open(path, encoding="utf-8").read()
    out = {}
    for n in names:
        m = re.search(_LITERAL_DEF_RE.format(name=re.escape(n)), src, re.M)
        if not m:
            continue
        raw = m.group(1)
        if raw in ("True", "False"):
            out[n] = {"default": raw == "True", "bool": True}
        else:
            out[n] = {"default": float(raw) if "." in raw else int(raw), "bool": False}
    return out


def _extract_paren_body(src: str, varname: str) -> str:
    """Zawartość krotki `varname = ( … )` przez BALANS nawiasów, ignorując
    komentarze `#…` i literały stringów. Naiwne `\\((.*?)\\)` ucinało krotkę na
    pierwszym `)` w komentarzu (ETAP4 ma dziesiątki komentarzy z `)`), gubiąc
    79/102 flagi decyzyjne — patrz test_scan_decision_lists_balanced."""
    m = re.search(varname + r"\s*=\s*\(", src)
    if not m:
        return ""
    i = m.end()  # tuż po '('
    depth = 1
    n = len(src)
    quote = None  # '\'' albo '"' gdy w stringu
    start = i
    while i < n and depth > 0:
        c = src[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c == "#":  # komentarz do końca linii
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        elif c in "'\"":
            quote = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return src[start:i]
        i += 1
    return src[start:i]


def scan_decision_lists(path: str = COMMON_PY) -> tuple:
    """ETAP4_DECISION_FLAGS + FLAGS_JSON_NUMERIC_OVERRIDES bez importu modułu
    (tool ma działać też na drzewie bez venv silnika)."""
    src = open(path, encoding="utf-8").read()

    def _tuple_items(varname):
        body = _extract_paren_body(src, varname)
        body = re.sub(r"#[^\n]*", "", body)  # usuń komentarze (bywa `== "1"`, prose)
        # Nazwy flag: UPPER, zaczynają się literą/`_` (nie cyfrą — filtruje `"1"`).
        return tuple(re.findall(r'"([A-Z_][A-Z0-9_]*)"', body))

    return (_tuple_items("ETAP4_DECISION_FLAGS")
            + _tuple_items("_FINGERPRINT_EXTRA_FLAGS"),
            _tuple_items("FLAGS_JSON_NUMERIC_OVERRIDES"))


def scan_unit_env(unit: str, systemd_dir: str | None = None) -> dict:
    """Environment= z main unitu + wszystkich drop-inów *.conf (jak systemd:
    ostatnia definicja wygrywa, drop-iny po main unicie)."""
    systemd_dir = systemd_dir or SYSTEMD_DIR
    env = {}
    files = []
    main = os.path.join(systemd_dir, unit)
    if os.path.exists(main):
        files.append(main)
    files += sorted(glob.glob(os.path.join(systemd_dir, unit + ".d", "*.conf")))
    for f in files:
        try:
            for line in open(f, encoding="utf-8", errors="replace"):
                line = line.strip()
                if not line.startswith("Environment="):
                    continue
                body = line[len("Environment="):].strip().strip('"')
                if "=" in body:
                    k, v = body.split("=", 1)
                    env[k.strip()] = (v.strip(), os.path.basename(f))
        except OSError:
            continue
    return env


def load_flags_json(path: str | None = None) -> dict:
    # path=None → czyta MODUŁOWY FLAGS_JSON PRZY WYWOŁANIU (nie default z def-time),
    # by monkeypatch FLAGS_JSON działał w testach (pułapka domyślnego argumentu).
    path = path or FLAGS_JSON
    try:
        return {k: v for k, v in json.load(open(path)).items()
                if not k.startswith("_comment")}
    except Exception:
        return {}


# Rodziny kluczy budowanych DYNAMICZNIE (f-string) — token-scan ich nie widzi.
# Przykład: evaluator.py f"CZASOWKA_T{trigger_min}_ENABLED".
DYNAMIC_KEY_FAMILIES = (re.compile(r"^CZASOWKA_T\d+_ENABLED$"),)

# Flagi celowo per-proces (kategorie (b) telemetria / (c) per-proces z ETAP4;
# pełna tabela + ACK: eod_drafts/2026-06-10/flag_inventory_etap4.md).
INTENTIONAL_PER_PROCESS = {
    "ENABLE_PANEL_BG_REFRESH",  # shadow=1 / watcher=0 ZAMIERZONE (własny cykl loginu)
    "ENABLE_LGBM_SHADOW", "ENABLE_LGBM_METRICS_READ", "ENABLE_PENDING_POOL",
    "ENABLE_OBJ_REPLAY_CAPTURE", "ENABLE_LOADAWARE_SELECTION_SHADOW",
    "PYTHONPATH",  # infra, nie flaga
}

# ── KURATOROWANA WARSTWA (L0.1, 2026-07-02) ────────────────────────────────
# Base-metadane (źródło/default/decyzyjność) auto-skanuje build_registry; ta
# warstwa dokłada to, czego kod nie wywnioskuje: ZASIĘG flagi + werdykt rozjazdu.
#
# SERVICE_SCOPED = flaga env-only, której KONSUMUJĄCA GAŁĄŹ KODU biegnie w
# DOKŁADNIE JEDNYM serwisie silnika → env ustawiony tylko w tym unicie jest
# POPRAWNY (nie rozjazd). owner = serwis JEDYNY wykonujący gałąź; „why" =
# konsument zweryfikowany grepem 2026-07-02. Zmiana zasięgu (nowy konsument w
# innym serwisie) UNIEWAŻNIA wpis — checker to wyłapie (patrz completeness).
SERVICE_SCOPED = {
    "CZASOWKA_MAX_EMIT_PER_TICK": (
        "dispatch-czasowka.service",
        "cap emisji czasówek/tick; czytany modułowo w czasowka_scheduler.py:54, "
        "gałąź emisji biegnie WYŁĄCZNIE w ticku czasówki (żaden inny serwis nie "
        "emituje). Tuning operacyjny, nie decyzyjny — hot-reload zbędny."),
    "CZASOWKA_RETROACTIVE_HOURS": (
        "dispatch-czasowka.service",
        "okno retroaktywne triggera (czasowka_scheduler.py:53); tylko tick czasówki."),
    "CZASOWKA_TELEGRAM_DRYRUN": (
        "dispatch-czasowka.service",
        "dry-run wysyłki czasówki (czasowka_scheduler.py:52, SAFE default ON=1); "
        "tylko tick czasówki wysyła."),
    "ENABLE_PLAN_RECHECK_LIVE_ETA_REFRESH": (
        "dispatch-plan-recheck.service",
        "live-ETA refresh w ticku plan-recheck (moduł-level :~89; ready ONLY w "
        "_refresh_live_eta_from_plans — jedyny caller run_recheck:~2712 + guard "
        ":~2710). Reachability RE-ZWERYFIKOWANA 18.07 pełnym call-grafem przy "
        "sprincie B2 (w odróżnieniu od COMMITTED_PROPAGATION, która okazała się "
        "multi-service): NIEOSIĄGALNA z pw (recanon/redecide nie dochodzą) → "
        "kuracja single-service PRAWDZIWA. Migracja do flags.json = opcjonalna "
        "higiena L0.1, bez żywego rozjazdu."),
}

# KNOWN_DIVERGENCES = rozjazdy PRAWDZIWE, cross-service, OTWARTE — wymagają
# naprawy POZA tą partycją (common.py + flags.json + ACK). Śledzone jawnie,
# NIE wyciszone (liczą się do ROZJAZDY). Wpis = diagnoza + plan domknięcia.
#
# USE_V2_PARSER usunięto stąd po migracji do ETAP4 i flipie 2026-07-10. Stary
# env-carrier nadal jest widoczny, ale flags.json jest teraz kanonem i checker
# klasyfikuje go uczciwie jako `json-overrides-env/open` do późniejszego usunięcia.
# ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION przeszla tedy 18.07 (rekuracja z
# falszywej SERVICE_SCOPED 'single-service exec' — pw wolal redecide→_gen od
# 07.06) i tego samego dnia zostala ZMIGROWANA do decision_flag/flags.json
# (sprint B2 za GO Adriana, wzor D3 fala A + hot-reload w pw). Stare env-carriery
# w drop-inach sa martwe → checker klasyfikuje je jako `json-overrides-env/open`
# do zdjecia (jak USE_V2_PARSER po flipie 10.07).
KNOWN_DIVERGENCES = {}


def scan_code_tokens(roots=None) -> set:
    """Wszystkie tokeny-identyfikatory w *.py (dispatch_v2 + cały scripts/,
    poza eod_drafts/__pycache__/.bak) — do wykrywania SIEROT w flags.json.
    Konsumpcja przez C.flag('NAZWA')/load_flags()['NAZWA'] też się łapie,
    bo nazwa występuje w źródle jako literal."""
    if roots is None:
        roots = (DISPATCH_V2, os.path.dirname(DISPATCH_V2))
    tokens = set()
    seen_files = set()
    for root in roots:
        for path in glob.glob(os.path.join(root, "**", "*.py"), recursive=True):
            real = os.path.realpath(path)
            if real in seen_files:
                continue
            seen_files.add(real)
            if "eod_drafts" in path or "__pycache__" in path or ".bak" in path:
                continue
            try:
                src = open(path, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            tokens.update(re.findall(r"[A-Za-z_][A-Za-z0-9_]{3,}", src))
    return tokens


def build_registry(*, common_path: str | None = None,
                   flags_path: str | None = None,
                   systemd_dir: str | None = None,
                   code_roots=None):
    """Zbuduj rejestr z jawnych wejść.

    Domyślne wartości zachowują tryb operatorski na tym hoście. Testy mechanizmu
    przekazują syntetyczne `common.py`, `flags.json`, katalog systemd i roots,
    dzięki czemu wynik nie zależy od bieżącego flipa ani stanu usług.
    """
    common_path = common_path or COMMON_PY
    systemd_dir = systemd_dir or SYSTEMD_DIR
    defs = scan_common(common_path)
    decision, numeric = scan_decision_lists(common_path)
    # Uzupełnij defaulty flag decyzyjnych/numerycznych definiowanych LITERAŁEM
    # (scan_common łapie tylko env-get). scan_common wygrywa gdy oba (env-overridable
    # to pełniejszy opis). Po tym KAŻDA flaga ETAP4 ma definicję → completeness=0
    # jest DOWODEM pełnego skanu, nie tautologią (names NIE seedowane listą ETAP4).
    lit = scan_literal_defaults([n for n in tuple(decision) + tuple(numeric)
                                 if n not in defs], common_path)
    defs = {**lit, **defs}
    fjson = load_flags_json(flags_path)
    unit_env = {u: scan_unit_env(u, systemd_dir) for u in ENGINE_UNITS}
    code_tokens = scan_code_tokens(code_roots)

    names = sorted(set(defs) | set(fjson)
                   | {k for env in unit_env.values() for k in env})
    rows, issues = [], []
    for n in names:
        d = defs.get(n)
        in_fjson = n in fjson
        envs = {u: unit_env[u][n] for u in ENGINE_UNITS if n in unit_env[u]}
        is_decision = n in decision or n in numeric
        if is_decision and in_fjson:
            source, effective = "flags.json (kanon hot-reload)", fjson[n]
        elif is_decision:
            source = "common.py default (brak klucza flags.json)"
            effective = d["default"] if d else None
        elif envs:
            vals = {u: v for u, (v, _f) in envs.items()}
            source, effective = "env unitów (zamrożone przy starcie)", vals
        else:
            source = "common.py default"
            effective = d["default"] if d else None
        rows.append({"flag": n, "defined": bool(d),
                     "default": (d or {}).get("default"),
                     "decision": is_decision, "flags_json": fjson.get(n) if in_fjson else None,
                     "env": {u: f"{v} ({f})" for u, (v, f) in envs.items()},
                     "source": source, "effective": effective})

        # Rozjazdy — sklasyfikowane (L0.1): każdy z polem `klass`, `flag`,
        # `verdict` ∈ {open, accepted-scoped, known-open, intentional}.
        intentional = n in INTENTIONAL_PER_PROCESS
        scoped = n in SERVICE_SCOPED
        known = n in KNOWN_DIVERGENCES
        if envs and not is_decision and set(envs) != set(ENGINE_UNITS):
            only = ", ".join(sorted(envs))
            base = (f"{n}: env-frozen tylko w [{only}] — pozostałe unity silnika "
                    f"liczą defaultem ({(d or {}).get('default')!r})")
            if intentional:
                verdict = "intentional"
                msg = f"✅ {base}. INTENCJONALNE per-proces (INTENTIONAL_PER_PROCESS)."
            elif scoped:
                verdict = "accepted-scoped"
                owner, why = SERVICE_SCOPED[n]
                msg = f"✅ {base}. SERVICE-SCOPED owner={owner}: {why}"
            elif known:
                verdict = "known-open"
                msg = f"⛔ {base}. ZNANY OTWARTY: {KNOWN_DIVERGENCES[n]}"
            else:
                verdict = "open"
                msg = (f"⚠ {base}. NIESKLASYFIKOWANY — dopisz do SERVICE_SCOPED "
                       f"(jeśli konsument w 1 serwisie, z ownerem+why) albo "
                       f"KNOWN_DIVERGENCES (jeśli cross-service, z planem+ACK). "
                       f"flaga SILNIKA cross-service = rozjazd klasy Z-04.")
            issues.append({"level": msg[0], "klass": "env-frozen-subset",
                           "flag": n, "verdict": verdict, "msg": msg})
        if envs and is_decision and in_fjson:
            msg = (f"⚠ {n}: klucz flags.json PRZYKRYWA env w "
                   f"[{', '.join(sorted(envs))}] — env martwy (decyzyjna=hot-reload), "
                   f"usunąć z unitu.")
            issues.append({"level": "⚠", "klass": "json-overrides-env",
                           "flag": n, "verdict": "open", "msg": msg})
        if (in_fjson and n not in code_tokens
                and not any(p.match(n) for p in DYNAMIC_KEY_FAMILIES)):
            msg = (f"⚠ {n}: SIEROTA — klucz w flags.json bez ŻADNEGO wystąpienia w "
                   f"kodzie *.py (literówka albo martwy klucz; klucze dynamiczne "
                   f"f-stringiem sprawdź ręcznie).")
            issues.append({"level": "⚠", "klass": "orphan",
                           "flag": n, "verdict": "open", "msg": msg})
        if envs and not intentional and not scoped:
            uniq = {v for v, _f in envs.values()}
            if len(uniq) > 1:
                msg = (f"⛔ {n}: RÓŻNE wartości env między unitami: "
                       + ", ".join(f"{u}={v}" for u, (v, _f) in sorted(envs.items())))
                issues.append({"level": "⛔", "klass": "value-mismatch",
                               "flag": n, "verdict": "open", "msg": msg})
    return rows, issues


def unclassified_divergences(issues):
    """Rozjazdy env-frozen-subset BEZ werdyktu (nie w SERVICE_SCOPED /
    KNOWN_DIVERGENCES / INTENTIONAL_PER_PROCESS). Cel completeness = 0."""
    return [i for i in issues
            if i["klass"] == "env-frozen-subset" and i["verdict"] == "open"]


def open_issues(issues):
    """Rozjazdy liczące się do metryki #4 (ROZJAZDY): wszystko poza
    accepted-scoped/intentional. known-open ZOSTAJE widoczne (śledzone)."""
    return [i for i in issues if i["verdict"] in ("open", "known-open")]


def accepted_issues(issues):
    return [i for i in issues if i["verdict"] in ("accepted-scoped", "intentional")]


def completeness_gaps(rows, *, common_path: str | None = None,
                      flags_path: str | None = None):
    """Braki pokrycia rejestru (sanity anty-regresja skanera): każdy klucz
    flags.json (nie-dynamiczny, nie-_comment) i każda flaga ETAP4 MUSZĄ mieć
    wiersz w rejestrze. Zwraca listę braków (pusta = pełne pokrycie)."""
    common_path = common_path or COMMON_PY
    fjson = load_flags_json(flags_path)
    decision, numeric = scan_decision_lists(common_path)
    covered = {r["flag"] for r in rows}
    gaps = []
    for k in fjson:
        if any(p.match(k) for p in DYNAMIC_KEY_FAMILIES):
            continue
        if k not in covered:
            gaps.append(("flags.json", k))
    for k in tuple(decision) + tuple(numeric):
        if k not in covered:
            gaps.append(("ETAP4", k))
    return gaps


def _msg(i):
    """Wstecznie kompatybilne: issue może być dict (L0.1) albo str (legacy)."""
    return i["msg"] if isinstance(i, dict) else i


def render(rows, issues, show_all=False, *, common_path: str | None = None,
           flags_path: str | None = None, systemd_dir: str | None = None):
    opn = open_issues(issues)
    acc = accepted_issues(issues)
    common_path = common_path or COMMON_PY
    flags_path = flags_path or FLAGS_JSON
    systemd_dir = systemd_dir or SYSTEMD_DIR
    gaps = completeness_gaps(rows, common_path=common_path, flags_path=flags_path)
    lines = []
    lines.append(f"FLAG REGISTRY — {len(rows)} flag "
                 f"(decyzyjne: {sum(1 for r in rows if r['decision'])}, "
                 f"w flags.json: {sum(1 for r in rows if r['flags_json'] is not None)}, "
                 f"env-frozen gdziekolwiek: {sum(1 for r in rows if r['env'])})")
    lines.append(f"WEJŚCIA: common={common_path} flags={flags_path} systemd={systemd_dir}")
    lines.append("")
    # UWAGA: metryka #4 (entropy_dashboard) parsuje `ROZJAZDY (\d+)` = OTWARTE
    # (open + known-open). accepted-scoped/intentional NIE liczą się (poprawne).
    lines.append(f"ROZJAZDY ({len(opn)}):")
    lines.extend("  " + _msg(i) for i in opn) if opn else lines.append("  (brak)")
    lines.append("")
    lines.append(f"AKCEPTOWANE service-scoped / intentional ({len(acc)}):")
    lines.extend("  " + _msg(i) for i in acc) if acc else lines.append("  (brak)")
    lines.append("")
    lines.append(f"BRAKI POKRYCIA rejestru ({len(gaps)}):")
    lines.extend(f"  ⛔ {src}:{k} bez wiersza — regresja skanera?" for src, k in gaps) \
        if gaps else lines.append("  (brak — pełne pokrycie flags.json + ETAP4)")
    if show_all:
        lines.append("")
        for r in rows:
            lines.append(f"- {r['flag']}: efektywnie={r['effective']!r} "
                         f"[{r['source']}] default={r['default']!r} "
                         f"flags.json={r['flags_json']!r} env={r['env'] or '—'}")
    return "\n".join(lines)


def render_md(rows, issues, *, common_path: str | None = None,
              flags_path: str | None = None, systemd_dir: str | None = None):
    opn, acc = open_issues(issues), accepted_issues(issues)
    out = ["# Rejestr flag (F3) — wygenerowany tools/flag_registry.py", ""]
    out.append(f"Wejścia: `common={common_path or COMMON_PY}` · "
               f"`flags={flags_path or FLAGS_JSON}` · "
               f"`systemd={systemd_dir or SYSTEMD_DIR}`")
    out.append("")
    out.append(f"Flag: **{len(rows)}** · rozjazdy OTWARTE: **{len(opn)}** · "
               f"akceptowane service-scoped: **{len(acc)}**")
    out.append("")
    out.append("## Rozjazdy OTWARTE")
    out.extend(f"- {_msg(i)}" for i in opn) if opn else out.append("- brak")
    out.append("")
    out.append("## Akceptowane service-scoped / intentional")
    out.extend(f"- {_msg(i)}" for i in acc) if acc else out.append("- brak")
    out.append("")
    out.append("## Pełny inwentarz")
    out.append("| flaga | efektywna | źródło | default | flags.json | env |")
    out.append("|---|---|---|---|---|---|")
    for r in rows:
        env = "<br>".join(f"{u}: {v}" for u, v in r["env"].items()) or "—"
        out.append(f"| `{r['flag']}` | `{r['effective']!r}` | {r['source']} "
                   f"| `{r['default']!r}` | `{r['flags_json']!r}` | {env} |")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="pełny inwentarz na stdout")
    ap.add_argument("--md", help="zapisz raport markdown do pliku")
    ap.add_argument("--common-py", default=COMMON_PY,
                    help="jawne źródło definicji flag")
    ap.add_argument("--flags-json", default=FLAGS_JSON,
                    help="jawny snapshot flags.json")
    ap.add_argument("--systemd-dir", default=SYSTEMD_DIR,
                    help="jawny katalog unitów/drop-inów")
    args = ap.parse_args()
    rows, issues = build_registry(common_path=args.common_py,
                                  flags_path=args.flags_json,
                                  systemd_dir=args.systemd_dir)
    print(render(rows, issues, show_all=args.all,
                 common_path=args.common_py, flags_path=args.flags_json,
                 systemd_dir=args.systemd_dir))
    if args.md:
        with open(args.md, "w", encoding="utf-8") as f:
            f.write(render_md(rows, issues, common_path=args.common_py,
                              flags_path=args.flags_json,
                              systemd_dir=args.systemd_dir) + "\n")
        print(f"\nMD: {args.md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
