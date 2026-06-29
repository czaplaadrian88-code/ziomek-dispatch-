#!/usr/bin/env python3
"""Przegląd shadow-detektora ulica↔miasto (b2, flaga ENABLE_ADDRESS_TOWN_MISMATCH_SHADOW).

Czyta dispatch_state/address_mismatch_shadow.jsonl (zapis: maybe_log_mismatch) i raportuje
ile rozjazdów/dzień, w jakich ulicach/miastach, ile zleceń — żeby zdecydować:
  A) wpiąć REALNY alert koordynatora (mismatch przy ingestii gastro = częsty zły geokod),
  B) zbudować pełną walidację ulica↔miasto (363 pary) jako twardą bramkę,
  C) zostawić jako shadow (rzadkie / głównie false-positive).

Read-only. Wynik na stdout + plik werdyktu; --notify = też Telegram (send_admin_alert).
Uruchamiać ABSOLUTNĄ ścieżką (cwd-niezależne).
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
LOG = Path("/root/.openclaw/workspace/dispatch_state/address_mismatch_shadow.jsonl")
VERDICT = Path("/root/.openclaw/workspace/dispatch_state/address_mismatch_review_verdict.txt")


def _load(since_ts: float | None) -> list[dict]:
    if not LOG.exists():
        return []
    out: list[dict] = []
    for line in LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except (ValueError, TypeError):
            continue
        if since_ts is not None and float(r.get("ts", 0)) < since_ts:
            continue
        out.append(r)
    return out


def _day(ts: float) -> str:
    return datetime.fromtimestamp(float(ts), WARSAW).strftime("%Y-%m-%d")


def build_report(since_ts: float | None) -> str:
    # Tylko wpisy ulica↔miasto (text_coords = osobny detektor, własna sekcja niżej).
    rows = [r for r in _load(since_ts) if r.get("check") != "text_coords"]
    L: list[str] = []
    L.append("🔎 PRZEGLĄD shadow ulica↔miasto (b2 ENABLE_ADDRESS_TOWN_MISMATCH_SHADOW)")
    if not rows:
        L.append("Brak wpisów w logu (0 rozjazdów wykrytych w oknie).")
        L.append("→ WERDYKT: C (zostaw shadow) — albo zbyt krótkie okno / zbyt mało ruchu gastro.")
        return "\n".join(L)

    per_day = Counter(_day(r["ts"]) for r in rows)
    pairs = Counter((r.get("street", "?"), r.get("town", "?")) for r in rows)
    orders = {r.get("order_id") for r in rows if r.get("order_id")}
    n_days = max(1, len(per_day))
    per_day_avg = len(rows) / n_days

    L.append(f"Okno: {min(per_day)}..{max(per_day)} ({n_days} dni z wpisami) | wpisów: {len(rows)} "
             f"| śr/dzień: {per_day_avg:.1f} | distinct zleceń: {len(orders)} | distinct par: {len(pairs)}")
    L.append("")
    L.append("Top rozjazdy (ulica → wybrane miasto ×N):")
    for (street, town), n in pairs.most_common(12):
        sample = next((r for r in rows if r.get("street") == street and r.get("town") == town), {})
        bia = sample.get("street_bialystok_count", "?")
        L.append(f"  {n:>3}× „{street}” → „{town}”  (ulica w Białymstoku {bia}×, suggest={sample.get('suggest_town')})")
    L.append("")
    L.append("Wpisy/dzień:")
    for d in sorted(per_day):
        L.append(f"  {d}: {per_day[d]}")

    # heurystyka werdyktu
    L.append("")
    distinct_pairs = len(pairs)
    if per_day_avg >= 3:
        L.append(f"→ WERDYKT (heurystyka): A — {per_day_avg:.1f}/dzień to materialny strumień złych "
                 f"geokodów u ingestii gastro. Rozważ realny alert koordynatora (mismatch → KOORD/flaga) "
                 f"+ ewentualnie B (walidacja 363 par jako twarda bramka).")
    elif per_day_avg >= 0.7:
        L.append(f"→ WERDYKT (heurystyka): A-light — {per_day_avg:.1f}/dzień, warto wpiąć cichy alert "
                 f"koordynatora (bez twardej bramki) i obserwować dalej; sprawdź {distinct_pairs} par pod kątem "
                 f"false-positive (ulica spoza Białegostoku o tej samej nazwie).")
    else:
        L.append(f"→ WERDYKT (heurystyka): C — rzadkie ({per_day_avg:.1f}/dzień). Zostaw shadow, "
                 f"przejrzyj próbki pod kątem realności; brak pilnej potrzeby twardej bramki.")
    L.append("⚠ Bramka kodu/flip = człowiek (Adrian). Heurystyka tylko wskazuje kierunek.")
    return "\n".join(L)


def build_coords_report(since_ts: float | None) -> str:
    """Sekcja text↔pin (ENABLE_ADDRESS_COORDS_MISMATCH_SHADOW): rozjazd napisanego
    adresu vs współrzędne trasy (case 484269 'Można'≠'Mroźna', 4,26 km)."""
    rows = [r for r in _load(since_ts) if r.get("check") == "text_coords"]
    L: list[str] = []
    L.append("")
    L.append("🔎 PRZEGLĄD shadow TEKST↔PIN (ENABLE_ADDRESS_COORDS_MISMATCH_SHADOW)")
    if not rows:
        L.append("Brak wpisów (0 rozjazdów tekst↔pin w oknie) — albo krótkie okno / mało ruchu.")
        return "\n".join(L)
    per_day = Counter(_day(r["ts"]) for r in rows)
    orders = {r.get("order_id") for r in rows if r.get("order_id")}
    streets = Counter(r.get("street", "?") for r in rows)
    n_days = max(1, len(per_day))
    per_day_avg = len(rows) / n_days
    dists = sorted(float(r.get("distance_m", 0)) for r in rows)
    med = dists[len(dists) // 2] if dists else 0.0
    L.append(f"Okno: {min(per_day)}..{max(per_day)} ({n_days} dni) | wpisów: {len(rows)} "
             f"| śr/dzień: {per_day_avg:.1f} | distinct zleceń: {len(orders)} "
             f"| mediana rozjazdu: {med:.0f} m | max: {dists[-1]:.0f} m")
    L.append("")
    L.append("Top rozjazdy (napisana ulica ×N — pin gdzie indziej):")
    for street, n in streets.most_common(12):
        sample = next((r for r in rows if r.get("street") == street), {})
        L.append(f"  {n:>3}× „{street}”  (rozjazd ~{float(sample.get('distance_m', 0)):.0f} m, "
                 f"oid={sample.get('order_id')})")
    L.append("")
    if per_day_avg >= 3:
        L.append(f"→ WERDYKT (heurystyka): A — {per_day_avg:.1f}/dzień materialny strumień kłamiących "
                 f"adresów. Rozważ alert koordynatora „adres niepewny” + fix u źródła "
                 f"(gastro_edit.regeocode_and_update sync tekstu z coords).")
    elif per_day_avg >= 0.7:
        L.append(f"→ WERDYKT (heurystyka): A-light — {per_day_avg:.1f}/dzień; cichy alert + obserwacja, "
                 f"przejrzyj próbki pod false-positive (legit inne miasto / alias ulicy).")
    else:
        L.append(f"→ WERDYKT (heurystyka): C — rzadkie ({per_day_avg:.1f}/dzień), zostaw shadow.")
    L.append("⚠ Bramka kodu/flip = człowiek (Adrian). Heurystyka tylko wskazuje kierunek.")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="YYYY-MM-DD (Warsaw) — tylko wpisy od tej daty")
    ap.add_argument("--notify", action="store_true", help="wyślij też na Telegram (send_admin_alert)")
    args = ap.parse_args()

    since_ts = None
    if args.since:
        since_ts = datetime.strptime(args.since, "%Y-%m-%d").replace(tzinfo=WARSAW).timestamp()

    report = build_report(since_ts) + "\n" + build_coords_report(since_ts)
    print(report)
    try:
        VERDICT.write_text(report + "\n", encoding="utf-8")
    except OSError:
        pass

    if args.notify:
        try:
            import sys
            sys.path.insert(0, "/root/.openclaw/workspace/scripts")
            from dispatch_v2.telegram_utils import send_admin_alert
            send_admin_alert(report)
        except Exception as e:  # noqa: BLE001
            print(f"[notify fail] {e}")


if __name__ == "__main__":
    main()
