"""DEAD-MAN alert path — niezależny czuwak nad SPRAWNOŚCIĄ alertowania (Z0-3).

Problem (audyt 2026-08-02): podstawowy stos alertów MP-#4
(`alert_onfailure` + `watchdog`) wysyła WSZYSTKO przez
`telegram_utils.send_admin_alert` → GŁÓWNY bot dispatch (`TELEGRAM_BOT_TOKEN`),
który jest CELOWO wyciszony od 26.06 (NIE wskrzeszać). Skutek: realne awarie
(`code-offsite-sync` padał 30.07–02.08, `dispatch-night-guard` failuje) nie
docierają do nikogo, bo cały ich kanał dostawy jest martwy. Ten sam kanał
obsługuje wykrywanie i dostawę — jak kanał leży, cisza jest niewidzialna.

Ten moduł to OSOBNA warstwa („kto pilnuje pilnującego"): NIE dzieli kanału
z wyciszonym botem. Czyta prawdę wprost z systemd + heartbeatów podstawowego
stosu i eskaluje NIEZALEŻNYM kanałem (@DajeszBot / assistant-telegram —
inny bot, inny proces, inna polityka; ODR-006/OD-6). Mail/SMTP na hoście
niedostępny (brak MTA), więc kanałem niezależnym jest push Telegram
przez bezpośredni HTTPS, z pominięciem `telegram_utils`/`notify_router`.

Granice (kanoniczny owner kontraktu):
  - Podstawowy kanał alertów = `alert_onfailure`/`watchdog` (piszą cron_health,
    wysyłają na główny bot). TEN moduł ich NIE zastępuje i NIE pisze do
    cron_health — tylko CZYTA ich heartbeat i systemd. Zero konkurencyjnych
    writerów tej samej prawdy.
  - Dostawa: TYLKO niezależny kanał (poniżej). Nigdy `telegram_utils`.

Shadow-first: DRY-RUN domyślnie WŁĄCZONY (env `DEADMAN_DRY_RUN` != "0" oraz
`DEADMAN_ENABLED` != "1"). W DRY-RUN moduł loguje CO by wysłał do
`deadman_watchdog_shadow.jsonl` i NIE robi żadnego I/O sieciowego.

Aktywacja (osobny ACK ownera — patrz deadman_watchdog.service.example):
  DEADMAN_ENABLED=1 i DEADMAN_DRY_RUN=0 + EnvironmentFile z tokenem.

Sekrety: moduł czyta NAZWY zmiennych z env (DEADMAN_ALERT_TOKEN /
ASSISTANT_TELEGRAM_TOKEN, DEADMAN_ALERT_CHAT_ID / ASSISTANT_TELEGRAM_ADMIN_ID).
NIGDY nie loguje wartości tokena/chat_id.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)

# --- Ścieżki (module-level default; funkcje robią runtime lookup — monkeypatch/HERMETIC-friendly) ---
STATE_DIR = Path("/root/.openclaw/workspace/dispatch_state")
DEADMAN_STATE_PATH = STATE_DIR / "deadman_watchdog_state.json"
DEADMAN_SHADOW_LOG = STATE_DIR / "deadman_watchdog_shadow.jsonl"
NIGHT_GUARD_HISTORY = STATE_DIR / "night_guard_history.jsonl"
CRON_HEALTH_PATH = STATE_DIR / "cron_health.json"

# --- Progi (env-tunable) ---
DEFAULT_COOLDOWN_H = 6.0            # nie spamuj tej samej awarii częściej niż co 6h
DEFAULT_NIGHT_GUARD_MAX_SILENCE_H = 26.0   # nocny strażnik: dobowy + margines
DEFAULT_PRIMARY_ALERT_MAX_SILENCE_H = 24.0  # jak długo brak alertu = kanał niemy

# Jednostki, których stan failed/inactive jest ŚWIADOMĄ decyzją ownera — NIE alarmują.
# Każdy wpis = uzasadnienie. Rozszerzalne przez env DEADMAN_SUPPRESS_UNITS (CSV, additive).
KNOWN_INTENTIONAL = {
    # dispatch-telegram wyciszony 26.06 (NIE wskrzeszać). Zwykle 'inactive', nie 'failed',
    # więc i tak nie pojawi się w --failed; trzymamy jawnie na wypadek stanu failed.
    "dispatch-telegram.service",
}

# Jednostki krytyczne dla operacji — ich failed ZAWSZE eskaluje (o ile nie w suppress).
# Pusty zbiór = traktuj każdą nie-suppress failed jako krytyczną. Zbiór zawężający, gdy
# owner zechce ograniczyć zakres do wskazanych jednostek (env DEADMAN_CRITICAL_UNITS CSV).
DEFAULT_CRITICAL_PREFIXES = ("dispatch-", "code-offsite-sync", "cto-", "assistant-", "papu-", "mailek-")


@dataclass
class Finding:
    kind: str                       # failed_unit | night_guard_stale | primary_health_unreadable
    key: str                        # klucz dedup/cooldown (stabilny per problem)
    severity: str                   # CRITICAL | P1 | P2
    summary: str                    # jedna linia do wiadomości
    detail: dict = field(default_factory=dict)


# ----------------------------- boundary: systemd -----------------------------
def _run_systemctl_failed() -> list[str] | None:
    """Zwraca listę nazw failed unitów (systemctl --failed). None = systemctl niedostępny.

    Granica systemd — testy monkeypatchują TĘ funkcję (żaden test nie dotyka realnego systemd).
    """
    try:
        r = subprocess.run(
            ["systemctl", "--failed", "--no-legend", "--plain", "--no-pager"],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as e:
        log.warning("deadman: systemctl --failed niedostępny: %s", e)
        return None
    if r.returncode not in (0, 1):  # 1 gdy są failed units (nadal poprawny output)
        log.warning("deadman: systemctl --failed rc=%s", r.returncode)
    units: list[str] = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # format: "UNIT LOAD ACTIVE SUB DESCRIPTION"; pierwsza kolumna = unit
        first = line.split()[0]
        if first.endswith(".service") or first.endswith(".timer") or "." in first:
            units.append(first)
    return units


# ----------------------------- helpers: env/config -----------------------------
def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _suppress_set() -> set[str]:
    s = set(KNOWN_INTENTIONAL)
    extra = os.getenv("DEADMAN_SUPPRESS_UNITS", "")
    for u in extra.split(","):
        u = u.strip()
        if u:
            s.add(u)
    return s


def _critical_prefixes() -> tuple[str, ...]:
    raw = os.getenv("DEADMAN_CRITICAL_UNITS", "")
    items = tuple(x.strip() for x in raw.split(",") if x.strip())
    return items or DEFAULT_CRITICAL_PREFIXES


def _is_critical(unit: str) -> bool:
    return any(unit.startswith(p) or unit == p or unit.startswith(p + ".") for p in _critical_prefixes())


def _is_dry_run() -> bool:
    """DRY-RUN gdy nie jest jawnie i explicite włączona realna wysyłka.

    Realna wysyłka WYMAGA: DEADMAN_ENABLED=1 ORAZ DEADMAN_DRY_RUN in {0,false,no}.
    Domyślnie (brak envów) → True (shadow-first).
    """
    enabled = os.getenv("DEADMAN_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")
    dry = os.getenv("DEADMAN_DRY_RUN", "1").strip().lower() not in ("0", "false", "no", "off")
    return dry or not enabled


# ----------------------------- helpers: readers -----------------------------
def _parse_iso(raw: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError, AttributeError):
        return None


def _night_guard_last_ts(path: Path | None = None) -> datetime | None:
    p = Path(path if path is not None else os.getenv("DEADMAN_NIGHT_GUARD_PATH", str(NIGHT_GUARD_HISTORY)))
    try:
        last = None
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    last = line
        if last is None:
            return None
        rec = json.loads(last)
        return _parse_iso(rec.get("ts", ""))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _cron_health_last_alert(unit: str, path: Path | None = None) -> datetime | None:
    """Kiedy PODSTAWOWY kanał ostatnio zaraportował alert dla tej jednostki.

    None = brak wpisu / brak alertu → dowód, że awaria nie została zaalarmowana.
    Rzuca FileNotFoundError-owo? Nie: zwraca None. Nieczytelny plik obsłużony w run().
    """
    p = Path(path if path is not None else os.getenv("DEADMAN_CRON_HEALTH_PATH", str(CRON_HEALTH_PATH)))
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    entry = (data.get("units") or {}).get(unit) or {}
    ts = entry.get("last_alert_ts")
    return _parse_iso(ts) if ts else None


def _cron_health_readable(path: Path | None = None) -> bool:
    p = Path(path if path is not None else os.getenv("DEADMAN_CRON_HEALTH_PATH", str(CRON_HEALTH_PATH)))
    try:
        json.loads(p.read_text(encoding="utf-8"))
        return True
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False


# ----------------------------- state (own cooldown ledger) -----------------------------
def _state_path() -> Path:
    return Path(os.getenv("DEADMAN_STATE_PATH", str(DEADMAN_STATE_PATH)))


def _load_state() -> dict:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"alerts": {}}


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".deadman-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _cooldown_active(key: str, now: datetime, cooldown_h: float, state: dict) -> bool:
    ts = (state.get("alerts") or {}).get(key)
    dt = _parse_iso(ts) if ts else None
    if dt is None:
        return False
    return (now - dt).total_seconds() < cooldown_h * 3600.0


def _record_alerted(keys: list[str], now: datetime, state: dict) -> None:
    alerts = state.setdefault("alerts", {})
    for k in keys:
        alerts[k] = now.isoformat()
    _atomic_write_json(_state_path(), state)


# ----------------------------- detection -----------------------------
def collect_findings(
    now: datetime,
    *,
    night_guard_max_silence_h: float,
    primary_alert_max_silence_h: float,
) -> list[Finding]:
    findings: list[Finding] = []
    suppress = _suppress_set()

    # 1) failed units (ground truth) + korelacja z ciszą podstawowego kanału
    failed = _run_systemctl_failed()
    if failed is None:
        findings.append(Finding(
            kind="primary_health_unreadable", key="systemctl_unavailable",
            severity="P1", summary="systemctl --failed niedostępny — nie da się zweryfikować stanu usług",
            detail={"reason": "systemctl_unavailable"},
        ))
    else:
        for unit in failed:
            if unit in suppress:
                continue
            if not _is_critical(unit):
                continue
            last_alert = _cron_health_last_alert(unit)
            silent = last_alert is None or (now - last_alert).total_seconds() > primary_alert_max_silence_h * 3600.0
            sev = "CRITICAL" if silent else "P1"
            reason = ("awaria + brak śladu alertu w podstawowym kanale (kanał NIEMY)"
                      if silent else "awaria (podstawowy kanał zaraportował — potwierdź dostawę)")
            findings.append(Finding(
                kind="failed_unit", key=f"failed:{unit}", severity=sev,
                summary=f"{unit}: {reason}",
                detail={"unit": unit, "primary_alert_silent": silent,
                        "last_alert_ts": last_alert.isoformat() if last_alert else None},
            ))

    # 2) heartbeat nocnego strażnika (regresja) — cisza = martwy strażnik
    ng = _night_guard_last_ts()
    if ng is None:
        findings.append(Finding(
            kind="night_guard_stale", key="night_guard:missing", severity="P1",
            summary="nocny strażnik regresji: brak odczytu heartbeatu (plik pusty/nieczytelny)",
            detail={"age_h": None},
        ))
    else:
        age_h = (now - ng).total_seconds() / 3600.0
        if age_h > night_guard_max_silence_h:
            findings.append(Finding(
                kind="night_guard_stale", key="night_guard:stale", severity="P1",
                summary=f"nocny strażnik regresji milczy {age_h:.1f}h (próg {night_guard_max_silence_h:.0f}h) — kanał może być martwy",
                detail={"age_h": round(age_h, 1), "last_ts": ng.isoformat()},
            ))

    # 3) czytelność ledgera zdrowia podstawowego kanału
    if not _cron_health_readable():
        findings.append(Finding(
            kind="primary_health_unreadable", key="cron_health:unreadable", severity="P2",
            summary="cron_health.json nieczytelny/brak — śledzenie zdrowia podstawowego kanału nie działa",
            detail={},
        ))

    return findings


# ----------------------------- message -----------------------------
def compose_message(findings: list[Finding], now: datetime) -> str:
    worst = "P2"
    order = {"CRITICAL": 3, "P1": 2, "P2": 1}
    for f in findings:
        if order.get(f.severity, 0) > order.get(worst, 0):
            worst = f.severity
    emoji = {"CRITICAL": "🚨", "P1": "🔴", "P2": "🟡"}.get(worst, "🟡")
    lines = [
        f"{emoji} DEAD-MAN ({worst}) — podstawowy kanał alertów może NIE działać",
        f"🕐 {now.astimezone().isoformat(timespec='seconds')}",
        f"🔎 Wykryto {len(findings)} problem(ów) niezależnie od głównego bota (wyciszony 26.06):",
        "",
    ]
    for f in findings:
        tag = {"CRITICAL": "🚨", "P1": "🔴", "P2": "🟡"}.get(f.severity, "•")
        lines.append(f"{tag} [{f.kind}] {f.summary}")
    lines += [
        "",
        "💡 Kanał niezależny (ten): @DajeszBot / assistant-telegram — NIE dispatch-telegram.",
        "📚 Runbook: journalctl --failed; sprawdź code-offsite-sync + dispatch-night-guard;",
        "   podstawowy stos = observability/alert_onfailure+watchdog (główny bot wyciszony).",
    ]
    return "\n".join(lines)


# ----------------------------- independent delivery -----------------------------
def _independent_creds() -> tuple[str | None, str | None]:
    """Token+chat z env (nazwy zmiennych, nie wartości). Nie loguje wartości."""
    token = os.getenv("DEADMAN_ALERT_TOKEN") or os.getenv("ASSISTANT_TELEGRAM_TOKEN")
    chat = os.getenv("DEADMAN_ALERT_CHAT_ID") or os.getenv("ASSISTANT_TELEGRAM_ADMIN_ID")
    return (token or None, chat or None)


def send_independent(text: str) -> bool:
    """Wyślij NIEZALEŻNYM kanałem (Telegram Bot API, bezpośredni HTTPS).

    Celowo NIE importuje telegram_utils/telegram_approver/notify_router — to jest
    ścieżka wyciszonego głównego bota, której dead-man ma NIE dzielić.
    Zwraca True tylko przy ok=True z API. Nigdy nie loguje tokena/chat_id.
    """
    token, chat = _independent_creds()
    if not token or not chat:
        log.error("deadman.send_independent: brak creds (DEADMAN_ALERT_TOKEN/ASSISTANT_TELEGRAM_TOKEN + *_CHAT_ID/ADMIN_ID)")
        return False
    import urllib.error
    import urllib.request
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat, "text": text}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        if not body.get("ok"):
            log.error("deadman.send_independent: API ok=False: %s", body.get("description"))
            return False
        return True
    except (urllib.error.URLError, json.JSONDecodeError, OSError, ValueError) as e:
        log.error("deadman.send_independent: HTTP fail: %s: %s", type(e).__name__, e)
        return False


def _shadow_log(record: dict) -> None:
    p = Path(os.getenv("DEADMAN_SHADOW_LOG", str(DEADMAN_SHADOW_LOG)))
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except OSError as e:
        log.warning("deadman: shadow log write fail: %s", e)


def _external_ping() -> None:
    """Opcjonalny ping do zewnętrznego dead-man-switch (np. healthchecks.io).

    Domyka lukę „kto pilnuje pilnującego" — jeśli TEN watchdog przestanie chodzić,
    zewnętrzny serwis zaalarmuje po braku pingu. Shadow: w DRY-RUN tylko log.
    """
    url = os.getenv("DEADMAN_EXTERNAL_PING_URL")
    if not url:
        return
    if _is_dry_run():
        log.info("deadman: [DRY-RUN] external ping -> (skonfigurowany URL)")
        return
    import urllib.request
    try:
        urllib.request.urlopen(url, timeout=10).read()
    except OSError as e:
        log.warning("deadman: external ping fail: %s", e)


# ----------------------------- orchestration -----------------------------
def run(now: datetime | None = None, *, dry_run: bool | None = None) -> dict:
    """Jeden przebieg dead-mana. Zwraca strukturę wyniku (dla testów/CLI).

    dry_run=None → wyznacz z env (shadow-first). Explicit bool nadpisuje.
    """
    now = now or datetime.now(timezone.utc)
    dry = _is_dry_run() if dry_run is None else dry_run
    cooldown_h = _env_float("DEADMAN_COOLDOWN_H", DEFAULT_COOLDOWN_H)
    ng_h = _env_float("DEADMAN_NIGHT_GUARD_MAX_SILENCE_H", DEFAULT_NIGHT_GUARD_MAX_SILENCE_H)
    pa_h = _env_float("DEADMAN_PRIMARY_ALERT_MAX_SILENCE_H", DEFAULT_PRIMARY_ALERT_MAX_SILENCE_H)

    all_findings = collect_findings(now, night_guard_max_silence_h=ng_h, primary_alert_max_silence_h=pa_h)

    # anti-noise: cooldown per klucz
    state = _load_state()
    active: list[Finding] = []
    cooled: list[str] = []
    for f in all_findings:
        if _cooldown_active(f.key, now, cooldown_h, state):
            cooled.append(f.key)
        else:
            active.append(f)

    result: dict = {
        "ts": now.isoformat(),
        "dry_run": dry,
        "findings_total": len(all_findings),
        "findings": [f.__dict__ for f in all_findings],
        "escalated": [f.key for f in active],
        "cooled_down": cooled,
        "sent": False,
        "message": None,
    }

    _external_ping()

    if not active:
        result["status"] = "quiet"
        return result

    msg = compose_message(active, now)
    result["message"] = msg

    if dry:
        result["status"] = "dry_run"
        _shadow_log({"ts": now.isoformat(), "would_send": True,
                     "keys": [f.key for f in active], "message": msg})
        # w DRY-RUN NIE nabijamy cooldownu — kolejne przebiegi mają nadal logować repro
        return result

    sent = send_independent(msg)
    result["sent"] = sent
    result["status"] = "sent" if sent else "send_failed"
    if sent:
        _record_alerted([f.key for f in active], now, state)
    return result


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    dry_override: bool | None = None
    if "--dry-run" in args:
        dry_override = True
    if "--no-dry-run" in args:
        dry_override = False
    res = run(dry_run=dry_override)
    status = res.get("status")
    if res.get("message"):
        print(res["message"])
    print(f"[deadman] status={status} total={res['findings_total']} "
          f"escalated={len(res['escalated'])} cooled={len(res['cooled_down'])} "
          f"dry_run={res['dry_run']} sent={res['sent']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
