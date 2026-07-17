#!/usr/bin/env python3
"""validate_state_schema.py — READ-ONLY drift-detektor schematu krytycznych plików stanu.

CEL:
Wykrywa CICHĄ degradację struktury 4 produkcyjnych plików `dispatch_state/`:
  - orders_state.json           (dict oid->rec)
  - courier_plans.json          (dict cid->plan)
  - courier_ground_truth.json   (dict cid->rec)
  - panel_packs_cache.json      (PŁASKI wrapper: ts/packs/tick/orders_in_panel)

To NIE jest wersjonowanie z zapisem. Pliki dict-of-entries są czytane przez
.items()/.values() w żywym kodzie — wstrzyknięcie klucza `_meta` na top-level
ZEPSUŁOby czytelników. Dlatego walidujemy WYŁĄCZNIE kształt, bez mutacji.

Dwa kształty (z baseline `shape`):
  - "dict_of_entries": top=dict; KAŻDY wpis musi być dict i zawierać required_keys.
  - "flat_object":     top=dict; required_keys to KLUCZE TOP-LEVEL (bez iteracji wpisów).

READ-ONLY. Zero zapisu do plików stanu. Zero zależności poza stdlib na ścieżce
podstawowej (telegram importowany LENIWIE tylko przy --alert).

Wyjście:
  exit 0 = wszystko OK (lub same WARN-y o brakujących plikach)
  exit 1 = wykryto DRIFT przynajmniej w jednym pliku

Flagi:
  --json   maszynowy JSON na stdout (zamiast tekstu)
  --alert  przy wykrytym driftcie wyślij alert przez dispatch_v2.telegram_utils
           (NIE wysyła nic gdy brak driftu; brak pakietu => cicha degradacja do WARN)

Uruchom:
  python3 -m dispatch_v2.tools.validate_state_schema
  python3 tools/validate_state_schema.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Twarda ścieżka do żywego katalogu stanu (READ-ONLY).
STATE_DIR = Path("/root/.openclaw/workspace/dispatch_state")

# Baseline leży obok tego skryptu.
BASELINE_PATH = Path(__file__).resolve().parent / "state_schema_baseline.json"

# Ile przykładowych winnych id pokazać w raporcie per kategoria.
MAX_EXAMPLES = 5


def _load_baseline(path: Path = BASELINE_PATH) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def validate_file(spec: dict, state_dir: Path = STATE_DIR) -> dict:
    """Zwaliduj pojedynczy plik wg jego specyfikacji baseline.

    Zwraca słownik raportu (zawsze JSON-serializowalny). Fail-soft:
    brakujący plik => status "warn" (NIE drift, NIE crash), zły JSON => "error".
    """
    filename = spec["filename"]
    path = state_dir / filename
    shape = spec.get("shape", "dict_of_entries")
    required = list(spec.get("required_keys", []))

    report = {
        "file": filename,
        "shape": shape,
        "status": "ok",            # ok | warn | drift | error
        "drift": False,
        "entries_total": 0,
        "missing_key_counts": {},  # key -> ile wpisów go nie ma
        "non_dict_entry_ids": [],  # przykłady wpisów które nie są dict
        "missing_top_keys": [],    # dla flat_object: brakujące klucze top-level
        "messages": [],
    }

    if not path.exists():
        report["status"] = "warn"
        report["messages"].append(f"PLIK NIE ISTNIEJE: {path} (pomijam — WARN, nie drift)")
        return report

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        report["status"] = "error"
        report["drift"] = True
        report["messages"].append(f"NIE DA SIĘ WCZYTAĆ/SPARSOWAĆ: {exc}")
        return report

    # Top-level musi być dict w obu kształtach.
    if not isinstance(data, dict):
        report["status"] = "drift"
        report["drift"] = True
        report["messages"].append(
            f"TOP-LEVEL nie jest dict (jest {type(data).__name__}) — oczekiwano dict"
        )
        return report

    if shape == "flat_object":
        # required_keys to klucze TOP-LEVEL; brak iteracji wpisów.
        report["entries_total"] = len(data)
        missing = [k for k in required if k not in data]
        if missing:
            report["status"] = "drift"
            report["drift"] = True
            report["missing_top_keys"] = missing
            report["messages"].append(
                f"BRAK kluczy top-level: {missing}"
            )
        return report

    # shape == "dict_of_entries"
    report["entries_total"] = len(data)
    missing_counts: dict[str, int] = {k: 0 for k in required}
    missing_examples: dict[str, list] = {k: [] for k in required}
    non_dict_ids: list = []

    for entry_id, entry in data.items():
        if not isinstance(entry, dict):
            if len(non_dict_ids) < MAX_EXAMPLES:
                non_dict_ids.append(entry_id)
            continue
        for k in required:
            if k not in entry:
                missing_counts[k] += 1
                if len(missing_examples[k]) < MAX_EXAMPLES:
                    missing_examples[k].append(entry_id)

    # Złóż wynik.
    non_zero_missing = {k: c for k, c in missing_counts.items() if c > 0}
    report["non_dict_entry_ids"] = non_dict_ids
    report["missing_key_counts"] = non_zero_missing
    # Przykłady tylko dla kluczy które realnie brakują.
    report["missing_key_examples"] = {
        k: missing_examples[k] for k in non_zero_missing
    }

    if non_dict_ids or non_zero_missing:
        report["status"] = "drift"
        report["drift"] = True
        if non_dict_ids:
            report["messages"].append(
                f"WPISY które nie są dict (przykłady): {non_dict_ids}"
            )
        for k, c in non_zero_missing.items():
            report["messages"].append(
                f"klucz '{k}' BRAK w {c}/{report['entries_total']} wpisach "
                f"(np. {missing_examples[k]})"
            )

    return report


def run(baseline: dict, state_dir: Path = STATE_DIR) -> dict:
    """Zwaliduj wszystkie pliki z baseline. Zwraca zbiorczy raport."""
    results = []
    for filename, spec in baseline.get("files", {}).items():
        spec = dict(spec)
        spec["filename"] = filename
        results.append(validate_file(spec, state_dir=state_dir))

    any_drift = any(r["drift"] for r in results)
    summary = {
        "schema_version": baseline.get("schema_version"),
        "state_dir": str(state_dir),
        "any_drift": any_drift,
        "files": results,
    }
    return summary


def _print_text(summary: dict) -> None:
    print(f"=== Walidacja schematu stanu (schema_version={summary['schema_version']}) ===")
    print(f"Katalog stanu: {summary['state_dir']}")
    for r in summary["files"]:
        flag = {
            "ok": "OK   ",
            "warn": "WARN ",
            "drift": "DRIFT",
            "error": "ERROR",
        }.get(r["status"], r["status"])
        print(f"[{flag}] {r['file']}  (wpisów: {r['entries_total']}, kształt: {r['shape']})")
        for msg in r["messages"]:
            print(f"        - {msg}")
    print("-" * 60)
    if summary["any_drift"]:
        print("WYNIK: DRIFT WYKRYTY (exit 1)")
    else:
        print("WYNIK: OK — brak driftu (exit 0)")


def _send_alert(summary: dict) -> None:
    """Wyślij alert tylko przy driftcie. Telegram importowany LENIWIE.

    Brak pakietu / ścieżki => cicha degradacja (drukujemy WARN, nie crashujemy):
    walidator dalej zwróci exit 1, więc cron i tak zauważy.
    """
    if not summary.get("any_drift"):
        return
    drifted = [r for r in summary["files"] if r["drift"]]
    lines = ["⚠ DRIFT schematu plików stanu dispatch_state:"]
    for r in drifted:
        head = f"• {r['file']} [{r['status']}]"
        detail = "; ".join(r["messages"][:3]) if r["messages"] else ""
        lines.append(f"{head} {detail}".rstrip())
    text = "\n".join(lines)
    try:
        from dispatch_v2.telegram_utils import send_admin_alert  # lazy
        send_admin_alert(text)
    except Exception as exc:  # noqa: BLE001 — fail-soft z założenia
        print(f"[WARN] nie udało się wysłać alertu Telegram: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="maszynowy JSON na stdout")
    parser.add_argument(
        "--alert",
        action="store_true",
        help="przy driftcie wyślij alert Telegram (nic nie wysyła bez driftu)",
    )
    parser.add_argument(
        "--state-dir",
        default=str(STATE_DIR),
        help="nadpisz katalog stanu (do testów /tmp); domyślnie produkcyjny",
    )
    parser.add_argument(
        "--baseline",
        default=str(BASELINE_PATH),
        help="nadpisz ścieżkę baseline JSON",
    )
    args = parser.parse_args(argv)

    try:
        baseline = _load_baseline(Path(args.baseline))
    except (json.JSONDecodeError, OSError) as exc:
        # Brak/zepsuty baseline to błąd konfiguracji, nie stanu — głośno.
        msg = {"error": f"nie da się wczytać baseline: {exc}", "any_drift": True}
        if args.json:
            print(json.dumps(msg, ensure_ascii=False))
        else:
            print(f"[ERROR] {msg['error']}", file=sys.stderr)
        return 1

    summary = run(baseline, state_dir=Path(args.state_dir))

    if args.alert:
        _send_alert(summary)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        _print_text(summary)

    return 1 if summary["any_drift"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
