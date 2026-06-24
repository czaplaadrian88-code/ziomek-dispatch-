"""pending_global_resweep_review — jednorazowy raport GO/NO-GO korpusu shadow.

Czyta dispatch_state/pending_global_resweep.jsonl (zbierany co 1 min przez
pending_global_resweep.py) i podsumowuje: ile wiszących ticków, ile by-się-
re-proponowało, rozbicie po reasonach, ile rozjazdów pile-on rozbitych, przykłady.
Drukuje JSON + czytelny tekst i wysyła skrót na Telegram Adriana.

Sugestia GO/NO-GO (heurystyka, nie decyzja):
  GO-kandydat gdy ≥1 dzień z sensowną liczbą would_repropose i rozjazdów pile-on
  (rzeczy które Adrian dziś robił ręcznie). NO-GO/wait gdy ~0 (mechanizm no-op).

Uruchomienie: python -m dispatch_v2.tools.pending_global_resweep_review [--days N]
"""
from __future__ import annotations
import sys
import json
import argparse
from collections import Counter, defaultdict

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

JSONL = "/root/.openclaw/workspace/dispatch_state/pending_global_resweep.jsonl"


def _load(path):
    rows = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return rows


def summarize(rows):
    by_day = defaultdict(list)
    for r in rows:
        day = (r.get("ts") or "")[:10]
        by_day[day].append(r)

    days = {}
    for day, rs in sorted(by_day.items()):
        ticks = {r.get("ts") for r in rs}                       # unikalne sweepy z ≥1 wiszącym
        would = [r for r in rs if r.get("would_repropose")]
        reasons = Counter(r.get("reason") for r in rs)
        would_reasons = Counter(r.get("reason") for r in would)
        spread_ticks = {r.get("ts") for r in rs if r.get("g_spread_improved")}
        would_oids = {r.get("order_id") for r in would}
        days[day] = {
            "ticks_with_hanging": len(ticks),
            "rows": len(rs),
            "would_repropose_rows": len(would),
            "would_repropose_orders": len(would_oids),
            "spread_fix_ticks": len(spread_ticks),
            "reasons_all": dict(reasons),
            "reasons_would": dict(would_reasons),
        }
    return days


def examples(rows, limit=6):
    out = []
    seen = set()
    for r in rows:
        if not r.get("would_repropose"):
            continue
        oid = r.get("order_id")
        if oid in seen:
            continue
        seen.add(oid)
        out.append({
            "ts": r.get("ts"), "order_id": oid, "restaurant": r.get("restaurant"),
            "proposed": r.get("proposed_name"), "new": r.get("new_name"),
            "reason": r.get("reason"), "delta_vs_now": r.get("delta_vs_now"),
            "maxpile": f"{r.get('g_maxpile_before')}→{r.get('g_maxpile_after')}",
        })
        if len(out) >= limit:
            break
    return out


def _verdict(days):
    tot_would = sum(d["would_repropose_orders"] for d in days.values())
    tot_spread = sum(d["spread_fix_ticks"] for d in days.values())
    if tot_would == 0 and tot_spread == 0:
        return "NO-GO/WAIT — korpus pusty lub same no-opy: shadow nie pokazuje wartości (jeszcze)."
    return (f"GO-KANDYDAT — {tot_would} zleceń by re-proponowano, {tot_spread} ticków z rozbiciem "
            f"pile-on. To rzeczy robione dziś ręcznie. Rozważ wpięcie PENDING_RESWEEP_LIVE (edit msg).")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jsonl", default=JSONL)
    ap.add_argument("--no-telegram", action="store_true")
    args = ap.parse_args()

    rows = _load(args.jsonl)
    days = summarize(rows)
    ex = examples(rows)
    verdict = _verdict(days)
    report = {"total_rows": len(rows), "days": days, "examples": ex, "verdict": verdict}
    print(json.dumps(report, ensure_ascii=False, indent=2))

    # Telegram skrót do Adriana
    if not args.no_telegram:
        lines = ["🔬 PENDING-RESWEEP shadow — przegląd GO/NO-GO", ""]
        if not rows:
            lines.append("Korpus pusty (0 wierszy) — żadne zlecenie nie wisiało w oknie pomiaru.")
        else:
            for day, d in days.items():
                lines.append(f"📅 {day}: wiszących ticków {d['ticks_with_hanging']}, "
                             f"by-repropose {d['would_repropose_orders']} zlec., "
                             f"rozjazd pile-on {d['spread_fix_ticks']} ticków")
                if d["reasons_would"]:
                    lines.append(f"   reasony: {d['reasons_would']}")
            if ex:
                lines.append("")
                lines.append("Przykłady (by-repropose):")
                for e in ex[:5]:
                    lines.append(f"• #{e['order_id']} {e['restaurant']}: "
                                 f"{e['proposed']} → {e['new']} ({e['reason']}, pile {e['maxpile']})")
        lines.append("")
        lines.append(verdict)
        try:
            from dispatch_v2.telegram_utils import send_admin_alert
            send_admin_alert("\n".join(lines), source="pending_resweep_review")
        except Exception as e:  # noqa: BLE001
            print(f"telegram send fail: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
