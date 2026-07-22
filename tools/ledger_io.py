"""ledger_io — READ-side kanon ledgerów Ziomka (audyt spójności, fala L0.3).

Write-side kanon istnieje od dawna (`core/jsonl_appender.append_jsonl` —
atomic O_APPEND + flock). READ-side był rozproszony: każdy konsument otwierał
żywy plik na własną rękę, przez co (a) gubił zrotowane siblingi i (b) kopiował
ad-hoc parsowanie ts / kanonizację oid. Ten moduł to JEDNO miejsce odczytu.

Dlaczego to ma znaczenie (FAZA1_03 / L0.3):
  * Naiwny odczyt SAMEGO żywego `shadow_decisions.jsonl` gubi ~29% okna 7 dni
    (497/1707 oid) — logrotate (copytruncate, size 100M / daily) przycina ogon
    po cichu. Rotation-aware odczyt (delegacja do `_rotated_logs`) domyka okno.
  * Fizyczna prawda (`gps_delivery_truth.jsonl`) pokrywa ~11,5% okna; proxy
    (`decision_outcomes.jsonl`) ~98,5%. Paczki (oid „900…") = 0% w OBU prawdach
    → ich etykieta to `none`, NIGDY „brak = zgoda". `require_join_coverage`
    zamienia brak fizyki w GŁOŚNY błąd (`LedgerCoverageError`), nie w cichy
    „inconclusive" traktowany jak zielone światło.

Moduł jest READ-only: żaden odczyt nie dotyka dysku zapisem. Jedyny wyjątek to
`verdict_txt_header`, który wyłącznie FORMATUJE 1-liniowy nagłówek (bez I/O).

Konsumenci do przepięcia w L1.2 (NIE tu): każde narzędzie w `tools/` które dziś
samo otwiera `shadow_decisions.jsonl` / `sla_log.jsonl` / `decision_outcomes.jsonl`
/ `gps_delivery_truth.jsonl` — przełączyć na `iter_shadow_decisions` /
`load_outcomes` / `load_gps_truth` / `join_decisions_with_truth`.

Fundament: `dispatch_v2/tools/_rotated_logs.py` (SP-B2-LOGROT) — rotation-aware
iteracja (.1 / .2.gz), pruning całych plików po mtime. Ten moduł buduje na nim,
dokładając: filtr ts per-rekord, kanonizację oid→str, optymalizację ogona
(`max_bytes`) dla świeżych okien oraz join decyzja↔prawda z etykietą źródła.

Testy: dispatch_v2/tests/test_ledger_io.py.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Iterator, Optional

try:  # importowany jako dispatch_v2.tools.ledger_io (kanon)
    from dispatch_v2.tools import _rotated_logs
except ImportError:  # pragma: no cover - fallback dla uruchomienia z katalogu tools/
    import _rotated_logs  # type: ignore


# ── Kanoniczne ścieżki ledgerów ────────────────────────────────────────────
# ŻYWE źródła prawdy. Uwaga: `sla` to ŻYWY scripts/logs/sla_log.jsonl, NIE
# martwy dispatch_state/sla_log.jsonl (zamrożony 2026-06-20).
LEDGER = {
    "shadow":    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl",
    "sla":       "/root/.openclaw/workspace/scripts/logs/sla_log.jsonl",
    "gps_truth": "/root/.openclaw/workspace/dispatch_state/gps_delivery_truth.jsonl",
    "outcomes":  "/root/.openclaw/workspace/dispatch_state/decision_outcomes.jsonl",
    "dwell":     "/root/.openclaw/workspace/dispatch_state/restaurant_dwell.json",
}

# Pola znacznika czasu per ledger — pierwsze obecne i parsowalne wygrywa.
# (outcomes: ts_decision bywa None w danych → written_at jest pierwszym pewnym.)
_TS_FIELDS = {
    "shadow":    ("ts", "timestamp"),
    "sla":       ("logged_at", "delivered_at"),
    "gps_truth": ("button_delivered_at", "physical_delivered_at", "delivered_day"),
    "outcomes":  ("written_at", "delivered_at", "ts_decision"),
}

# Klucze oid — kanonizowane do str (join po str(order_id)).
_OID_KEYS = ("order_id", "oid")

# Etykiety źródła prawdy w join_decisions_with_truth.
TRUTH_PHYSICAL = "physical_gps"   # rekonstrukcja fizyczna z GPS (gps_delivery_truth)
TRUTH_PROXY = "button_proxy"      # proxy z decision_outcomes (przycisk „doręczono")
TRUTH_NONE = "none"               # brak w OBU prawdach (np. paczki 900…) — NIE „zgoda"

# Etykiety werdyktu (label_verdict).
VERDICT_GROUND_TRUTH = "ground_truth"
VERDICT_PROXY_CERTIFIED = "proxy_certified"
VERDICT_VOID = "VOID"

# Progi jakości werdyktu. Domyślny próg fizyki = 1 spójny z require_join_coverage
# (min_physical=1): choćby JEDNA fizyczna kotwica pozwala oprzeć werdykt o fizykę;
# przy zerowej fizyce, ale ≥1 proxy — werdykt „proxy_certified"; zero obu → VOID.
# Caller, który chce ostrzejszą poprzeczkę, bramkuje przez require_join_coverage.
MIN_PHYSICAL_FOR_GROUND_TRUTH = 1
MIN_PROXY_FOR_CERTIFIED = 1


class LedgerCoverageError(RuntimeError):
    """Za mało prawdy fizycznej w oknie, by wydać werdykt (fail-loud).

    Świadomie NIE „inconclusive": brak GPS ground-truth nie może być cicho
    interpretowany jak zgoda/zielone światło (FAZA1_03 / L0.3).
    """


# ── Pomocnicze (prywatne) ──────────────────────────────────────────────────
def _ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """None-safe → tz-aware UTC (naive traktujemy jako UTC)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_ts(value) -> Optional[datetime]:
    """Parsuj znacznik czasu (ISO8601 z 'Z'/'+00:00', spacją, sama data, epoch).

    Zwraca tz-aware UTC albo None. Naive → UTC.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return _ensure_utc(value)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    s = str(value).strip()
    if not s:
        return None
    s = s.replace("Z", "+00:00") if s.endswith("Z") else s
    try:
        return _ensure_utc(datetime.fromisoformat(s))
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return _ensure_utc(datetime.strptime(s, fmt))
        except ValueError:
            continue
    return None


def _rec_ts(rec: dict, fields) -> Optional[datetime]:
    """Znacznik czasu rekordu z listy pól kandydujących (pierwsze parsowalne)."""
    for f in fields:
        v = rec.get(f)
        if v:
            dt = _parse_ts(v)
            if dt is not None:
                return dt
    return None


def _oid_str(rec: dict) -> Optional[str]:
    """Kanoniczny oid rekordu jako str (order_id → oid), albo None."""
    for k in _OID_KEYS:
        v = rec.get(k)
        if v is not None:
            return str(v)
    return None


def _canon_oid(rec: dict) -> None:
    """W miejscu: zamień oid rekordu na str (kanonizacja klucza joinu)."""
    for k in _OID_KEYS:
        if k in rec and rec[k] is not None:
            rec[k] = str(rec[k])
            return


def _iso(v) -> str:
    """datetime → ISO (tz-aware UTC); cokolwiek innego → str(v) bez zmian."""
    if isinstance(v, datetime):
        return _ensure_utc(v).isoformat()
    return str(v)


def _read_live_tail(path: str, cutoff_dt: Optional[datetime], max_bytes: int):
    """Ogon ŻYWEGO pliku jako lista rekordów — albo None gdy ogon niepewny.

    Optymalizacja dla świeżych okien (tick-strażnicy): zamiast czytać cały (np.
    67MB) plik + zrotowane siblingi, czytamy tylko ostatnie `max_bytes` bajtów
    żywego pliku. Zwracamy None (→ caller idzie pełną ścieżką rotation-aware),
    gdy ogon NIE gwarantuje semantyki identycznej z pełnym odczytem:
      * plik krótszy/równy max_bytes — brak oszczędności, pełna ścieżka i tak tania,
      * mtime pliku < cutoff — żywy plik „zimny", okno może sięgać zrotowanych,
      * istnieją zrotowane siblingi W OKNIE (mtime ≥ cutoff) — mogą trzymać
        rekordy z okna, których ogon żywego pliku nie widzi,
      * najstarszy KOMPLETNY rekord w ogonie ma ts > cutoff — okno zaczyna się
        PRZED cięciem ogona, więc ogon mógłby zgubić wczesne rekordy okna.
    W pozostałych przypadkach ogon jest autorytatywny → zwracamy przefiltrowane
    (ts ≥ cutoff), skanonizowane rekordy. Zakłada chronologiczny append (log
    dopisywany w czasie rzeczywistym — spełnione dla shadow_decisions).
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return None
    if size <= max_bytes:
        return None
    if cutoff_dt is not None:
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(path), timezone.utc)
        except OSError:
            return None
        if mtime < cutoff_dt:
            return None
        # Zrotowane siblingi w oknie → pełna ścieżka (ogon żywego ich nie obejmuje).
        if len(_rotated_logs.files_in_window(path, cutoff_dt)) > 1:
            return None
    try:
        with open(path, "rb") as f:
            f.seek(size - max_bytes)
            chunk = f.read()
    except OSError:
        return None
    text = chunk.decode("utf-8", errors="replace")
    # Odrzuć 1. (ułamaną) linię — cięcie prawie na pewno trafia w środek rekordu.
    parsed = []
    for ln in text.split("\n")[1:]:
        ln = ln.strip()
        if not ln:
            continue
        try:
            r = json.loads(ln)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(r, dict):
            parsed.append(r)
    if not parsed:
        return None
    if cutoff_dt is not None:
        first_ts = _rec_ts(parsed[0], _TS_FIELDS["shadow"])
        if first_ts is None or first_ts > cutoff_dt:
            # Okno zaczyna się przed cięciem — ogon niekompletny.
            return None
    out = []
    for r in parsed:
        if cutoff_dt is not None:
            ts = _rec_ts(r, _TS_FIELDS["shadow"])
            if ts is None or ts < cutoff_dt:
                continue
        _canon_oid(r)
        out.append(r)
    return out


