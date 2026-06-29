#!/usr/bin/env python3
"""reassign_global_select_review — raport po peaku: czy globalne rozbijanie pile-on
PRZERZUTÓW realnie działa (10 na Jakuba → 1-2) i czy NIE over-hide'uje.

Czyta dispatch_state/reassign_global_select.jsonl (werdykt per tick pisany przez
reassignment_global_select.run_once: candidates_in / survivors_out / hidden_out /
maxpile_before / maxpile_after / spread_improved / dropped).

Podsumowuje okno (domyślnie ostatnie N godzin): ile ticków miało realny pile-on
(maxpile_before≥2), ile rozbił (maxpile_after<before), rozkład maxpile, największe
rozbicia, sumy survivor/hidden. + STRAŻNIK OVER-HIDE (sanity C9): ticki z
candidates_in≥2 ale survivors_out==0 = podejrzenie nadmiernego chowania (ten sam bug
złapany 29.06 przy starcie — singleton błędnie hide). Drukuje JSON + tekst, wysyła
skrót na Telegram Adriana (best-effort; jak muted → plik werdyktu zostaje).

Uruchomienie: python -m dispatch_v2.tools.reassign_global_select_review [--hours N] [--no-telegram]
"""
from __future__ import annotations
import sys
import json
import argparse
from collections import Counter
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

JSONL = "/root/.openclaw/workspace/dispatch_state/reassign_global_select.jsonl"
OUT_VERDICT = "/root/.openclaw/workspace/dispatch_state/reassign_global_select_review_verdict.txt"


def _load(path):
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    rows.append(json.loads(ln))
                except ValueError:
                    continue
    except OSError:
        return []
    return rows


def _parse(ts):
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d
    except (ValueError, TypeError):
        return None


def summarize(rows, hours, since=None):
    now = datetime.now(timezone.utc)
    cutoff = _parse(since) if since else (now - timedelta(hours=hours))   # --since odcina pre-fix ticki
    cutoff = cutoff or (now - timedelta(hours=hours))
    win = [r for r in rows if (_parse(r.get("ts")) or now) >= cutoff]
    pile = [r for r in win if (r.get("maxpile_before") or 0) >= 2]      # jakikolwiek pile-on
    # NIE ma twardego capu „≤2" (Adrian 29.06: „mogą być i 4 o ile składają się na dobry worek").
    # Silnik (global_allocate→assess_order) dokłada do kuriera DOPÓKI to jego najlepsza WYKONALNA
    # opcja (R6 tier 35/40, dobra trasa) → worek 3-4 na jednego = OK gdy dobry. De-pile usuwa tylko
    # SPURIOUS nadmiar (zlecenia lepsze u innego / nie mieszczące się w dobrym worku). Sukces =
    # REDUKCJA tam gdzie nadmiar, NIE „zejście do 2". maxpile_after = rozmiar zwalidowanego worka.
    big = [r for r in pile if (r.get("maxpile_before") or 0) >= 3]
    big_reduced = [r for r in big if (r.get("maxpile_after") or 0) < (r.get("maxpile_before") or 0)]
    worst_after = max([(r.get("maxpile_after") or 0) for r in win], default=0)
    # STRAŻNIK over-hide: pile-on był (candidates_in≥2) ale 0 survivorów = podejrzenie nadmiernego chowania
    overhide = [r for r in win if (r.get("candidates_in") or 0) >= 2 and (r.get("survivors_out") or 0) == 0]
    biggest = sorted(pile, key=lambda r: -(r.get("maxpile_before") or 0))[:8]
    return {
        "window_h": hours,
        "ticks_in_window": len(win),
        "ticks_with_pileon": len(pile),
        "big_pileon_ge3": len(big),
        "big_reduced": len(big_reduced),
        "worst_maxpile_after": worst_after,
        "maxpile_before_dist": dict(sorted(Counter((r.get("maxpile_before") or 0) for r in win).items())),
        "maxpile_after_dist": dict(sorted(Counter((r.get("maxpile_after") or 0) for r in win).items())),
        "sum_candidates": sum((r.get("candidates_in") or 0) for r in win),
        "sum_survivors": sum((r.get("survivors_out") or 0) for r in win),
        "sum_hidden": sum((r.get("hidden_out") or 0) for r in win),
        "overhide_suspect_ticks": len(overhide),
        "biggest": [{"ts": (r.get("ts") or "")[:19], "before": r.get("maxpile_before"),
                     "after": r.get("maxpile_after"), "survivors": r.get("survivors_out"),
                     "hidden": r.get("hidden_out")} for r in biggest],
    }


