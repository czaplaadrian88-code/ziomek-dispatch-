"""ETAP 6 (Z-19) KROK 3 — backfill naruszeń restauracji ±5 min z historii CSV.

Źródło: /root/panel_history_new/lokalka_zamowienia_2025-11_do_2026-06-09.csv
(56k zleceń, kolumny `czas kuriera` = commit HH:MM, `czas odbioru` = realny
odbiór HH:MM, `oczekiwanie odbiór` = HH:MM:SS zmierzone czekanie kuriera pod
restauracją — proxy przyjazdu tam gdzie niezerowe).

Formuła identyczna z żywym detektorem (sla_tracker._check_restaurant_violations):
  arrival = real − oczekiwanie  (gdy oczekiwanie > 0; inaczej commit fallback)
  wait_min = real − max(commit, arrival);  violation gdy > 5.

Filtr: status=doręczone, bez kont paczkowych. HH:MM bez daty → diff
normalizowany do [-720, 720) (operacja kończy się ~22:00, wraparound bezpieczny).

Wyjście: restaurant_violations_baseline.md (ten katalog).
"""
import csv
from collections import defaultdict
from pathlib import Path

CSV_PATH = "/root/panel_history_new/lokalka_zamowienia_2025-11_do_2026-06-09.csv"
OUT_PATH = Path(__file__).parent / "restaurant_violations_baseline.md"
THRESHOLD_MIN = 5.0
MIN_ORDERS = 30  # ranking tylko restauracje z sensowną próbą

# Konta paczkowe / firmowe (brak termiki, kontrakt ±5 nie dotyczy).
# UWAGA: "Street Mama Thai" to RESTAURACJA — stoplist po pełnej nazwie.
PACZKA_NAMES = {
    "nadajesz.pl", "dr tusz", "dentomax", "orthdruk", "interpap polska",
    "3giga", "bravilor", "street-sport", "street sport", "mali wojownicy",
    "matka polka hybrydowa", "nadzwyczajnie", "adam chwiesko",
}


def _hhmm_to_min(s):
    try:
        h, m = s.strip().split(":")[:2]
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def _hms_to_min(s):
    try:
        parts = [int(x) for x in s.strip().split(":")]
        h, m, sec = (parts + [0, 0, 0])[:3]
        return h * 60 + m + sec / 60.0
    except (ValueError, AttributeError):
        return None


def _signed_diff(a_min, b_min):
    """a − b w minutach, znormalizowane do [-720, 720)."""
    return ((a_min - b_min + 720) % 1440) - 720


def main():
    per_rest = defaultdict(lambda: {"n": 0, "waits": [], "src_status4": 0})
    skipped_paczka = 0
    rows_used = 0
    with open(CSV_PATH, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row.get("status") != "doręczone":
                continue
            rest = (row.get("nazwa restauracji") or "?").strip()
            if rest.lower() in PACZKA_NAMES:
                skipped_paczka += 1
                continue
            commit = _hhmm_to_min(row.get("czas kuriera"))
            real = _hhmm_to_min(row.get("czas odbioru"))
            if commit is None or real is None:
                continue
            rows_used += 1
            wait_at_rest = _hms_to_min(row.get("oczekiwanie odbiór")) or 0.0
            # arrival = real − oczekiwanie; max(commit, arrival) jako kotwica
            vs_commit = _signed_diff(real, commit)
            if wait_at_rest > 0:
                anchor_wait = min(vs_commit, wait_at_rest)
                src4 = True
            else:
                anchor_wait = vs_commit
                src4 = False
            st = per_rest[rest]
            st["n"] += 1
            if anchor_wait > THRESHOLD_MIN:
                st["waits"].append(anchor_wait)
                if src4:
                    st["src_status4"] += 1

    rows = []
    for rest, st in per_rest.items():
        if st["n"] < MIN_ORDERS:
            continue
        nv = len(st["waits"])
        if nv == 0:
            continue
        ws = sorted(st["waits"])
        med = ws[len(ws) // 2] if len(ws) % 2 else (ws[len(ws) // 2 - 1] + ws[len(ws) // 2]) / 2
        p90 = ws[min(len(ws) - 1, int(0.9 * len(ws)))]
        rows.append((rest, st["n"], nv, 100.0 * nv / st["n"], med, p90, st["src_status4"]))

    rows.sort(key=lambda r: -r[2])
    total_orders = sum(st["n"] for st in per_rest.values())
    total_viol = sum(len(st["waits"]) for st in per_rest.values())

    lines = [
        "# Baseline naruszeń restauracji ±5 min — backfill z historii CSV",
        "",
        f"Źródło: `{CSV_PATH}` (2025-11 → 2026-06-09). "
        f"Formuła = żywy detektor ETAP 6: `wait = real_pickup − max(commit, przyjazd)`, "
        f"violation > {THRESHOLD_MIN:.0f} min. Przyjazd: `real − oczekiwanie odbiór` gdy "
        "panel zmierzył czekanie (≈21% wierszy), inaczej commit fallback.",
        "",
        f"- Zleceń (doręczone, bez paczek): **{rows_used}** "
        f"(paczki/firmowe pominięte: {skipped_paczka})",
        f"- Naruszeń łącznie: **{total_viol}** = "
        f"{100.0 * total_viol / max(total_orders, 1):.1f}% zleceń",
        f"- Ranking: restauracje z ≥{MIN_ORDERS} zleceniami",
        "",
        "| # | Restauracja | Zleceń | Naruszeń | % | Mediana wait | p90 | w tym z pomiarem czekania |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i, (rest, n, nv, pct, med, p90, s4) in enumerate(rows[:40], 1):
        lines.append(
            f"| {i} | {rest} | {n} | {nv} | {pct:.1f}% | "
            f"{med:.0f} min | {p90:.0f} min | {s4} |"
        )
    lines += [
        "",
        "Ograniczenia: HH:MM bez daty (wraparound znormalizowany ±12h); "
        "`oczekiwanie odbiór` zależy od użycia statusu 4 przez kuriera "
        "(0 ≠ brak czekania, tylko brak pomiaru → wtedy commit fallback, "
        "jak w żywym detektorze); % liczony na doręczonych.",
    ]
    OUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"OK → {OUT_PATH} ({len(rows)} restauracji w rankingu, "
          f"{total_viol}/{rows_used} naruszeń)")


if __name__ == "__main__":
    main()
