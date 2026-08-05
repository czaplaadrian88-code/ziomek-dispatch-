#!/usr/bin/env python3
"""shadow_review_daily — JEDEN dobowy czytelnik cienia (zastępuje 7 wypalonych timerów one-shot).

Powód istnienia (ŻNIWA 2026-08-03): pisarze cienia chodzą co 1-5 min, a czytelnicy byli
timerami JEDNORAZOWYMI i wygaśli między 26.06 a 06.07. Skutek: 777 MB strumieni rośnie 24/7,
a ostatnie werdykty mają 28-38 dni. To nie brak danych — to brak odbioru danych.

Kontrakt tego narzędzia:
  * 100 % READ-ONLY wobec stanu. Nie dotyka dispatch_state/, scripts/logs/, flags.json ani ledgera.
    Pisze WYŁĄCZNIE do własnego katalogu raportów (--out-dir).
  * ZERO Telegrama. Każdy czytelnik jest wołany z --no-telegram; czytelnik bez tego przełącznika
    NIE JEST uruchamiany w ogóle (lista EXCLUDED niżej z powodem).
  * FAIL-CLOSED na korpusie. Werdykt nie powstaje, jeśli korpus jest martwy (stale) albo jeśli
    nie da się jednoznacznie ustalić jego ścieżki. Lepiej brak werdyktu niż werdykt z martwego
    strumienia — dokładnie ten defekt wyprodukował pusty `MIXED` b_route (real_joined: 0) 30.06.
  * JEDEN kanoniczny właściciel ścieżki korpusu = konfiguracja PISARZA (unit systemd + drop-iny,
    czytane przez `systemctl cat`). Czytelnik nie ma własnego, konkurencyjnego defaultu.

Uruchomienie ręczne (read-only):
    /root/.openclaw/venvs/dispatch/bin/python shadow_review_daily.py --out-dir /root/artifacts/shadow-review
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone

VENV_PY = "/root/.openclaw/venvs/dispatch/bin/python"
SCRIPTS = "/root/.openclaw/workspace/scripts"
STATE = "/root/.openclaw/workspace/dispatch_state"
DEFAULT_OUT = "/root/artifacts/shadow-review"

# Ile godzin wstecz korpus wolno uznać za żywy. Pisarze chodzą co 1-5 min, więc 48 h to
# ~600x zapas — próg łapie strumień MARTWY (dni/tygodnie ciszy), nie chwilową przerwę.
DEFAULT_MAX_CORPUS_AGE_H = 48.0


class Reader:
    """Jeden czytelnik cienia + kontrakt jego korpusu."""

    def __init__(self, name, argv, corpus, corpus_from_unit=None,
                 corpus_env=None, timeout=900, note="", out_dir_flag=None):
        self.name = name
        self.argv = argv
        self.corpus = corpus                    # ścieżka stała albo None gdy z unitu
        self.corpus_from_unit = corpus_from_unit  # (unit, ENV_KEY) — kanoniczne źródło ścieżki
        self.corpus_env = corpus_env            # ENV do przekazania czytelnikowi
        self.timeout = timeout
        self.note = note
        # Czytelnik, który sam produkuje TRWAŁY artefakt (nie tylko stdout do <nazwa>.log),
        # dostaje katalog dnia tą flagą. Bez tego pisałby we własny default, czyli POZA
        # sandbox systemd (ReadWritePaths = tylko katalog raportów) — job by się wywalił.
        self.out_dir_flag = out_dir_flag


READERS = [
    Reader(
        name="b_route",
        argv=["-m", "dispatch_v2.tools.b_route_shadow_review", "--no-telegram"],
        corpus=f"{STATE}/b_route_shadow.jsonl",
        note="join z realnym outcome naprawiony u źródła w L1.2 (97f27e9be, 02.07); "
             "ostatni bieg 30.06 był PRZED fixem i dał real_joined=0",
    ),
    Reader(
        name="bundle_calib",
        argv=["-m", "dispatch_v2.tools.bundle_calib_review", "--no-telegram"],
        corpus=None,
        corpus_from_unit=("dispatch-bundle-calib-shadow.service", "BUNDLE_CALIB_OUT_JSONL"),
        corpus_env="BUNDLE_CALIB_CORPUS",
        note="korpus MUSI iść za pisarzem: drop-in λ=0 z 03.07 przepiął pisarza na "
             "bundle_calib_shadow_l0.jsonl, a default czytelnika wskazuje stary korpus λ=1.5 "
             "zamrożony 02.07. Bez tego wiązania odrodzony timer werdyktowałby martwy strumień.",
    ),
    Reader(
        name="pending_resweep",
        argv=["-m", "dispatch_v2.tools.pending_global_resweep_review", "--no-telegram"],
        corpus=f"{STATE}/pending_global_resweep.jsonl",
        note="bramka resweep.g5-g6-shadow = READY_FOR_REVIEW",
    ),
    Reader(
        name="reassignment",
        argv=["-m", "dispatch_v2.tools.reassignment_shadow_eval", "--no-telegram"],
        corpus=f"{STATE}/reassignment_shadow.jsonl",
        timeout=1800,
        note="korpus 91 MB — najdłuższy bieg w zestawie",
    ),
    Reader(
        name="suwak_autonomii",
        argv=["-m", "dispatch_v2.tools.suwak_autonomii_review", "--no-telegram"],
        corpus=None,
        out_dir_flag="--out-dir",
        note="SUWAK AUTONOMII (2 liczby ownera z 19.07) — jedyny czytelnik z DWOMA źródłami "
             "(scripts/logs/shadow_decisions.jsonl + rotacje ORAZ dispatch_state/"
             "outcomes_clean_shadow.jsonl), więc świeżość każdego z nich sprawdza SAM i "
             "raportuje jako null+powód. Bramka korpusu harnessu (jedna ścieżka na czytelnika) "
             "wycięłaby go z szeregu czasowego przy uschnięciu JEDNEGO źródła — a dziura w "
             "szeregu jest gorsza niż rekord z jawnym powodem. Wynik: append do "
             "<out-dir>/../suwak_autonomii.jsonl + snapshot dnia.",
    ),
]

# Katalogi ŻYWEGO stanu — żaden wołany czytelnik nie ma prawa do nich pisać.
# Lista jest kontraktem sprawdzanym ratchetem testowym, nie komentarzem.
LIVE_STATE_DIRS = (
    "/root/.openclaw/workspace/dispatch_state",
    "/root/.openclaw/workspace/scripts/logs",
)

# Czytelnicy ŚWIADOMIE NIEURUCHAMIANI przez ten job — z powodem. To nie jest przeoczenie.
EXCLUDED = [
    {
        "name": "objm_lexr6_smoke_verdict",
        "unit": "dispatch-objm-lexr6-smoke-verdict.service",
        "reason": "brak przełącznika --no-telegram (send_admin_alert bezwarunkowy, "
                  "objm_lexr6_smoke_verdict.py:37). Poza tym to werdykt JEDNORAZOWEGO smoke-testu "
                  "z 26.06, nie odczyt cykliczny — właściwym domknięciem jest bramka, nie timer.",
    },
    {
        "name": "pickup_slip_monitor",
        "unit": "dispatch-pickup-slip-review.service",
        "reason": "to NIE jest czytelnik, tylko PISARZ — dopisuje wyniki do "
                  "dispatch_state/pickup_slip_monitor.jsonl (pickup_slip_monitor.py:175-181), "
                  "co łamie kontrakt read-only tego joba. Poza tym jest zbędny: własny timer "
                  "dispatch-pickup-slip-monitor.timer ŻYJE i chodzi codziennie 22:30 UTC "
                  "(--days 3). Wypalony one-shot ...-review z 04.07 był jednorazowym biegiem "
                  "na szerszym oknie (--days 6), a nie odczytem cyklicznym.",
    },
    {
        "name": "ziomek_time_route_review",
        "unit": "ziomek-time-route-review.service",
        "reason": "brak --no-telegram (_telegram() bezwarunkowe). Dodatkowo jego PISARZ "
                  "ziomek-time-route-monitor ma MONITOR_STOP_AFTER=2026-07-10 (minęło) i chodzi "
                  "mimo wygaszenia — właściwa naprawa to WYŁĄCZENIE pisarza za ACK, "
                  "nie ożywianie czytelnika.",
    },
]


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_env_from_unit_text(text: str, env_key: str):
    """Wartość `Environment=<KLUCZ>=...` z tekstu `systemctl cat` (unit + drop-iny).

    Ostatnie przypisanie wygrywa — tak samo interpretuje je systemd. Wydzielone z
    resolve_corpus_from_unit, żeby dało się to przetestować bez systemd.
    """
    found = None
    pat = re.compile(r"^\s*Environment\s*=\s*[\"']?" + re.escape(env_key) + r"=([^\"'\s]+)")
    for line in text.splitlines():
        m = pat.match(line)
        if m:
            found = m.group(1)
    return found


def resolve_corpus_from_unit(unit: str, env_key: str):
    """Ścieżka korpusu z KONFIGURACJI PISARZA (unit + drop-iny).

    `systemctl cat` pokazuje drop-iny (w przeciwieństwie do `systemctl show -p Environment`),
    więc to jedyne wiarygodne źródło. Brak klucza = brak werdyktu (fail-closed), NIE cichy default.
    """
    try:
        out = subprocess.run(["systemctl", "cat", unit], capture_output=True,
                             text=True, timeout=30)
    except Exception as exc:
        return None, f"systemctl cat {unit} nie wystartował: {type(exc).__name__}: {exc}"
    if out.returncode != 0:
        return None, f"systemctl cat {unit} exit={out.returncode}: {out.stderr.strip()[:200]}"
    found = parse_env_from_unit_text(out.stdout, env_key)
    if not found:
        return None, (f"w konfiguracji {unit} nie ma Environment={env_key}= — nie da się "
                      f"jednoznacznie ustalić korpusu")
    return found, ""


def corpus_health(path: str, max_age_h: float):
    """Czy korpus żyje. Zwraca (ok, opis, meta)."""
    if not os.path.exists(path):
        return False, f"korpus nie istnieje: {path}", {}
    st = os.stat(path)
    age_h = (time.time() - st.st_mtime) / 3600.0
    meta = {
        "path": path,
        "size_mb": round(st.st_size / 1048576.0, 1),
        "mtime": _iso(datetime.fromtimestamp(st.st_mtime, timezone.utc)),
        "age_hours": round(age_h, 1),
    }
    if st.st_size == 0:
        return False, "korpus PUSTY (0 B)", meta
    if age_h > max_age_h:
        return False, (f"korpus MARTWY — ostatni zapis {round(age_h / 24.0, 1)} dni temu "
                       f"(próg {max_age_h} h)"), meta
    return True, "", meta


def run_reader(reader: Reader, out_dir: str, max_age_h: float, dry: bool):
    res = {
        "name": reader.name,
        "argv": [VENV_PY] + reader.argv,
        "note": reader.note,
        "status": None,
        "corpus": None,
        "detail": "",
        "log": None,
        "duration_s": None,
        "exit_code": None,
    }

    argv = list(reader.argv)
    if reader.out_dir_flag:
        argv += [reader.out_dir_flag, out_dir]
        res["argv"] = [VENV_PY] + argv

    corpus = reader.corpus
    env_extra = {}
    if reader.corpus_from_unit:
        unit, key = reader.corpus_from_unit
        corpus, err = resolve_corpus_from_unit(unit, key)
        if corpus is None:
            res["status"] = "BLOCKED"
            res["detail"] = f"nie ustalono korpusu z {unit}: {err}"
            return res
        if reader.corpus_env:
            env_extra[reader.corpus_env] = corpus

    if corpus:
        ok, why, meta = corpus_health(corpus, max_age_h)
        res["corpus"] = meta or {"path": corpus}
        if not ok:
            res["status"] = "SKIPPED_STALE"
            res["detail"] = why
            return res

    if dry:
        res["status"] = "DRY_RUN"
        res["detail"] = "tryb --dry-run: sprawdzono korpus, czytelnika nie uruchomiono"
        return res

    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(env_extra)
    log_path = os.path.join(out_dir, f"{reader.name}.log")
    t0 = time.time()
    try:
        proc = subprocess.run([VENV_PY] + argv, cwd=SCRIPTS, env=env,
                              capture_output=True, text=True, timeout=reader.timeout)
        out = proc.stdout or ""
        err = proc.stderr or ""
        res["exit_code"] = proc.returncode
        res["status"] = "OK" if proc.returncode == 0 else "FAILED"
    except subprocess.TimeoutExpired:
        out, err = "", f"TIMEOUT po {reader.timeout}s"
        res["status"] = "TIMEOUT"
    except Exception as exc:
        out, err = "", f"{type(exc).__name__}: {exc}"
        res["status"] = "ERROR"
    res["duration_s"] = round(time.time() - t0, 1)

    header = (f"# {reader.name} | {_iso(_now())} | status={res['status']} "
              f"exit={res['exit_code']} czas={res['duration_s']}s\n"
              f"# korpus: {json.dumps(res['corpus'], ensure_ascii=False)}\n"
              f"# argv: {' '.join(res['argv'])}\n\n")
    with open(log_path, "w") as fh:
        fh.write(header)
        fh.write(out)
        if err:
            fh.write("\n--- stderr ---\n")
            fh.write(err)
    res["log"] = log_path
    res["verdict_line"] = _extract_verdict(out)
    if res["status"] != "OK" and err:
        res["detail"] = err.strip().splitlines()[-1][:300] if err.strip() else ""
    return res


def _extract_verdict(text: str):
    """Linia werdyktu, jeśli czytelnik ją drukuje (konwencja '➤ WERDYKT:' / '"verdict"')."""
    for line in text.splitlines():
        if "WERDYKT" in line:
            return line.strip()[:400]
    for line in reversed(text.splitlines()):
        if '"verdict"' in line:
            try:
                obj = json.loads(line.split("JSON:", 1)[-1].strip())
                return f"verdict={obj.get('verdict')}"
            except Exception:
                return line.strip()[:400]
    return None


def main():
    ap = argparse.ArgumentParser(description="Dobowy zbiorczy czytelnik strumieni cienia (read-only).")
    ap.add_argument("--out-dir", default=DEFAULT_OUT,
                    help=f"katalog raportów (default {DEFAULT_OUT})")
    ap.add_argument("--max-corpus-age-hours", type=float, default=DEFAULT_MAX_CORPUS_AGE_H,
                    help="powyżej tego wieku korpus = martwy, werdykt NIE powstaje")
    ap.add_argument("--only", help="uruchom tylko wskazanych czytelników (po przecinku)")
    ap.add_argument("--dry-run", action="store_true",
                    help="sprawdź korpusy i wypisz plan, nie uruchamiaj czytelników")
    args = ap.parse_args()

    day = _now().strftime("%Y-%m-%d")
    out_dir = os.path.join(args.out_dir, day)
    os.makedirs(out_dir, exist_ok=True)

    selected = READERS
    if args.only:
        want = {x.strip() for x in args.only.split(",") if x.strip()}
        selected = [r for r in READERS if r.name in want]
        unknown = want - {r.name for r in READERS}
        if unknown:
            print(f"NIEZNANI czytelnicy: {sorted(unknown)}", file=sys.stderr)
            return 2

    started = _now()
    results = [run_reader(r, out_dir, args.max_corpus_age_hours, args.dry_run) for r in selected]

    summary = {
        "generated_at": _iso(started),
        "finished_at": _iso(_now()),
        "out_dir": out_dir,
        "max_corpus_age_hours": args.max_corpus_age_hours,
        "dry_run": args.dry_run,
        "readers": results,
        "excluded": EXCLUDED,
        "contract": {
            "read_only": "zero zapisów poza out_dir",
            "telegram": "wyłączony bezwarunkowo (--no-telegram); czytelnik bez przełącznika nie jest wołany",
            "fail_closed": "brak/martwy/nieustalony korpus = brak werdyktu, nie cichy default",
        },
    }
    with open(os.path.join(out_dir, "summary.json"), "w") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)

    lines = [f"# Dobowy przegląd cienia — {day}", "",
             f"Start {summary['generated_at']} · koniec {summary['finished_at']} · "
             f"próg świeżości korpusu {args.max_corpus_age_hours} h", "",
             "| czytelnik | status | korpus (MB / wiek) | werdykt / powód |",
             "|---|---|---|---|"]
    for r in results:
        c = r.get("corpus") or {}
        cs = (f"{c.get('size_mb')} MB / {c.get('age_hours')} h"
              if c.get("size_mb") is not None else "—")
        what = r.get("verdict_line") or r.get("detail") or ""
        lines.append(f"| {r['name']} | {r['status']} | {cs} | {what.replace('|', '/')[:200]} |")
    lines += ["", "## Świadomie nieuruchamiane", ""]
    for e in EXCLUDED:
        lines.append(f"- **{e['name']}** ({e['unit']}) — {e['reason']}")
    lines += ["", f"Pełne wyjścia: `{out_dir}/<czytelnik>.log`, maszynowo `summary.json`."]
    with open(os.path.join(out_dir, "SUMMARY.md"), "w") as fh:
        fh.write("\n".join(lines) + "\n")

    print("\n".join(lines))
    bad = [r for r in results if r["status"] in ("FAILED", "TIMEOUT", "ERROR")]
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
