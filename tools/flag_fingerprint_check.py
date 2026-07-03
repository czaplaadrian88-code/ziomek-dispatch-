#!/usr/bin/env python3
"""flag_fingerprint_check — rekoncyliacja EFEKTYWNEGO stanu flag per serwis (L0.1, 2026-07-02).

Przyszły STRAŻNIK „stan flagi = efektywny w procesie" (Przykazanie #0 wzorzec #9).
READ-ONLY. Łączy CZTERY źródła prawdy i wypisuje rozjazdy per flaga per serwis:

  (a) flags.json                — kanon hot-reload flag decyzyjnych,
  (b) FLAG_FINGERPRINT z logów  — co proces FAKTYCZNIE rozwiązał przy starcie
                                  (per serwis; journald tylko dla shadow, reszta
                                  loguje do plików scripts/logs/<proc>.log),
  (c) Environment= + drop-iny   — /etc/systemd/system/*.service(.d) (env-frozen),
  (d) rejestr (flag_registry)   — pokrycie + werdykty service-scoped/known.

Reguła „kto wygrywa" (wzorzec #9):
  • flaga DECYZYJNA (ETAP4/flags.json) = HOT-RELOAD → flags.json wygrywa NA ŻYWO;
    fingerprint to SNAPSHOT startu (może dryfować gdy flags.json edytowany po
    starcie — benign, proces doczyta przy następnym `flag()`),
  • flaga ENV-only (moduł-level const) = ZAMROŻONA przy starcie → env unitu
    wygrywa; klucz flags.json o tej nazwie jest MARTWY.

Wykrywane klasy:
  VALUE-MISMATCH   ⛔ flaga w ≥2 fingerprintach z RÓŻNĄ wartością (żywy rozjazd),
  INTERMITTENT-COLD ⛔ proc emituje fingerprint = common.py DEFAULTY (flags.json
                     overrides NIE zaaplikowane) w części emitów (flag-load part-time
                     zawodzi). ≥COLD_DRIFT_MIN flag naraz ≠ flags.json = wholesale,
                     NIE targeted drift → collapse do 1 findingu (per-flag JSON-DRIFT
                     tego proc POMINIĘTY gdy last=cold),
  COVERAGE-GAP     ⚠ flaga w części fingerprintów, brak w innym (proc starszy niż
                     dodanie do ETAP4 → stale-process; restart rekoncyliuje),
  JSON-DRIFT       ⚠ decyzyjna: flags.json ≠ fingerprint w serwisie (hot-reload
                     live=flags.json; fingerprint stale),
  ENV-DEAD         ⚠ env-frozen dla flagi decyzyjnej (flags.json przykrywa env),
  REGISTRY-ONLY    ⚠ decyzyjna w rejestrze, w ŻADNYM fingerprincie (nie rozwiązana).

Użycie:
  python3 -m dispatch_v2.tools.flag_fingerprint_check            # tabela
  python3 -m dispatch_v2.tools.flag_fingerprint_check --jsonl P  # + jsonl do P
  python3 -m dispatch_v2.tools.flag_fingerprint_check --json     # jsonl na stdout
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
DISPATCH_V2 = os.path.dirname(_HERE)
SCRIPTS_ROOT = os.path.dirname(DISPATCH_V2)
FLAGS_JSON = "/root/.openclaw/workspace/scripts/flags.json"
LOGS_DIR = "/root/.openclaw/workspace/scripts/logs"
SYSTEMD_DIR = "/etc/systemd/system"

# proc= w linii fingerprint  →  (plik logu, unit systemd, journalctl fallback)
SERVICES = {
    "shadow": ("shadow.log", "dispatch-shadow.service", "dispatch-shadow"),
    "plan-recheck": ("plan_recheck.log", "dispatch-plan-recheck.service", "dispatch-plan-recheck"),
    "panel-watcher": ("watcher.log", "dispatch-panel-watcher.service", "dispatch-panel-watcher"),
    "czasowka": ("czasowka.log", "dispatch-czasowka.service", "dispatch-czasowka"),
}

_FP_KV = re.compile(r"\b([A-Z_][A-Z0-9_]*)=([01])\b")

# Ile boolowskich flag decyzyjnych naraz może się rozjechać z flags.json zanim
# uznamy to za COLD-LOAD (wholesale: proc użył common.py defaultów zamiast
# flags.json) a nie za garść targeted JSON-DRIFT. Dobrane z marginesem:
# obserwowany cold czasówki = 38 flag, benign-stale panel-watcher = 1 flaga.
COLD_DRIFT_MIN = 15


def _last_fingerprint_line(proc: str) -> str | None:
    """Ostatnia linia FLAG_FINGERPRINT danego procesu: najpierw plik logu, potem
    journald (fallback dla serwisów logujących tylko do journala)."""
    log, _unit, jctl = SERVICES[proc]
    path = os.path.join(LOGS_DIR, log)
    best = None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if "FLAG_FINGERPRINT" in line and f"proc={proc}" in line:
                    best = line
    except OSError:
        best = None
    if best:
        return best
    try:  # journald fallback
        r = subprocess.run(["journalctl", "-u", jctl, "--no-pager", "-o", "cat"],
                           capture_output=True, text=True, timeout=30)
        for line in (r.stdout or "").splitlines():
            if "FLAG_FINGERPRINT" in line and f"proc={proc}" in line:
                best = line
    except Exception:
        pass
    return best


def parse_fingerprints() -> dict:
    """{proc: {flag: '0'/'1'}} z ostatniego startu każdego serwisu (puste = brak
    linii / serwis nie logował / nie działa)."""
    out = {}
    for proc in SERVICES:
        line = _last_fingerprint_line(proc)
        if not line:
            out[proc] = {}
            continue
        body = line.split("FLAG_FINGERPRINT", 1)[1]
        out[proc] = dict(_FP_KV.findall(body))
    return out


def _recent_fingerprints(proc: str, limit: int = 30) -> list:
    """Ostatnie `limit` fingerprintów procesu z PLIKU logu — do wykrycia
    NIESTABILNOŚCI: proc emitujący raz wartości z flags.json, raz same defaulty
    common.py = flags.json overrides NIE zaaplikowane przy części emitów.
    Zwraca listę dictów {flag: '0'/'1'} (najstarszy→najnowszy)."""
    log = SERVICES[proc][0]
    path = os.path.join(LOGS_DIR, log)
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if "FLAG_FINGERPRINT" in line and f"proc={proc}" in line:
                    body = line.split("FLAG_FINGERPRINT", 1)[1]
                    out.append(dict(_FP_KV.findall(body)))
    except OSError:
        return []
    return out[-limit:]


def load_flags_json(path: str | None = None) -> dict:
    # path=None → czyta MODUŁOWY FLAGS_JSON PRZY WYWOŁANIU (nie default z def-time),
    # by monkeypatch ffc.FLAGS_JSON działał w testach (pułapka domyślnego argumentu).
    path = path or FLAGS_JSON
    try:
        return {k: v for k, v in json.load(open(path)).items()
                if not k.startswith("_comment")}
    except Exception:
        return {}


def _drift_count(fp: dict, fjson: dict, bool_flags: set) -> int:
    """Ile boolowskich flag decyzyjnych w fingerprincie ≠ flags.json (mismatch,
    nie brak). ≥COLD_DRIFT_MIN = wholesale cold-load (common.py defaulty)."""
    n = 0
    for flag, val in fp.items():
        if flag in bool_flags and flag in fjson:
            want = _fjson_bool(fjson[flag])
            if want is not None and val != want:
                n += 1
    return n


def _fjson_bool(v):
    """Wartość flags.json → '0'/'1' gdy boolowska, else None (numeryczna/inna)."""
    if isinstance(v, bool):
        return "1" if v else "0"
    if v in (0, 1):
        return "1" if v else "0"
    return None


def scan_unit_env(unit: str) -> dict:
    """Environment= main unit + drop-iny (ostatni wygrywa)."""
    env = {}
    files = []
    main = os.path.join(SYSTEMD_DIR, unit)
    if os.path.exists(main):
        files.append(main)
    files += sorted(glob.glob(os.path.join(SYSTEMD_DIR, unit + ".d", "*.conf")))
    for f in files:
        try:
            for line in open(f, encoding="utf-8", errors="replace"):
                line = line.strip()
                if not line.startswith("Environment="):
                    continue
                body = line[len("Environment="):].strip().strip('"')
                if "=" in body:
                    k, v = body.split("=", 1)
                    env[k.strip()] = v.strip()
        except OSError:
            continue
    return env


def _load_flag_registry():
    """flag_registry jako moduł — działa i jako pakiet (`-m dispatch_v2.tools…`),
    i standalone (worktree/kanon) przez load-by-path z tego samego katalogu."""
    try:
        from dispatch_v2.tools import flag_registry as fr
        return fr
    except Exception:
        import importlib.util
        p = os.path.join(_HERE, "flag_registry.py")
        spec = importlib.util.spec_from_file_location("_flag_registry_sibling", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod


def _decision_flags():
    """(bool_flags, numeric_flags): fingerprint zawiera TYLKO boolowskie flagi
    decyzyjne (0/1). Numeryczne overridy (FLAGS_JSON_NUMERIC_OVERRIDES) są
    legalnie POZA fingerprintem — nie mylić z brakiem (fałszywka #17)."""
    fr = _load_flag_registry()
    decision, numeric = fr.scan_decision_lists()
    return set(decision), set(numeric)


def reconcile() -> dict:
    fps = parse_fingerprints()
    fjson = load_flags_json()
    bool_flags, numeric_flags = _decision_flags()
    decision_flags = bool_flags | numeric_flags
    unit_env = {proc: scan_unit_env(SERVICES[proc][1]) for proc in SERVICES}

    live_procs = [p for p in SERVICES if fps[p]]
    all_fp_flags = set().union(*[set(fps[p]) for p in SERVICES]) if any(fps.values()) else set()
    findings = []

    def add(klass, flag, detail, who_wins):
        findings.append({"klass": klass, "flag": flag, "detail": detail,
                         "who_wins": who_wins})

    # 1) COVERAGE-GAP: flaga w części fingerprintów, brak w innym żywym procesie.
    #    VALUE-MISMATCH tylko dla NIE-decyzyjnych (env-frozen) w ≥2 fingerprintach
    #    — dla decyzyjnych (hot-reload) różnica snapshotów = JSON-DRIFT (poniżej),
    #    nie realny live-rozjazd (#17 anty-fałszywka: live=flags.json dla wszystkich).
    for flag in sorted(all_fp_flags):
        present = {p: fps[p][flag] for p in SERVICES if flag in fps[p]}
        vals = set(present.values())
        if len(present) < len(live_procs):
            missing = [p for p in live_procs if flag not in fps[p]]
            reason = ("stale-process: flaga dodana do ETAP4 po starcie tych "
                      "procesów → restart je zrekoncyliuje"
                      if flag in bool_flags else
                      "flaga nie rozwiązana w tych procesach (poza ich fingerprintem)")
            add("COVERAGE-GAP", flag,
                {"value": sorted(vals)[0] if vals else None,
                 "obecna_w": sorted(present), "brak_w": missing}, reason)
        elif len(vals) > 1 and flag not in bool_flags:
            add("VALUE-MISMATCH", flag, {p: present.get(p) for p in SERVICES},
                "flaga ENV-FROZEN z RÓŻNĄ wartością między procesami = żywy rozjazd "
                "(cross-proces env divergence). Ujednolić drop-iny.")

    # 2a) INTERMITTENT-COLD: proc, którego fingerprint rozjeżdża się z flags.json
    #    WHOLESALE (≥COLD_DRIFT_MIN flag naraz) = użył common.py defaultów zamiast
    #    flags.json (flag-load nie zaaplikował overrides). Skanujemy recent, by
    #    pokazać CZĘSTOTLIWOŚĆ (part-time zawodzenie, nie jednorazowy blip). Gdy
    #    ostatni snapshot procesu jest cold — jego per-flag JSON-DRIFT pomijamy
    #    (to artefakt cold-load, nie targeted drift → nie zaśmiecamy 38 wpisami).
    cold_last = set()
    for proc in live_procs:
        recent = _recent_fingerprints(proc)
        if not recent:
            continue
        cold_recent = sum(1 for fp in recent
                          if _drift_count(fp, fjson, bool_flags) >= COLD_DRIFT_MIN)
        last_cold = _drift_count(fps[proc], fjson, bool_flags) >= COLD_DRIFT_MIN
        if last_cold:
            cold_last.add(proc)
        if cold_recent or last_cold:
            add("INTERMITTENT-COLD", f"proc:{proc}",
                {"cold_recent": cold_recent, "z_ostatnich": len(recent),
                 "ostatni_snapshot_cold": last_cold,
                 "flag_rozjazd_gdy_cold": _drift_count(fps[proc], fjson, bool_flags)
                 if last_cold else COLD_DRIFT_MIN},
                f"proc {proc}: fingerprint = common.py DEFAULTY (flags.json overrides "
                f"NIE zaaplikowane) w {cold_recent}/{len(recent)} ostatnich emitach "
                f"(≥{COLD_DRIFT_MIN} flag ≠ flags.json). ⚠ ZANIM uznasz za flag-load "
                f"bug silnika: skoreluj timestampy cold-linii ze startami serwisu w "
                f"journalu (`journalctl -u dispatch-<proc>`). Cold POZA kadencją ticków "
                f"= OBCY proces pisał do PROD-loga (testy pytest z odartym flags.json — "
                f"klasa kłamiącego przyrządu; eskalacja 02.07 REFUTED tak właśnie: "
                f"334/334 ticków warm, 0/9 klastrów cold od serwisu; od 03.07 guard "
                f"DISPATCH_UNDER_PYTEST w common.setup_logger wycisza file-logi testów). "
                f"Cold NA tickach serwisu = realny flag-load bug → ESKALUJ. Per-flag "
                f"JSON-DRIFT tego proc pominięty gdy last=cold.")

    # 2b) JSON-DRIFT: decyzyjna (hot-reload), fingerprint procesu ≠ AKTUALNY flags.json.
    #    To PRIMARNY sygnał dla flag decyzyjnych: live=flags.json, więc fingerprint≠json
    #    znaczy proces czytał inny/starszy stan przy ostatnim reloadzie. Benign jeśli
    #    flags.json zmieniony PO starcie procesu (mtime); ⚠ jeśli mtime STARSZY niż
    #    fingerprint a nadal dryf — proces widzi inny flags.json / nie hot-reloaduje.
    #    Procy z last=cold pomijamy (ich dryf = cold-load, raportowany w 2a).
    for flag in sorted(all_fp_flags & set(fjson)):
        want = _fjson_bool(fjson[flag])
        if want is None or flag not in bool_flags:
            continue
        drift = {p: fps[p][flag] for p in live_procs
                 if p not in cold_last and flag in fps[p] and fps[p][flag] != want}
        if drift:
            add("JSON-DRIFT", flag,
                {"flags.json": want, "fingerprint": drift},
                "live=flags.json (hot-reload mtime-based). Sprawdź `stat flags.json` "
                "vs czas fingerprint procesu: json nowszy = benign snapshot; json "
                "starszy = proces nie widzi bieżącego flags.json (ESKALUJ).")

    # 3) ENV-DEAD (wzorzec #9): decyzyjna ustawiona w Environment= unitu
    for proc in SERVICES:
        for flag, val in sorted(unit_env[proc].items()):
            if flag in decision_flags and flag in fjson:
                add("ENV-DEAD", flag,
                    {"unit": SERVICES[proc][1], "env": val,
                     "flags.json": _fjson_bool(fjson.get(flag))},
                    "flaga DECYZYJNA → flags.json przykrywa env; Environment= w unicie "
                    "MARTWY (usunąć z drop-inu, mylące).")

    # 4) REGISTRY-ONLY: BOOLOWSKA decyzyjna w rejestrze, w ŻADNYM fingerprincie
    #    (numeryczne overridy pomijamy — legalnie poza fingerprintem, #17).
    if live_procs:
        for flag in sorted(bool_flags - all_fp_flags):
            add("REGISTRY-ONLY", flag,
                {"w_fingerprincie": False},
                "boolowska decyzyjna w ETAP4 ale w żadnym fingerprincie — dodana po "
                "ostatnim starcie WSZYSTKICH procesów (globalny restart zrekoncyliuje).")

    return {
        "procs_live": live_procs,
        "procs_dead": [p for p in SERVICES if not fps[p]],
        "fingerprint_sizes": {p: len(fps[p]) for p in SERVICES},
        "findings": findings,
    }


def render(res: dict) -> str:
    L = []
    L.append("FLAG FINGERPRINT CHECK — rekoncyliacja stanu flag per serwis (L0.1)")
    L.append(f"procesy żywe: {res['procs_live']} | bez fingerprint: {res['procs_dead']}")
    L.append("rozmiary fingerprintów: "
             + ", ".join(f"{p}={n}" for p, n in res["fingerprint_sizes"].items()))
    L.append("")
    f = res["findings"]
    L.append(f"ROZJAZDY ({len(f)}):")
    if not f:
        L.append("  (brak — wszystkie serwisy spójne)")
    order = {"VALUE-MISMATCH": 0, "INTERMITTENT-COLD": 1, "ENV-DEAD": 2,
             "COVERAGE-GAP": 3, "REGISTRY-ONLY": 4, "JSON-DRIFT": 5}
    # JSON-DRIFT bywa liczny (dziesiątki hot-reload flag) → SUMARYZUJ per serwis,
    # pełna lista w jsonl. Reszta klas: per-wpis (mało).
    drift = [it for it in f if it["klass"] == "JSON-DRIFT"]
    rest = [it for it in f if it["klass"] != "JSON-DRIFT"]
    for it in sorted(rest, key=lambda x: (order.get(x["klass"], 9), x["flag"])):
        L.append(f"  [{it['klass']}] {it['flag']}")
        L.append(f"       {json.dumps(it['detail'], ensure_ascii=False)}")
        L.append(f"       → {it['who_wins']}")
    if drift:
        per_svc = {}
        for it in drift:
            for svc in it["detail"]["fingerprint"]:
                per_svc.setdefault(svc, []).append(it["flag"])
        L.append(f"  [JSON-DRIFT] {len(drift)} flag decyzyjnych fingerprint≠flags.json "
                 f"(pełna lista w jsonl). Serwisy dryfujące:")
        for svc, flags in sorted(per_svc.items()):
            ex = ", ".join(sorted(flags)[:4])
            L.append(f"       · {svc}: {len(flags)} flag (np. {ex}…)")
        L.append(f"       → {drift[0]['who_wins']}")
    L.append("")
    counts = {}
    for it in f:
        counts[it["klass"]] = counts.get(it["klass"], 0) + 1
    L.append("PER KLASA: " + (", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
                              or "(brak)"))
    return "\n".join(L)


def _jsonl_lines(res: dict):
    yield json.dumps({"_meta": {"procs_live": res["procs_live"],
                               "procs_dead": res["procs_dead"],
                               "fingerprint_sizes": res["fingerprint_sizes"]}},
                     ensure_ascii=False, sort_keys=True)
    for it in sorted(res["findings"], key=lambda x: (x["klass"], x["flag"])):
        yield json.dumps(it, ensure_ascii=False, sort_keys=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", help="zapisz rozjazdy jako jsonl do pliku")
    ap.add_argument("--json", action="store_true", help="jsonl na stdout (bez tabeli)")
    args = ap.parse_args()
    res = reconcile()
    if args.json:
        print("\n".join(_jsonl_lines(res)))
    else:
        print(render(res))
    if args.jsonl:
        with open(args.jsonl, "w", encoding="utf-8") as fh:
            fh.write("\n".join(_jsonl_lines(res)) + "\n")
        if not args.json:
            print(f"\nJSONL: {args.jsonl}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
