#!/usr/bin/env python3
"""Strażnik wieczorny gate-fixa 30a01d2 (incydent SYNCWORKA KOORD 50%).

Uruchamiany jednorazowo przez at 21:30 UTC 12.06 (23:30 Warsaw). Read-only +
1 TG. Sprawdza okno post-fix (od 18:33 UTC) na shadow_decisions:
  - KOORD-rate (alarm > 25% — baseline sprzed incydentu ~15,6%),
  - liczba KOORD all_candidates_low_score (alarm: > 20% wszystkich decyzji),
  - sanity: kary sync/loadgov aplikowane w rankingu przy verdict=PROPOSE
    (dowód że bramka liczy score bez delt).
Cel: złapać porażkę fixa PRZED sobotnim peakiem 13.06. Werdykt 2-dniowy
robi osobno at#137 (nd 14.06, syncworka_loadgov_verdict_check.py).
"""
import json
import sys

sys.path.insert(0, '/root/.openclaw/workspace/scripts')
from dispatch_v2.tools._rotated_logs import iter_jsonl_records  # noqa: E402
from dispatch_v2.telegram_utils import send_admin_alert  # noqa: E402

PATH = '/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl'
POST_FROM = '2026-06-12T18:33'


def main():
    n = koord = low = propose = sync_pen_propose = lg_pen_propose = 0
    for d in iter_jsonl_records(PATH):
        if (d.get('ts') or '') < POST_FROM:
            continue
        n += 1
        if d.get('verdict') == 'KOORD':
            koord += 1
            if 'all_candidates_low_score' in (d.get('reason') or ''):
                low += 1
        else:
            propose += 1
            b = d.get('best') or {}
            if (b.get('bonus_sync_spread') or 0) != 0:
                sync_pen_propose += 1
            if (b.get('bonus_loadgov_shadow_delta') or 0) < 0:
                lg_pen_propose += 1

    if n == 0:
        send_admin_alert('🌙 Gate-fix check 23:30: zero decyzji od 18:33 UTC — brak danych, sprawdź jutro rano.')
        return
    kr = 100.0 * koord / n
    lr = 100.0 * low / n
    ok = kr <= 25.0 and lr <= 20.0
    head = '✅ Gate-fix 30a01d2 DZIAŁA' if ok else '🔴 ALARM: gate-fix NIE domknął incydentu'
    msg = (
        f"{head} (okno post-fix 18:33→teraz, n={n})\n"
        f"• KOORD: {koord} ({kr:.1f}%) — baseline ~15,6%, incydent miał 50%\n"
        f"• w tym all_candidates_low_score: {low} ({lr:.1f}%)\n"
        f"• PROPOSE z karą sync w rankingu: {sync_pen_propose}/{propose} (kara działa, nie wpycha w ciszę)\n"
        f"• PROPOSE z karą loadgov: {lg_pen_propose}/{propose}\n"
    )
    if not ok:
        msg += "→ Rozważ na noc: ENABLE_BUNDLE_SYNC_SPREAD=false w flags.json (hot) przed sobotnim peakiem."
    send_admin_alert(msg)
    print(msg)


if __name__ == '__main__':
    main()