# ── API publiczne ──────────────────────────────────────────────────────────
def iter_shadow_decisions(cutoff_dt: Optional[datetime], *,
                          max_bytes: Optional[int] = None,
                          include_observations: bool = False) -> Iterator[dict]:
    """Rekordy shadow_decisions z okna [cutoff_dt, teraz], rotation-aware.

    Deleguje pruning całych plików do `_rotated_logs.iter_jsonl_records`, potem
    filtruje ts ≥ cutoff per-rekord i kanonizuje oid→str. `max_bytes` (opcja dla
    tick-strażników) czyta tylko ogon żywego pliku, gdy to bezpieczne —
    semantyka wynikowa identyczna z pełną ścieżką (patrz `_read_live_tail`).
    Lifecycle observations żyją w tym samym kanonicznym pliku, ale domyślnie
    nie wchodzą do mianowników historycznych konsumentów decyzji.
    """
    cutoff_dt = _ensure_utc(cutoff_dt)
    path = LEDGER["shadow"]
    if max_bytes is not None:
        tail = _read_live_tail(path, cutoff_dt, max_bytes)
        if tail is not None:
            for rec in tail:
                if include_observations or rec.get("decision_kind") != "lifecycle_observation":
                    yield rec
            return
    for rec in _rotated_logs.iter_jsonl_records(path, cutoff_dt):
        if cutoff_dt is not None:
            ts = _rec_ts(rec, _TS_FIELDS["shadow"])
            if ts is None or ts < cutoff_dt:
                continue
        _canon_oid(rec)
        if include_observations or rec.get("decision_kind") != "lifecycle_observation":
            yield rec


