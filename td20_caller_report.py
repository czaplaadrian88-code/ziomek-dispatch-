#!/usr/bin/env python3
"""td20_caller_report.py — raport rozkładu caller= dla haversine sentinel (0,0).

tech-debt #20 Krok 1→2 pomost. Instrumentacja (wdrożona 2026-05-18 19:12,
osrm_client.haversine) loguje przy sentinelu (0,0) ramkę wołającego:
  `... haversine sentinel (0,0): ... caller=<plik>:<linia> in <funkcja>()`

Ten skrypt zbiera wszystkie takie linie z dispatch.log(+.1), liczy rozkład
call-site'ów i wysyła podsumowanie na Telegram — żeby Krok 2 (fix u źródła)
ruszał z gotową diagnozą „który z 8 call-site'ów wstrzykuje (0,0)".

Uruchomienie: jednorazowy `at` job 2026-05-19 19:00 UTC (doba danych).
  python3 -m dispatch_v2.td20_caller_report            # wysyła Telegram
  python3 -m dispatch_v2.td20_caller_report --dry-run   # tylko print
"""
import glob
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

LOG_GLOB = "/root/.openclaw/workspace/scripts/logs/dispatch.log*"


def collect():
    """Zwraca (total, {caller: count}, {caller: sample_line})."""
    counts, samples, total = {}, {}, 0
    for path in sorted(glob.glob(LOG_GLOB)):
        if path.endswith(".gz"):
            continue  # instrumentacja <2 dni — nieskompresowane wystarczą
        try:
            with open(path, errors="replace") as f:
                for line in f:
                    if "haversine sentinel" in line and "caller=" in line:
                        total += 1
                        caller = line.split("caller=", 1)[1].strip()
                        counts[caller] = counts.get(caller, 0) + 1
                        samples.setdefault(caller, line.strip())
        except OSError:
            continue
    return total, counts, samples


def build_message(total, counts, samples):
    if total == 0:
        return ("🔎 tech-debt #20 — raport caller= (haversine sentinel)\n\n"
                "0 trafień z `caller=`. Albo instrumentacja nie złapała nic "
                "(mało prawdopodobne — ~60-130/dzień), albo rotacja logu "
                "wypchnęła linie do .gz. Sprawdź ręcznie:\n"
                "grep 'haversine sentinel' logs/dispatch.log*")
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    lines = [f"  {n}×  {caller}" for caller, n in ranked]
    top_caller, top_n = ranked[0]
    top_sample = samples.get(top_caller, "")
    # wyciągnij ll1/ll2 z przykładu dominującego call-site'a
    ll = ""
    if "ll1=" in top_sample:
        ll = top_sample.split("ll1=", 1)[1].split(" caller=", 1)[0]
    return (
        "🔎 tech-debt #20 — raport caller= (haversine sentinel 0,0)\n"
        "Okno: od wdrożenia instrumentacji 2026-05-18 19:12 UTC.\n\n"
        f"Trafień łącznie: {total}\n"
        f"Rozkład call-site'ów:\n" + "\n".join(lines) + "\n\n"
        f"Dominujący: {top_caller} ({top_n}×)\n"
        f"Przykład coords: ll1/ll2 = {ll}\n\n"
        "Krok 2: fix u źródła w sesji Claude — który argument haversine "
        "jest (0,0) (pozycja kuriera / pickup / drop / anchor) → fail-loud "
        "None zamiast (0,0) (Lekcja #81) albo fallback coords. "
        "Detal: memory/tech_debt_backlog.md #20."
    )


def main():
    dry = "--dry-run" in sys.argv
    total, counts, samples = collect()
    msg = build_message(total, counts, samples)
    if dry:
        print(msg)
        return
    try:
        from dispatch_v2.telegram_utils import send_admin_alert
        send_admin_alert(msg)
        print(f"td20_caller_report: wysłano (total={total}, "
              f"call-sites={len(counts)})")
    except Exception as e:
        print(f"td20_caller_report: Telegram send fail: {type(e).__name__}: {e}")
        print(msg)


if __name__ == "__main__":
    main()