def verdict(s):
    if s["ticks_in_window"] == 0:
        return "BRAK DANYCH — żaden tick w oknie (peak bez aktywnych przerzutów?)."
    if s["overhide_suspect_ticks"] > 0 and s["ticks_with_pileon"] and \
       s["overhide_suspect_ticks"] >= max(2, s["ticks_with_pileon"] // 3):
        return (f"⚠ UWAGA OVER-HIDE — {s['overhide_suspect_ticks']} ticków z ≥2 kandydatami ale 0 survivorów. "
                f"Sprawdź czy nie chowa genuine przerzutów (bug singleton). Rozważ rollback flagi.")
    if s["big_pileon_ge3"] > 0:
        return (f"✅ DZIAŁA — {s['big_reduced']}/{s['big_pileon_ge3']} dużych pile-onów (≥3 na jednego) ROZBITYCH "
                f"(spurious nadmiar zdjęty: {s['sum_hidden']} ukrytych de-piled). Worki kept = rozmiar "
                f"zwalidowany feasibility (3-4 na jednego = OK gdy dobry worek, NIE problem). "
                f"maxpile after: {s['maxpile_after_dist']}.")
    if s["ticks_with_pileon"] == 0:
        return "OK (cicho) — w oknie nie było pile-onu na jednego kuriera."
    return (f"OK — pile-ony rozmiaru 2 (walidne worki, brak dużych ≥3 do rozbicia); "
            f"{s['sum_survivors']} pokazanych / {s['sum_hidden']} ukrytych.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=8.0)
    ap.add_argument("--since", default=None, help="ISO cutoff (np. po fixie) — nadpisuje --hours")
    ap.add_argument("--no-telegram", action="store_true")
    args = ap.parse_args()

    s = summarize(_load(JSONL), args.hours, since=args.since)
    v = verdict(s)
    report = {"verdict": v, **s}
    print(json.dumps(report, ensure_ascii=False, indent=2))

    lines = ["🔁 RAPORT po peaku — globalne rozbijanie pile-on PRZERZUTÓW", ""]
    lines.append(f"okno {s['window_h']}h: ticków {s['ticks_in_window']}, z pile-on {s['ticks_with_pileon']}, "
                 f"DUŻYCH(≥3) {s['big_pileon_ge3']}→rozbitych {s['big_reduced']} "
                 f"(worek kept feasibility-validated, 3-4 OK)")
    lines.append(f"kandydaci {s['sum_candidates']} → pokazani {s['sum_survivors']} / ukryci {s['sum_hidden']}")
    lines.append(f"maxpile before {s['maxpile_before_dist']} → after {s['maxpile_after_dist']}")
    if s["overhide_suspect_ticks"]:
        lines.append(f"⚠ over-hide-suspect ticki: {s['overhide_suspect_ticks']}")
    if s["biggest"]:
        lines.append("")
        lines.append("Największe rozbicia (przed→po):")
        for b in s["biggest"][:5]:
            lines.append(f"• {b['ts']}  {b['before']}→{b['after']} (pokazane {b['survivors']}, ukryte {b['hidden']})")
    lines.append("")
    lines.append(v)
    text = "\n".join(lines)

    try:
        with open(OUT_VERDICT, "w", encoding="utf-8") as f:
            f.write(text + "\n\n" + json.dumps(report, ensure_ascii=False, indent=2))
    except OSError:
        pass

    if not args.no_telegram:
        try:
            from dispatch_v2.telegram_utils import send_admin_alert
            send_admin_alert(text, source="reassign_global_select_review")
        except Exception as e:  # noqa: BLE001
            print(f"telegram send fail (plik werdyktu zostaje): {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