def parse_sla_ts(value) -> Optional[datetime]:
    """Znacznik czasu POLA sla_log (`picked_up_at`/`delivered_at`) → aware UTC.

    Semantyka writera (sla_tracker.py, panel Rutcom): stemple NAIVE = czas
    WARSZAWSKI, nie UTC (§ docstring `_parse_aware_utc` w sla_tracker). Martwy
    dispatch_state/sla_log.jsonl (sla_join_worker, zamrożony 20.06) niósł aware
    UTC — konsumenci pisani pod niego, parsując naive żywego loga jako UTC,
    wnoszą +2h błędu (near-miss L1.2, join no_gps_eta_error: mediana ~131 min
    zamiast ~11). TEN parser to jedno źródło tej wiedzy: aware → UTC,
    naive → Europe/Warsaw → UTC.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return _parse_ts(value)  # epoch = UTC z definicji
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        if not s:
            return None
        s = s.replace("Z", "+00:00") if s.endswith("Z") else s
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            dt = None
        if dt is None:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
                try:
                    dt = datetime.strptime(s, fmt)
                    break
                except ValueError:
                    continue
            if dt is None:
                return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc)
    # naive = czas warszawski writera → przypnij Warszawę i przelicz na UTC
    try:
        from zoneinfo import ZoneInfo
        return dt.replace(tzinfo=ZoneInfo("Europe/Warsaw")).astimezone(timezone.utc)
    except Exception:  # brak tzdata (nie powinno się zdarzyć) — uczciwy fallback UTC
        return dt.replace(tzinfo=timezone.utc)


def iter_sla(cutoff_dt: Optional[datetime] = None) -> Iterator[dict]:
    """Rekordy sla_log z okna [cutoff_dt, teraz], rotation-aware (L1.2).

    Czyta ŻYWY `scripts/logs/sla_log.jsonl` (LEDGER["sla"]) — NIE martwy
    `dispatch_state/sla_log.jsonl` (zamrożony 2026-06-20; dwa toole czytały go
    do L1.2: no_gps_eta_error, prep_bias_r6_replay). Filtr ts per-rekord po
    polach `logged_at`/`delivered_at`; oid kanonizowany do str.
    """
    cutoff_dt = _ensure_utc(cutoff_dt)
    fields = _TS_FIELDS["sla"]
    for rec in _rotated_logs.iter_jsonl_records(LEDGER["sla"], cutoff_dt):
        if cutoff_dt is not None:
            ts = _rec_ts(rec, fields)
            if ts is None or ts < cutoff_dt:
                continue
        _canon_oid(rec)
        yield rec


def _load_keyed(ledger_key: str, cutoff_dt: Optional[datetime]) -> dict:
    """Wspólny loader {str(oid): rekord} dla JSONL ledgerów prawdy (last-wins).

    iter_jsonl_records yielduje chronologicznie (najstarsze→najnowsze), więc
    ostatni rekord danego oid nadpisuje wcześniejsze — najświeższa prawda wygrywa.
    """
    cutoff_dt = _ensure_utc(cutoff_dt)
    fields = _TS_FIELDS[ledger_key]
    out: dict = {}
    for rec in _rotated_logs.iter_jsonl_records(LEDGER[ledger_key], cutoff_dt):
        oid = _oid_str(rec)
        if oid is None:
            continue
        if cutoff_dt is not None:
            ts = _rec_ts(rec, fields)
            if ts is None or ts < cutoff_dt:
                continue
        out[oid] = rec
    return out


def load_gps_truth(cutoff_dt: Optional[datetime] = None) -> dict:
    """{str(oid): rekord} z gps_delivery_truth (fizyczna prawda ~11,5% okna)."""
    return _load_keyed("gps_truth", cutoff_dt)


def load_outcomes(cutoff_dt: Optional[datetime] = None) -> dict:
    """{str(oid): rekord} z decision_outcomes (proxy „przycisk" ~98,5% okna)."""
    return _load_keyed("outcomes", cutoff_dt)


def load_restaurant_dwell() -> dict:
    """{str(oid): rekord} z restaurant_dwell.json (całościowy JSON, nie JSONL)."""
    try:
        with open(LEDGER["dwell"], encoding="utf-8", errors="replace") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): v for k, v in data.items()}


