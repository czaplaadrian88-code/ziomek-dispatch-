#!/usr/bin/env python3
"""flag_fingerprint_guard — STRAŻNIK-TIMER rekoncyliacji flag (READ-ONLY, staged).

Robi z jednorazowego `flag_fingerprint_check` (4-źródłowa rekoncyliacja:
flags.json ↔ FLAG_FINGERPRINT w logach ↔ Environment= drop-inów ↔ rejestr)
periodyczny detektor. ZERO wpływu na decyzje — jak carried_first_guard /
pickup_floor_guard: plik JSONL + log, alert edge-triggered za flagą.

Trzy poziomy werdyktu:
  OK    — brak rozjazdów istotnych (benign COVERAGE-GAP/JSON-DRIFT/REGISTRY-ONLY
          = stale-process/benign-snapshot, samo-goją się przy restarcie → LOG,
          NIE alert),
  DRIFT — rozjazd ŹRÓDEŁ nie samo-gojący: VALUE-MISMATCH (env-frozen różny między
          procesami) lub ENV-DEAD (decyzyjna flaga z martwym Environment= w unicie,
          wzorzec #9). Alert-worthy.
  COLD  — INTERMITTENT-COLD (proc emituje common.py DEFAULTY zamiast flags.json)
          POTWIERDZONY korelacją z journalem serwisu. Najwyższy priorytet.

⚠ KRYTYCZNA LEKCJA (ledger §20, wzorzec #10): naiwny odczyt PROD-loga KŁAMIE —
testy pytest z conftest-owo ODARTYM flags.json pisały cold-linie do żywego loga
przez module-level `setup_logger(..., PROD path)`. Fix U ŹRÓDŁA już jest (guard
`DISPATCH_UNDER_PYTEST` w common.setup_logger), ALE strażnik i tak MUSI korelować:
zanim uzna „COLD", sprawdza czy cold-linie w PLIKU logu padły W OKNIE FAKTYCZNEGO
biegu serwisu (`journalctl -u <unit>` Starting→Deactivated). Cold POZA oknem biegu
= OBCY proces (pytest) → status UNVERIFIED, ZERO alertu. Journal niedostępny →
UNVERIFIED (gracefully-degrade, nie alertuj). COLD tylko gdy ≥1 cold-linia leży
w oknie realnego ticku serwisu.

Kanał: `dispatch_state/flag_fingerprint_guard.jsonl` (atomic append) + log.
Stan edge-triggera: `dispatch_state/flag_fingerprint_guard_state.json` (atomic).
Alert Telegram: TYLKO gdy flaga `ENABLE_FLAG_FINGERPRINT_GUARD_ALERT` (env override
→ flags.json, DOMYŚLNIE OFF) — na start LOG-ONLY. Edge-triggered (wzór
objm_lexr6_canary_monitor `_notify_decision`): alert przy WEJŚCIU/eskalacji rozjazdu
i przy POWROCIE do normy; nie co tick.

Uruchomienie: `python3 -m dispatch_v2.tools.flag_fingerprint_guard [--dry] [--json]`
(--dry = nie zapisuj JSONL/stanu ani nie wysyłaj; tylko wypisz werdykt).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "/root/.openclaw/workspace/scripts")
from dispatch_v2.tools import flag_fingerprint_check as ffc  # noqa: E402

DISPATCH_STATE = "/root/.openclaw/workspace/dispatch_state"
GUARD_LOG = os.path.join(DISPATCH_STATE, "flag_fingerprint_guard.jsonl")
STATE_PATH = os.path.join(DISPATCH_STATE, "flag_fingerprint_guard_state.json")
ALLOWLIST_PATH = os.path.join(DISPATCH_STATE, "flag_fingerprint_guard_allowlist.json")

ALERT_FLAG = "ENABLE_FLAG_FINGERPRINT_GUARD_ALERT"

# Rozjazdy ŹRÓDEŁ nie samo-gojące (alert-worthy). VALUE-MISMATCH = env-frozen różny
# między procesami; ENV-DEAD = decyzyjna z martwym Environment= (wzorzec #9). Reszta
# klas (COVERAGE-GAP/JSON-DRIFT/REGISTRY-ONLY) = stale/benign → LOG, nie alert.
DRIFT_CLASSES = frozenset({"VALUE-MISMATCH", "ENV-DEAD"})
BENIGN_CLASSES = frozenset({"JSON-DRIFT", "COVERAGE-GAP", "REGISTRY-ONLY"})
COLD_CLASS = "INTERMITTENT-COLD"

# Okno wstecz na cold-linie w pliku + na journal serwisu.
COLD_LOOKBACK_MIN = int(os.environ.get("FFG_COLD_LOOKBACK_MIN", "60"))
# Tolerancja dopasowania cold-linii do okna biegu serwisu (Starting→Deactivated).
# Bieg oneshot trwa ~1s, fingerprint logowany w jego trakcie; ±kilka s absorbuje
# rozjazd „emit vs Finished". MAŁA celowo — luźna tolerancja fałszywie potwierdza
# obce (pytest) cold-linie (anty-§20). NIE podnosić bez świadomej decyzji.
JOURNAL_WINDOW_TOL_S = float(os.environ.get("FFG_JOURNAL_TOL_S", "5"))
# Gdy Starting nie ma sparowanego terminala w oknie — max długość biegu.
JOURNAL_MAX_RUN_S = 120.0
# Rzadkie przypomnienie o utrzymującym się rozjeździe (edge-trigger remind).
REMIND_H = float(os.environ.get("FFG_REMIND_H", "6.0"))

_LOG_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _parse_log_ts(line: str) -> Optional[datetime]:
    """Timestamp linii logu (UTC — zweryfikowane: log-ts == journal +00:00)."""
    m = _LOG_TS.match(line)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        return None


# ── I/O wrappery (monkeypatch-owalne w testach — zero PROD reads w suite) ─────
def _read_log_lines(proc: str) -> List[str]:
    log = ffc.SERVICES[proc][0]
    path = os.path.join(ffc.LOGS_DIR, log)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.readlines()
    except OSError:
        return []


def _run_journalctl(unit: str, since_dt: datetime) -> Optional[str]:
    """Stdout `journalctl -u unit --since ... -o short-iso` (READ-ONLY). None gdy
    journal niedostępny/pusty → wywołujący degraduje do UNVERIFIED."""
    since = since_dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    try:
        r = subprocess.run(
            ["journalctl", "-u", unit, "--since", since, "-o", "short-iso", "--no-pager"],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:
        return None
    out = r.stdout or ""
    return out if out.strip() else None


# ── korelacja cold ↔ journal (anty-§20) ──────────────────────────────────────
def _cold_line_timestamps(proc: str, fjson: dict, bool_flags: set,
                          since_dt: datetime, limit: int = 2000) -> List[datetime]:
    """Timestampy linii FLAG_FINGERPRINT proc w oknie [since_dt, teraz], których
    fingerprint = wholesale cold (≥COLD_DRIFT_MIN flag ≠ flags.json)."""
    out: List[datetime] = []
    for line in _read_log_lines(proc)[-limit:]:
        if "FLAG_FINGERPRINT" not in line or f"proc={proc}" not in line:
            continue
        ts = _parse_log_ts(line)
        if ts is None or ts < since_dt:
            continue
        body = line.split("FLAG_FINGERPRINT", 1)[1]
        fp = dict(ffc._FP_KV.findall(body))
        if ffc._drift_count(fp, fjson, bool_flags) >= ffc.COLD_DRIFT_MIN:
            out.append(ts)
    return out


def _service_intervals(unit: str, since_dt: datetime) -> Optional[List[Tuple[datetime, datetime]]]:
    """Okna faktycznych biegów serwisu z journala: [Starting, Deactivated/Finished].
    None = journal niedostępny (degrade). [] = journal jest, ale zero biegów w oknie."""
    text = _run_journalctl(unit, since_dt)
    if text is None:
        return None
    _TERMINAL = ("Deactivated successfully", "Finished ", "Failed", "Main process exited")
    starts: List[datetime] = []
    ends: List[datetime] = []
    for line in text.splitlines():
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        ts = _parse_iso(parts[0])
        if ts is None:
            continue
        rest = parts[1]
        if "Starting" in rest:
            starts.append(ts.astimezone(timezone.utc))
        elif any(t in rest for t in _TERMINAL):
            ends.append(ts.astimezone(timezone.utc))
    intervals: List[Tuple[datetime, datetime]] = []
    ends_sorted = sorted(ends)
    for s in sorted(starts):
        end = next((e for e in ends_sorted if e >= s), None)
        if end is None or (end - s).total_seconds() > JOURNAL_MAX_RUN_S:
            end = s + timedelta(seconds=JOURNAL_MAX_RUN_S)
        intervals.append((s, end))
    return intervals


def _in_any_interval(ts: datetime, intervals: List[Tuple[datetime, datetime]],
                     tol_s: float) -> bool:
    tol = timedelta(seconds=tol_s)
    for s, e in intervals:
        if (s - tol) <= ts <= (e + tol):
            return True
    return False


def confirm_cold(proc: str, since_dt: datetime, fjson: dict, bool_flags: set) -> Dict[str, Any]:
    """Werdykt czy INTERMITTENT-COLD proc pochodzi od SERWISU (anty-§20).

    CONFIRMED  — ≥1 cold-linia w PLIKU leży w oknie realnego biegu serwisu.
    UNVERIFIED — journal niedostępny LUB wszystkie cold-linie POZA oknami biegu
                 (obcy proces, np. pytest) → NIE alertuj.
    NO_RECENT_COLD — reconcile widzi cold, ale w oknie lookback brak cold-linii
                 (stary blip) → nie alertuj.
    """
    cold_ts = _cold_line_timestamps(proc, fjson, bool_flags, since_dt)
    if not cold_ts:
        return {"status": "NO_RECENT_COLD", "n_cold": 0}
    unit = ffc.SERVICES[proc][1]
    intervals = _service_intervals(unit, since_dt)
    if intervals is None:
        return {"status": "UNVERIFIED", "reason": "journal_unavailable", "n_cold": len(cold_ts)}
    confirmed = [t for t in cold_ts if _in_any_interval(t, intervals, JOURNAL_WINDOW_TOL_S)]
    if confirmed:
        return {"status": "CONFIRMED", "n_cold": len(cold_ts),
                "n_confirmed": len(confirmed), "n_service_runs": len(intervals)}
    return {"status": "UNVERIFIED", "reason": "cold_outside_service_windows",
            "n_cold": len(cold_ts), "n_service_runs": len(intervals)}


# ── allowlista znanych/zaakceptowanych rozjazdów ─────────────────────────────
def _load_allowlist() -> set:
    """Zbiór tokenów zaakceptowanych rozjazdów. Token pasuje gdy == `flag` LUB
    == `klass:flag` findingu. Seed = znane-otwarte z ledger §4."""
    accepted = {"USE_V2_PARSER"}  # known-open, migracja ETAP4 za ACK (ledger §4 pkt 2)
    try:
        with open(ALLOWLIST_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        for tok in data.get("accepted", []):
            accepted.add(str(tok))
    except Exception:
        pass
    return accepted


def _is_allowlisted(klass: str, flag: str, allow: set) -> bool:
    return flag in allow or f"{klass}:{flag}" in allow


# ── ewaluacja (czysta względem I/O reconcile) ────────────────────────────────
def evaluate(now: Optional[datetime] = None, since_min: Optional[int] = None) -> Dict[str, Any]:
    now = now or _now()
    since = now - timedelta(minutes=since_min if since_min is not None else COLD_LOOKBACK_MIN)
    res = ffc.reconcile()
    fjson = ffc.load_flags_json()
    bool_flags, _numeric = ffc._decision_flags()
    allow = _load_allowlist()

    drift_alertable: List[dict] = []
    cold_reports: List[dict] = []
    benign: List[dict] = []
    accepted: List[dict] = []

    for it in res["findings"]:
        klass, flag = it["klass"], it["flag"]
        if klass == COLD_CLASS:
            proc = flag.split(":", 1)[1] if flag.startswith("proc:") else flag
            conf = confirm_cold(proc, since, fjson, bool_flags)
            rep = {"klass": klass, "flag": flag, "proc": proc, "confirm": conf}
            if _is_allowlisted(klass, flag, allow):
                rep["accepted"] = True
                accepted.append(rep)
            else:
                cold_reports.append(rep)
        elif klass in DRIFT_CLASSES:
            if _is_allowlisted(klass, flag, allow):
                accepted.append(it)
            else:
                drift_alertable.append(it)
        else:
            benign.append(it)

    confirmed_cold = [r for r in cold_reports if r["confirm"]["status"] == "CONFIRMED"]
    if confirmed_cold:
        level = "COLD"
    elif drift_alertable:
        level = "DRIFT"
    else:
        level = "OK"

    sig_items = sorted([f"COLD:{r['flag']}" for r in confirmed_cold]
                       + [f"{it['klass']}:{it['flag']}" for it in drift_alertable])
    signature = f"{level}|" + ";".join(sig_items)

    return {
        "ts": now.isoformat(),
        "level": level,
        "signature": signature,
        "procs_live": res["procs_live"],
        "procs_dead": res["procs_dead"],
        "fingerprint_sizes": res["fingerprint_sizes"],
        "counts": {
            "drift_alertable": len(drift_alertable),
            "confirmed_cold": len(confirmed_cold),
            "cold_unverified": len([r for r in cold_reports if r["confirm"]["status"] != "CONFIRMED"]),
            "benign": len(benign),
            "accepted": len(accepted),
        },
        "drift": drift_alertable,
        "cold": cold_reports,
        "benign_sample": [{"klass": b["klass"], "flag": b["flag"]} for b in benign[:6]],
    }


# ── edge-trigger (wzór objm_lexr6_canary_monitor._notify_decision) ───────────
def _detail_str(record: Dict[str, Any]) -> str:
    parts = []
    for r in record["cold"]:
        if r["confirm"]["status"] == "CONFIRMED":
            parts.append(f"COLD {r['proc']} (cold={r['confirm'].get('n_confirmed')}/"
                         f"{r['confirm'].get('n_cold')} w oknie biegu)")
    for it in record["drift"]:
        parts.append(f"{it['klass']} {it['flag']}")
    return "; ".join(parts) or "(brak)"


def notify_decision(level: str, signature: str, detail: str, prev: Dict[str, Any],
                    now: datetime, remind_after: Optional[timedelta]
                    ) -> Tuple[bool, Optional[str], Dict[str, Any]]:
    """Pure (testowalne): czy wysłać (edge-triggered) + treść + stan-jeśli-wysłano.

    OK: alert TYLKO gdy poprzednio DRIFT/COLD (powrót do normy), raz.
    DRIFT/COLD: alert gdy sygnatura ≠ poprzednia (nowy/eskalacja), albo gdy ten sam
      stan trwa dłużej niż remind_after (rzadkie przypomnienie). Inaczej cisza.
    """
    prev_sig = prev.get("signature")
    prev_level = prev.get("level")
    prev_sent_raw = prev.get("last_sent")
    prev_sent = _parse_iso(prev_sent_raw)
    send, msg = False, None
    if level == "OK":
        if prev_level and prev_level != "OK":
            send = True
            msg = f"🟢 FLAG-FINGERPRINT GUARD OK — rozjazdy/cold ustąpiły (był {prev_level})"
    else:
        head = ("🔴 FLAG-FINGERPRINT COLD (potwierdzony journalem serwisu)"
                if level == "COLD" else "🟠 FLAG-FINGERPRINT DRIFT (rozjazd źródeł)")
        if signature != prev_sig:
            send, msg = True, f"{head} | {detail}"
        elif remind_after and (prev_sent is None or (now - prev_sent) >= remind_after):
            hh = remind_after.total_seconds() / 3600.0
            send, msg = True, f"{head} (nadal >{hh:.0f}h) | {detail}"
    new_sent = now.isoformat() if send else prev_sent_raw
    return send, msg, {"signature": signature, "level": level, "last_sent": new_sent}


def _alert_enabled() -> bool:
    """Flaga alertu (DOMYŚLNIE OFF). Env `ENABLE_FLAG_FINGERPRINT_GUARD_ALERT`
    nadpisuje flags.json (wzór _perf_slo_enabled)."""
    env = os.environ.get(ALERT_FLAG)
    if env is not None:
        return env.strip().lower() in ("1", "true", "yes", "on")
    try:
        return bool(json.load(open(ffc.FLAGS_JSON)).get(ALERT_FLAG, False))
    except Exception:
        return False


def _load_state() -> Dict[str, Any]:
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def _save_state(d: Dict[str, Any]) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(STATE_PATH), prefix=".ffg_state_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, STATE_PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def _append_jsonl(record: Dict[str, Any]) -> None:
    with open(GUARD_LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def run(write: bool = True, now: Optional[datetime] = None) -> Dict[str, Any]:
    record = evaluate(now=now)
    now_dt = _parse_iso(record["ts"]) or _now()
    prev = _load_state()
    remind = timedelta(hours=REMIND_H)
    want, msg, cand_state = notify_decision(
        record["level"], record["signature"], _detail_str(record), prev, now_dt, remind)
    armed = _alert_enabled()
    sent = False
    if write:
        _append_jsonl(record)
    if want and armed:
        try:
            from dispatch_v2.telegram_utils import send_admin_alert
            sent = bool(send_admin_alert(
                msg, source="flag_fingerprint_guard",
                priority="high" if record["level"] == "COLD" else None))
        except Exception as e:  # noqa: BLE001 — alert nigdy nie wywala strażnika
            print(f"[flag_fingerprint_guard] notify pominięte: {e!r}")
            sent = False
        # przesuwaj zegar remind TYLKO gdy realnie wysłano
        new_state = cand_state if sent else {
            "signature": record["signature"], "level": record["level"],
            "last_sent": prev.get("last_sent")}
    else:
        # log-only / brak wysyłki: śledź sygnaturę+poziom, NIE przesuwaj last_sent
        new_state = {"signature": record["signature"], "level": record["level"],
                     "last_sent": prev.get("last_sent")}
    if write:
        try:
            _save_state(new_state)
        except Exception as e:  # noqa: BLE001
            print(f"[flag_fingerprint_guard] zapis stanu pominięty: {e!r}")
    record["alert"] = {"enabled": armed, "would_send": want, "sent": sent, "msg": msg}
    return record


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true", help="nie zapisuj JSONL/stanu, nie wysyłaj")
    ap.add_argument("--json", action="store_true", help="wypisz pełny rekord JSON")
    args = ap.parse_args()
    record = run(write=not args.dry)
    if args.json:
        print(json.dumps(record, ensure_ascii=False, indent=2))
    else:
        c = record["counts"]
        print(f"[flag_fingerprint_guard] level={record['level']} "
              f"drift={c['drift_alertable']} cold_confirmed={c['confirmed_cold']} "
              f"cold_unverified={c['cold_unverified']} benign={c['benign']} "
              f"accepted={c['accepted']} alert_enabled={record['alert']['enabled']} "
              f"would_send={record['alert']['would_send']} sent={record['alert']['sent']}")
        if record["drift"]:
            print("  DRIFT:", _detail_str(record))
        for r in record["cold"]:
            print(f"  COLD {r['proc']}: {r['confirm']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
