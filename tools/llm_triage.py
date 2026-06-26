#!/usr/bin/env python3
"""
Narzędzie offline, tylko do odczytu, do diagnostyki systemu Ziomek.
Wykorzystuje OpenRouter – wywołuje LLM TRIAGE (hipoteza przyczyny) oraz LLM JUDGE (ocena).
Wynik zapisywany jest do pliku `llm_proposals.jsonl` i drukowany na konsolę.
Nie modyfikuje stanu silnika, nie wysyła komunikatów na Telegram, nie pisze kodu.
Gdy `wake_llm` ma wartość `False` – nie są wykonywane żadne wywołania LLM (koszt = 0).
"""

import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone

# ------------------------------------------------------------------------------
# ŚCIEŻKI / STAŁE
# ------------------------------------------------------------------------------
VERDICT_PATH = "/root/.openclaw/workspace/scripts/logs/reports/severity_verdict.json"
REPORT_PATH  = "/root/.openclaw/workspace/scripts/logs/reports/daily_rule_report.json"
OUT_JSONL    = "/root/.openclaw/workspace/scripts/logs/reports/llm_proposals.jsonl"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
TRIAGE_MODEL = "deepseek/deepseek-v4-flash"       # tani model, czyta raport
JUDGE_MODEL  = "anthropic/claude-opus-4.8"        # mocny, inna rodzina modelu

PRIMER = (
    "Ziomek = silnik dispatchu kurierskiego. "
    "Metryki dzienne: koord_rate=% zlecen spadlych do koordynatora (fallback, im wiecej tym gorzej); "
    "latency_p95=ms decyzji; "
    "zero_feasible_rate=% decyzji bez wykonalnego kuriera; "
    "r6_pred_over35_rate=% propozycji z przewidywanym czasem worka >35min; "
    "r6_actual_breach_rate=% REALNIE dostarczonych >35min od odbioru (twarda regula); "
    "fleet_gini_load=nierownosc obciazenia floty (0=rowno,1=jeden bierze wszystko); "
    "best_effort_rate=% propozycji awaryjnych. "
    "ZASADY TWARDE (nietykalne miekkimi zmianami): R6<=35min, R-DECLARED-TIME (odbior>=czas umowiony), "
    "R-FLEET-LEVEL (sprawiedliwosc floty). "
    "Equal-treatment: brak kar dla kurierow bez GPS / pre-shift. "
    "Petla jest propose-with-ACK: nic nie wchodzi bez czlowieka."
)