def join_decisions_with_truth(cutoff_dt: Optional[datetime]) -> Iterator[dict]:
    """Decyzje z okna wzbogacone o prawdę (fizyczną / proxy / brak).

    Każdy yield = płytka kopia rekordu decyzji + pola:
      truth_source     ∈ {physical_gps, button_proxy, none}
      truth_confidence  = gps_truth.confidence (przy physical) | None
      physical          = rekord gps_truth | None
      proxy             = rekord decision_outcomes | None

    Prawda ładowana bez cutoff — rekord prawdy powstaje PO decyzji (po doręczeniu),
    więc może wypaść tuż poza okno decyzji; join po oid jest niezależny od czasu.
    """
    cutoff_dt = _ensure_utc(cutoff_dt)
    gps = load_gps_truth(None)
    outcomes = load_outcomes(None)
    for rec in iter_shadow_decisions(cutoff_dt):
        oid = _oid_str(rec)
        physical = gps.get(oid) if oid is not None else None
        proxy = outcomes.get(oid) if oid is not None else None
        if physical is not None:
            source = TRUTH_PHYSICAL
        elif proxy is not None:
            source = TRUTH_PROXY
        else:
            source = TRUTH_NONE
        row = dict(rec)
        row["truth_source"] = source
        row["truth_confidence"] = physical.get("confidence") if physical else None
        row["physical"] = physical
        row["proxy"] = proxy
        yield row


