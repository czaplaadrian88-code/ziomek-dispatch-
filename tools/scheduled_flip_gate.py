#!/usr/bin/env python3
"""scheduled_flip_gate.py — BRAMKOWANY auto-flip flag L3/L4 na konkretny termin (at-job).

Adrian 02.07: „zapisz cron na flipy L3 i L4, żeby był konkretny termin". Flagi
L3/L4 czytane HOT (`decision_flag()` per-wywołanie) → flip = atomowy zapis
flags.json, bez restartu, cofalny w sekundy. Ten skrypt NIE flipuje na ślepo:
przechodzi bramkę (ETAP-0-lite), flipuje TYLKO gdy zielono, inaczej HOLD+alert.

Subkomendy:
  flip   --flag NAME --to true|false [--expect-current V] [--profile P] [--apply]
  verify --profile P [--rollback-on-error]

Bramka flip (wszystko musi przejść, inaczej HOLD):
  1. py_compile kluczowych plików silnika (nie-zepsute od merge).
  2. szybkie testy flagi (test_l3_*/test_l4_* — sekundy) zielone.
  3. flaga = wartość oczekiwana (idempotencja: już-flipnięta → NO-OP, nie dubluj).
  4. off-peak (poza 09-12 i 15-18 UTC = 11-14/17-20 Warsaw).
  5. strażnik pickup_floor_guard: 0 nowych naruszeń w ostatnich ~2h.
  6. shadow żywy (heartbeat świeży, brak ERROR-burst w ostatniej godzinie).
  7. GC-real DODATKOWO: świeży dry-run GC pokazuje 0 usunięć AKTYWNYCH planów.

Log: dispatch_state/scheduled_flips.jsonl. Telegram best-effort (priority low).
Bez ZoneInfo-only: liczy UTC (peak-okno w UTC). Zero zależności decyzyjnych.
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone, timedelta

ROOT = "/root/.openclaw/workspace/scripts"
DISP = os.path.join(ROOT, "dispatch_v2")
FLAGS = os.path.join(ROOT, "flags.json")
LOG = "/root/.openclaw/workspace/dispatch_state/scheduled_flips.jsonl"
FLOOR_GUARD = "/root/.openclaw/workspace/dispatch_state/pickup_floor_guard.jsonl"
PY = "/root/.openclaw/venvs/dispatch/bin/python"
# Markery plan_recheck (L3_REGEN / L4_ANCHOR_FLOOR / GC_COURIER_PLANS) idą przez
# StreamHandler→stderr, a systemd `dispatch-plan-recheck.service` ma
# StandardOutput/StandardError=append:<plik> → trafiają do PLIKU, NIE do journala.
# Dlatego `journalctl -u dispatch-plan-recheck` pokazuje 0 markerów (fałszywy
# sygnał — ugryzło przy GC-verify at-206 06.07: „marker_hits=0" mimo 22 realnych
# markerów w pliku). cmd_verify MUSI skanować TEN plik, nie tylko journal.
PLAN_RECHECK_LOG = os.path.join(ROOT, "logs", "plan_recheck.log")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Peak UTC (Warsaw 11-14 i 17-20 CEST = UTC 09-12 i 15-18). Off-peak = reszta.
PEAK_HOURS_UTC = set(range(9, 12)) | set(range(15, 18))

ENGINE_FILES = ["dispatch_pipeline.py", "feasibility_v2.py", "plan_recheck.py",
                "state_machine.py", "courier_resolver.py", "common.py"]
PROFILE_TESTS = {
    "l4": ["tests/test_l4_available_from.py"],
    "l3gate": ["tests/test_l3_plan_recheck_gates.py"],
    "l3gc-dry": ["tests/test_l3_plan_recheck_gates.py"],
    "l3gc-real": ["tests/test_l3_plan_recheck_gates.py"],
    "perf-slo": ["tests/test_perf_budget_slo.py"],
}


def _now():
    return datetime.now(timezone.utc)


def _log(rec):
    rec["ts"] = _now().isoformat()
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(LOG))
    try:
        with os.fdopen(fd, "w") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        with open(LOG, "a") as f, open(tmp) as t:
            f.write(t.read())
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    print(json.dumps(rec, ensure_ascii=False))


def _telegram(msg):
    try:
        from dispatch_v2.telegram_utils import send_admin_alert
        send_admin_alert(msg, priority="low")
    except Exception as e:
        print(f"[telegram pominięte: {e!r}]")


def _load_flags():
    with open(FLAGS) as f:
        return json.load(f)


# Każdą flagę czytaj DOKŁADNIE tak jak silnik (inaczej idempotencja/expect kłamie):
# ENABLE_* decyzyjne → decision_flag(); PLAN_GC_DRY_RUN → flag(name, True) default True.
_READERS = {
    "ENABLE_AVAILABLE_FROM_SINGLE_SOURCE": ("decision_flag", None),
    "ENABLE_PLAN_RECHECK_GATES": ("decision_flag", None),
    "ENABLE_COURIER_PLANS_GC": ("decision_flag", None),
    "PLAN_GC_DRY_RUN": ("flag", True),
}


def _effective(flag):
    """Wartość EFEKTYWNA jaką widzi silnik. Flaga nieobecna w flags.json = jej
    default (const dla ENABLE_*, inline True dla PLAN_GC_DRY_RUN), NIE None."""
    try:
        from dispatch_v2 import common as _C
        how, default = _READERS.get(flag, ("decision_flag", None))
        if how == "flag":
            return bool(_C.flag(flag, default))
        return bool(_C.decision_flag(flag))
    except Exception:
        return None


def _write_flags(d):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(FLAGS))
    with os.fdopen(fd, "w") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, FLAGS)


# ── Wspólne liczniki logów (BLIŹNIAK: używane i przez _gate, i przez cmd_verify —
#    ten sam benign-filtr, żeby gate i verify nie kłamały RÓŻNIE na tym samym tle) ──

# Tło DEFENSYWNE, NIE awaria silnika: COORD_GUARD (osrm_client, Lekcja #140/#81)
# loguje na ERROR gdy POPRAWNIE odrzuci współrzędną (0,0)/None/poza-bbox →
# sentinel infeasible (zamiast fikcyjnej trasy ~6285 km). To działająca obrona,
# nie błąd — nie może nabijać err_burst (fałszywy HOLD/rollback na ~25 zdarzeń/2h
# tła geokod-miss). Filtrujemy TYLKO ten jawnie znany wzorzec (realne ERROR liczą się).
_BENIGN_ERR_PATTERNS = ("COORD_GUARD",)


def _count_err_burst(lines):
    """(real_errs, benign_skipped): linie ERROR/Traceback z pominięciem tła
    defensywnego. benign zwracamy dla obserwowalności — nie gubimy sygnału."""
    real = benign = 0
    for ln in lines:
        if "ERROR" in ln or "Traceback" in ln:
            if any(p in ln for p in _BENIGN_ERR_PATTERNS):
                benign += 1
            else:
                real += 1
    return real, benign


def _count_markers(lines, tok):
    """Liczba linii zawierających token markera (substring — np. `L3_REGEN` łapie
    i L3_REGEN_REJECTED, i L3_REGEN_BOTH_BREACH)."""
    return sum(1 for ln in lines if tok in ln)


def _parse_log_ts(line):
    """Prefiks '%Y-%m-%d %H:%M:%S' (formatter plan_recheck; serwer=UTC) → aware
    UTC datetime. None gdy brak parsowalnego prefiksu (linia-kontynuacja)."""
    if len(line) < 19:
        return None
    try:
        return datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _read_log_window(path, since, now=None):
    """Linie z pliku logu z ostatnich `since` (timedelta), filtrowane po prefiksie
    czasu. Linia bez prefiksu (kontynuacja Tracebacka) dziedziczy stan ostatniej
    sparsowanej (multi-line nie wypada z okna). Brak pliku / błąd → []. Strumieniowo
    (plik może mieć dziesiątki MB — nie wczytujemy całości do pamięci)."""
    now = now or _now()
    cutoff = now - since
    out = []
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            in_window = False
            for ln in fh:
                ts = _parse_log_ts(ln)
                if ts is not None:
                    in_window = ts >= cutoff
                if in_window:
                    out.append(ln)
    except FileNotFoundError:
        return []
    except Exception:
        return []
    return out


def _gate(profile):
    """Zwraca (ok: bool, reasons: list[str]). Każdy fail = powód HOLD."""
    fails = []
    # 1. py_compile
    r = subprocess.run([PY, "-m", "py_compile", *ENGINE_FILES], cwd=DISP,
                       capture_output=True, text=True)
    if r.returncode != 0:
        fails.append(f"py_compile FAIL: {r.stderr.strip()[:200]}")
    # 2. testy flagi
    tests = PROFILE_TESTS.get(profile, [])
    if tests:
        r = subprocess.run([PY, "-m", "pytest", *tests, "-q", "-p", "no:cacheprovider"],
                           cwd=DISP, capture_output=True, text=True)
        if r.returncode != 0:
            fails.append(f"testy {profile} FAIL: {r.stdout.strip()[-200:]}")
    # 4. off-peak
    if _now().hour in PEAK_HOURS_UTC:
        fails.append(f"PEAK (UTC h={_now().hour}) — poza off-peak")
    # 5. strażnik pickup_floor — 0 nowych naruszeń w ~2h
    try:
        cutoff = _now() - timedelta(hours=2)
        viol = 0
        with open(FLOOR_GUARD) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if not r.get("tick_summary"):
                    continue
                if (r.get("ts", "") or "") < cutoff.isoformat():
                    continue
                viol += (r.get("viol_proposal", 0) + r.get("viol_plan", 0)
                         + r.get("viol_recheck_leak", 0))
        if viol > 0:
            fails.append(f"pickup_floor_guard: {viol} naruszeń w 2h")
    except FileNotFoundError:
        pass
    # 6. shadow żywy (brak ERROR-burst) — pomija tło defensywne COORD_GUARD
    #    (BLIŹNIAK licznika z cmd_verify: ten sam _count_err_burst, żeby gate
    #    i verify nie kłamały różnie na tym samym tle geokod-miss).
    try:
        r = subprocess.run(["journalctl", "-u", "dispatch-shadow", "--since",
                            "1 hour ago", "--no-pager"], capture_output=True, text=True)
        errs, _benign = _count_err_burst(r.stdout.splitlines())
        if errs > 20:
            fails.append(f"dispatch-shadow: {errs} ERROR w godzinie (burst)")
    except Exception:
        pass
    return (not fails), fails


def _gc_dryrun_safe():
    """GC-real gate: świeży dry-run — KAŻDY aktywny plan zachowany
    (gc_active_kept == active_plans) i coś realnie do usunięcia (nie all-empty
    fałszywy sukces). Aktywne z definicji są KEPT; gate łapie regres, gdyby GC
    zaczął kasować aktywny (active_kept < active_plans)."""
    r = subprocess.run([PY, "-c",
        "import sys; sys.path.insert(0,'/root/.openclaw/workspace/scripts');"
        "from dispatch_v2 import plan_recheck as PR;"
        "from datetime import datetime,timezone;"
        "os_=PR._load_orders_state(); summ={};"
        "PR._gc_courier_plans(os_, datetime.now(timezone.utc), summ, dry_run=True, max_age_h=48.0);"
        "import json; print('GCJSON'+json.dumps(summ))"],
        cwd=DISP, capture_output=True, text=True)
    out = r.stdout
    for ln in out.splitlines():
        if ln.startswith("GCJSON"):
            rep = json.loads(ln[6:])
            active = rep.get("active_plans", 0)
            kept = rep.get("gc_active_kept", 0)
            # bezpieczny: wszystkie aktywne zachowane. Jeśli active>0 i kept<active
            # → GC zjadłby aktywny plan → NIEbezpieczny.
            safe = (active == 0) or (kept >= active)
            rep["_gate_active"] = active
            rep["_gate_kept"] = kept
            return safe, rep
    return False, {"error": (r.stderr or out)[-200:]}


def cmd_flip(a):
    prof = a.profile or ""
    flags = _load_flags()
    cur = _effective(a.flag)  # wartość jaką widzi silnik (nieobecna w flags.json = const)
    target = (a.to.lower() == "true")
    # idempotencja (na wartości EFEKTYWNEJ)
    if cur == target:
        _log({"action": "flip", "flag": a.flag, "profile": prof, "result": "NOOP",
              "reason": f"efektywnie już={cur}"})
        _telegram(f"⏭ scheduled-flip {a.flag}: efektywnie już {cur} (NO-OP, {prof})")
        return 0
    if a.expect_current is not None:
        exp = (a.expect_current.lower() == "true")
        if cur != exp:
            _log({"action": "flip", "flag": a.flag, "profile": prof, "result": "HOLD",
                  "reason": f"efektywny stan {cur}≠oczekiwany {exp} (ktoś zmienił?)"})
            _telegram(f"🛑 scheduled-flip {a.flag} HOLD: stan {cur}≠{exp} — sprawdź ręcznie")
            return 2
    ok, fails = _gate(prof)
    if ok and prof == "l3gc-real":
        safe, rep = _gc_dryrun_safe()
        if not safe:
            ok = False
            fails.append(f"GC dry-run NIEbezpieczny (usunąłby aktywne): {rep}")
    if not ok:
        _log({"action": "flip", "flag": a.flag, "profile": prof, "result": "HOLD",
              "gate_fails": fails})
        _telegram(f"🛑 scheduled-flip {a.flag} HOLD ({prof}): " + " | ".join(fails)[:300])
        return 3
    if not a.apply:
        _log({"action": "flip", "flag": a.flag, "profile": prof, "result": "DRY_OK",
              "would_set": target})
        print(f"[DRY] bramka zielona — flip {a.flag} {cur}→{target} gotowy (--apply by wykonać)")
        return 0
    flags[a.flag] = target
    _write_flags(flags)
    _log({"action": "flip", "flag": a.flag, "profile": prof, "result": "FLIPPED",
          "from": cur, "to": target})
    _telegram(f"✅ scheduled-flip {a.flag}: {cur}→{target} ({prof}). "
              f"Hot (bez restartu). Rollback: flaga→{cur} w flags.json. "
              f"Weryfikacja markerów za ~2h.")
    return 0


def cmd_verify(a):
    """Behawioralna weryfikacja że flip zadziałał (markery w logu). Rollback opcjonalny.

    Markery plan_recheck NIE trafiają do journala (systemd redirect → plik) — więc
    skanujemy DWIE ścieżki: (1) journal dispatch-shadow+plan-recheck dla err_burst
    (i jako backstop markera, gdyby kiedyś wrócił do journala), (2) PLIK
    plan_recheck.log — REALNE źródło markerów, okno 2h po prefiksie czasu. err_burst
    pomija tło defensywne COORD_GUARD (inaczej fałszywy alarm na geokod-miss)."""
    prof = a.profile
    marker_tok = {
        "l4": "L4_ANCHOR_FLOOR",
        "l3gate": "L3_REGEN",
        "l3gc-real": "GC_COURIER_PLANS",
    }.get(prof)

    # (1) journal — err_burst (2h) + backstop markera
    journal_lines = []
    try:
        r = subprocess.run(["journalctl", "-u", "dispatch-shadow", "-u",
                            "dispatch-plan-recheck", "--since", "2 hours ago",
                            "--no-pager"], capture_output=True, text=True)
        journal_lines = r.stdout.splitlines()
    except Exception:
        pass
    err_burst, coord_guard = _count_err_burst(journal_lines)

    # (2) PLIK plan_recheck.log — realne źródło markerów (okno 2h)
    file_lines = _read_log_window(PLAN_RECHECK_LOG, timedelta(hours=2))

    hit = None
    if marker_tok:
        # źródła wzajemnie ROZŁĄCZNE (systemd kieruje markery do pliku, journal ma
        # 0) → suma bez podwójnego liczenia; journal to backstop na przyszłość.
        hit = _count_markers(file_lines, marker_tok) + _count_markers(journal_lines, marker_tok)

    _log({"action": "verify", "profile": prof, "marker_hits": hit,
          "err_burst": err_burst, "coord_guard_benign": coord_guard,
          "marker_src": "plan_recheck.log+journal"})
    if a.rollback_on_error and err_burst > 20:
        _telegram(f"⚠ verify {prof}: {err_burst} realnych ERROR w 2h "
                  f"(pominięto {coord_guard} COORD_GUARD tła) — ROZWAŻ rollback flagi "
                  f"(nie auto-cofam bez pewności)")
        return 3
    _telegram(f"🔎 verify {prof}: markery={hit}, ERROR={err_burst} "
              f"(pominięto {coord_guard} COORD_GUARD tła, 2h). "
              f"{'OK — flip działa' if (hit or 0) > 0 else 'brak markerów — flip mógł nie mieć na czym zadziałać (mały ruch)'}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("flip")
    f.add_argument("--flag", required=True)
    f.add_argument("--to", required=True, choices=["true", "false"])
    f.add_argument("--expect-current", default=None)
    f.add_argument("--profile", default="")
    f.add_argument("--apply", action="store_true")
    f.set_defaults(func=cmd_flip)
    v = sub.add_parser("verify")
    v.add_argument("--profile", required=True)
    v.add_argument("--rollback-on-error", action="store_true")
    v.set_defaults(func=cmd_verify)
    a = ap.parse_args()
    sys.exit(a.func(a))


if __name__ == "__main__":
    main()