# ------------------------------------------------------------------------------
# FUNKCJE POMOCNICZE
# ------------------------------------------------------------------------------
def _load_json(path, default):
    """Wczytaj plik JSON; w przypadku błędu zwróć `default`."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return default


def _call_openrouter(model, system, user, max_tokens=1200, temperature=0.2):
    """Wywołaj OpenRouter API. Zwraca treść odpowiedzi lub None."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        print("Brak OPENROUTER_API_KEY w środowisku. Pomijam wywołanie LLM.", file=sys.stderr)
        return None

    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ],
        "temperature": temperature,
        "max_tokens": max_tokens
    }).encode("utf-8")

    req = urllib.request.Request(
        OPENROUTER_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json"
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.load(resp)
    except Exception as exc:
        print(f"Błąd wywołania OpenRouter: {exc}", file=sys.stderr)
        return None

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        print(f"Nieoczekiwana struktura odpowiedzi OpenRouter: {exc}", file=sys.stderr)
        return None


def _parse_json_loose(text):
    """Parsuj JSON z opcjonalnym wycinaniem pierwszego bloku {...}."""
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # spróbuj wyciąć pierwszy nawias klamrowy
    try:
        start = text.index("{")
        end = text.rindex("}")
        candidate = text[start:end + 1]
        return json.loads(candidate)
    except (ValueError, json.JSONDecodeError):
        return None


def _build_context(verdict, report):
    """Zbuduj czytelny kontekst dla LLM na podstawie werdyktu i raportu."""
    parts = []
    parts.append(PRIMER)
    parts.append("\nWERDYKT severity (najnowszy dzien):\n")
    parts.append(json.dumps(verdict, indent=1, ensure_ascii=False))
    parts.append("\nRAPORT (ostatnie do 10 dni):\n")
    if isinstance(report, list):
        parts.append(json.dumps(report[-10:], indent=1, ensure_ascii=False))
    else:
        parts.append(str(report))
    return "\n".join(parts)


# ------------------------------------------------------------------------------
# DIAGNOSTA (TRIAGE) I SĘDZIA (JUDGE)
# ------------------------------------------------------------------------------
_TRIAGE_SYSTEM = (
    "Jestes diagnosta systemu Ziomek. "
    "Dla wskazanych anomalii postaw HIPOTEZE przyczyny "
    "(klasa problemu / warstwa / interakcja metryk) i wskaz CO ZBADAC "
    "(konkretna metryka/dzwignia). "
    "NIE piszesz kodu. NIE proponujesz flipowania flag. "
    "Odpowiedz WYLACZNIE JSON: "
    "{\"hypotheses\":[{\"issue\":str,\"likely_cause\":str,\"what_to_investigate\":str,\"confidence\":number}],\"summary\":str}"
)

_JUDGE_SYSTEM = (
    "Jestes niezaleznym sedzia (inna rodzina modelu niz diagnosta). "
    "Ocen diagnoze: (1) czy oparta na danych z raportu czy spekulacja; "
    "(2) czy szanuje zasady Ziomka HARD>SOFT "
    "(R6 35min / R-DECLARED / R-FLEET-LEVEL nietykalne przez miekkie zmiany) "
    "i equal-treatment; (3) czy nie sugeruje czegos co je lamie. "
    "Odpowiedz WYLACZNIE JSON: "
    "{\"verdict\":\"SOUND|WEAK|REJECT\",\"critique\":str,\"grounded_in_data\":bool,\"respects_hard_soft\":bool}"
)


def run_triage(context, model=TRIAGE_MODEL):
    """Wywołaj model TRIAGE i zwróć słownik z raw oraz sparsowaną odpowiedzią."""
    content = _call_openrouter(model, _TRIAGE_SYSTEM, context, max_tokens=2200)
    parsed = _parse_json_loose(content) if content else None
    return {"model": model, "raw": content, "parsed": parsed}


def run_judge(context, triage_obj, model=JUDGE_MODEL):
    """Wywołaj model JUDGE i zwróć słownik z raw oraz sparsowaną odpowiedzią."""
    diag_data = triage_obj.get("parsed") or triage_obj.get("raw")
    if diag_data is None:
        diag_text = "Brak dostępnych danych diagnostycznych."
    else:
        diag_text = json.dumps(diag_data, ensure_ascii=False, indent=1)

    user = context + "\n\nDIAGNOZA DO OCENY:\n" + diag_text
    content = _call_openrouter(model, _JUDGE_SYSTEM, user)
    parsed = _parse_json_loose(content) if content else None
    return {"model": model, "raw": content, "parsed": parsed}


# ------------------------------------------------------------------------------
# ZAPIS PROPOZYCJI
# ------------------------------------------------------------------------------
def append_proposal(rec):
    """Dopisz rekord do pliku JSONL (tworzy katalog, jeśli nie istnieje)."""
    os.makedirs(os.path.dirname(OUT_JSONL), exist_ok=True)
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    with open(OUT_JSONL, "a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


def _send_telegram(rec):
    """Wyslij ZWIEZLE podsumowanie diagnozy na grupe ziomka (PODGLAD — nie do wykonania). Fail-soft."""
    try:
        from dispatch_v2.telegram_utils import send_admin_alert
    except Exception as exc:
        print(f"Telegram niedostepny: {exc}", file=sys.stderr)
        return
    tp = (rec.get("triage") or {}).get("parsed") or {}
    jp = (rec.get("judge") or {}).get("parsed") or {}
    lines = [
        f"🔎 DIAGNOZA Ziomka (PODGLAD — nie wykonane) {rec.get('date')} | severity {rec.get('top_severity')}",
        f"Triage: {(tp.get('summary') or '—')[:400]}",
    ]
    for h in (tp.get("hypotheses") or [])[:3]:
        lines.append(f"• {str(h.get('issue',''))[:80]}: zbadaj — {str(h.get('what_to_investigate',''))[:120]}")
    lines.append(
        f"Sedzia [{rec.get('judge_model')}]: {jp.get('verdict','?')} | "
        f"dane:{jp.get('grounded_in_data','?')} HARD:{jp.get('respects_hard_soft','?')}"
    )
    if jp.get("critique"):
        lines.append(f"  ↳ {str(jp['critique'])[:300]}")
    try:
        send_admin_alert("\n".join(lines), source="llm_triage")
        print("Wyslano podsumowanie na Telegram.")
    except Exception as exc:
        print(f"Blad wysylki Telegram: {exc}", file=sys.stderr)


# ------------------------------------------------------------------------------
# MAIN
# ------------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Narzędzie offline do diagnostyki Ziomek z użyciem LLM (OpenRouter)."
    )
    parser.add_argument("--verdict", default=VERDICT_PATH)
    parser.add_argument("--report", default=REPORT_PATH)
    parser.add_argument("--triage-model", default=TRIAGE_MODEL)
    parser.add_argument("--judge-model", default=JUDGE_MODEL)
    parser.add_argument("--force", action="store_true",
                        help="Wywołaj LLM nawet gdy wake_llm=False")
    parser.add_argument("--dry-run", action="store_true",
                        help="Nie zapisuj propozycji do pliku")
    parser.add_argument("--telegram", action="store_true",
                        help="wyslij podsumowanie na grupe ziomka (PODGLAD)")
    args = parser.parse_args()

    verdict = _load_json(args.verdict, {})
    report = _load_json(args.report, [])

    if not verdict.get("wake_llm") and not args.force:
        print("wake_llm=False -> brak wywołania LLM (koszt 0)")
        return 0

    context = _build_context(verdict, report)

    triage = run_triage(context, model=args.triage_model)
    judge = run_judge(context, triage, model=args.judge_model)

    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "date": verdict.get("date"),
        "top_severity": verdict.get("top_severity"),
        "wake_llm": verdict.get("wake_llm"),
        "n_issues": len(verdict.get("issues", [])),
        "triage_model": args.triage_model,
        "triage": triage,
        "judge_model": args.judge_model,
        "judge": judge
    }

    # ---- Czytelny wydruk na konsolę ----
    print("--- DIAGNOSTYKA LLM ---")
    print(f"Data werdyktu: {verdict.get('date')}  "
          f"top_severity: {verdict.get('top_severity')}  "
          f"wake_llm: {verdict.get('wake_llm')}  "
          f"issues: {len(verdict.get('issues', []))}")

    t_parsed = triage.get("parsed")
    t_raw = triage.get("raw")
    if t_parsed:
        print("\n[TRIAGE]")
        print(f"  Podsumowanie: {t_parsed.get('summary', '')}")
        for i, h in enumerate(t_parsed.get("hypotheses", []), 1):
            print(f"  Hipoteza {i}: {h.get('issue','')} | "
                  f"przyczyna: {h.get('likely_cause','')} | "
                  f"co zbadać: {h.get('what_to_investigate','')} | "
                  f"pewność: {h.get('confidence','')}")
    else:
        print("\n[TRIAGE] (brak parsowalnej odpowiedzi, raw)")
        print(f"  {t_raw[:500] if t_raw else 'Brak odpowiedzi'}")

    j_parsed = judge.get("parsed")
    j_raw = judge.get("raw")
    if j_parsed:
        print("\n[JUDGE]")
        print(f"  Werdykt: {j_parsed.get('verdict', '')}")
        print(f"  Krytyka: {j_parsed.get('critique', '')}")
        print(f"  Oparte na danych: {j_parsed.get('grounded_in_data', '')}  "
              f"Szanuje HARD>SOFT: {j_parsed.get('respects_hard_soft', '')}")
    else:
        print("\n[JUDGE] (brak parsowalnej odpowiedzi, raw)")
        print(f"  {j_raw[:500] if j_raw else 'Brak odpowiedzi'}")

    if not args.dry_run:
        append_proposal(rec)
        print(f"\nPropozycja zapisana do {OUT_JSONL}")
    else:
        print("\nDry-run — nie zapisano propozycji.")

    if args.telegram:
        _send_telegram(rec)

    return 0


if __name__ == "__main__":
    sys.exit(main())