def require_join_coverage(rows, min_physical: int = 1) -> None:
    """Fail-loud gdy w oknie za mało prawdy fizycznej (raise LedgerCoverageError).

    `rows` = zmaterializowana sekwencja wierszy z join_decisions_with_truth
    (NIE generator — jest zliczana, nie zwracana). Zeruje ryzyko „cisza = zgoda":
    brak GPS ground-truth zatrzymuje werdykt zamiast go cicho przepuścić.
    """
    n_physical = sum(1 for r in rows if r.get("truth_source") == TRUTH_PHYSICAL)
    if n_physical < min_physical:
        raise LedgerCoverageError(
            f"za mało prawdy fizycznej w oknie: {n_physical} < {min_physical} "
            f"(brak GPS ground-truth — NIE traktuj ciszy jako zgody; "
            f"FAZA1_03/L0.3: fizyka pokrywa ~11,5% okna 7d)"
        )


def label_verdict(n_physical: int, n_proxy: int) -> str:
    """Etykieta jakości werdyktu: ground_truth / proxy_certified / VOID."""
    if n_physical >= MIN_PHYSICAL_FOR_GROUND_TRUTH:
        return VERDICT_GROUND_TRUTH
    if n_proxy >= MIN_PROXY_FOR_CERTIFIED:
        return VERDICT_PROXY_CERTIFIED
    return VERDICT_VOID


def verdict_txt_header(*, window_since, window_until, n_physical: int,
                       n_proxy: int, stale_after) -> str:
    """1-liniowy nagłówek werdyktu (sam string, bez I/O)."""
    return (
        f"# ledger_io verdict | generated={_iso(datetime.now(timezone.utc))} "
        f"| window={_iso(window_since)}..{_iso(window_until)} "
        f"| truth=phys {n_physical}/proxy {n_proxy} "
        f"| stale_after={_iso(stale_after)}"
    )


__all__ = [
    "LEDGER",
    "LedgerCoverageError",
    "TRUTH_PHYSICAL", "TRUTH_PROXY", "TRUTH_NONE",
    "VERDICT_GROUND_TRUTH", "VERDICT_PROXY_CERTIFIED", "VERDICT_VOID",
    "iter_shadow_decisions", "iter_sla", "parse_sla_ts",
    "load_gps_truth", "load_outcomes", "load_restaurant_dwell",
    "join_decisions_with_truth",
    "require_join_coverage", "label_verdict", "verdict_txt_header",
]
