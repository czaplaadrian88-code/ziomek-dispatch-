#!/usr/bin/env python3
"""Werdykt 2-dniowy flipów SYNCWORKA + LOADGOV (at#137, niedziela 14.06 08:30 Warsaw).

Porównuje okno PO re-flipie (12.06 18:33 UTC, z gate-fixem 30a01d2) z baseline
sprzed flipu (04-11.06). Wysyła podsumowanie na Telegram (send_admin_alert)
z gotową rekomendacją keep / flaga-OFF wg reguły 2-dniowej Adriana.

Metryki (z werdyktów SYNCWORKA_replay_werdykt.md + LOADGOV_werdykt.md):
  1. KOORD-rate ogółem + all_candidates_low_score/dzień (strażnik incydentu 12.06)
  2. spread gotowości zwycięzcy na decyzjach z workiem (mediana, %>10 min)
  3. udział karanych zwycięzców (kara działa = zwycięzcy mają NIŻSZY spread)
  4. loadgov: rozkład EWMA, ile karanych bag≥3, czy alert defensywny odpalił
"""
import json
import sys
from datetime import datetime, timezone

sys.path.insert(0, '/root/.openclaw/workspace/scripts')
from dispatch_v2.tools._rotated_logs import iter_jsonl_records
from dispatch_v2.telegram_utils import send_admin_alert

PATH = '/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl'
PRE_FROM, PRE_TO = '2026-06-04T00:00', '2026-06-11T12:28'
POST_FROM = '2026-06-12T18:33'   # re-flip z gate-fixem


def collect(frm, to=None):
    rows = []
    for r in iter_jsonl_records(PATH):
        ts = r.get('ts') or ''
        if ts >= frm and (to is None or ts < to):
            rows.append(r)
    return rows


def seg(rows):
    n = len(rows)
    prop = [r for r in rows if r.get('verdict') == 'PROPOSE']
    koord = [r for r in rows if r.get('verdict') == 'KOORD']
    low = [r for r in koord if 'all_candidates_low_score' in (r.get('reason') or '')]
    spreads, pen = [], 0
    ewmas, lg_pen = [], 0
    for r in prop:
        b = r.get('best') or {}
        s = b.get('sync_ready_spread_min')
        if s is not None:
            spreads.append(s)
            if (b.get('bonus_sync_spread_shadow_delta') or 0) < 0:
                pen += 1
        e = b.get('loadgov_load_ewma')
        if e is not None:
            ewmas.append(e)
            if (b.get('bonus_loadgov_shadow_delta') or 0) < 0:
                lg_pen += 1
    spreads.sort(); ewmas.sort()
    hours = 1.0
    if rows:
        try:
            t0 = datetime.fromisoformat(rows[0]['ts'])
            t1 = datetime.fromisoformat(rows[-1]['ts'])
            hours = max(1.0, (t1 - t0).total_seconds() / 3600.0)
        except Exception:
            pass
    return {
        'n': n, 'prop': len(prop), 'koord': len(koord),
        'koord_pct': len(koord) / max(1, n) * 100,
        'low': len(low), 'low_per_day': len(low) / hours * 24,
        'spread_med': spreads[len(spreads)//2] if spreads else None,
        'spread_over10_pct': (sum(1 for s in spreads if s > 10) / len(spreads) * 100) if spreads else None,
        'spread_n': len(spreads), 'pen_winners': pen,
        'ewma_med': ewmas[len(ewmas)//2] if ewmas else None,
        'ewma_max': ewmas[-1] if ewmas else None,
        'ewma_over27': sum(1 for e in ewmas if e > 2.7),
        'lg_pen': lg_pen,
    }


def fmt(v, spec='.1f'):
    return format(v, spec) if isinstance(v, (int, float)) else '—'


def main():
    pre = seg(collect(PRE_FROM, PRE_TO))
    post = seg(collect(POST_FROM))

    koord_ok = post['koord_pct'] <= pre['koord_pct'] + 5.0
    low_ok = post['low_per_day'] <= max(15.0, pre['low_per_day'] * 1.3)
    spread_better = (post['spread_med'] is not None and pre['spread_med'] is not None
                     and post['spread_med'] < pre['spread_med'])

    if koord_ok and low_ok:
        rekom = ("✅ Zalecenie: ZOSTAWIĆ obie flagi włączone."
                 + (" Worki są lepiej zsynchronizowane." if spread_better else
                    " (Spread bez wyraźnej poprawy — sprawdź ręcznie nadpisania koordynatora.)"))
    else:
        rekom = ("⛔ Zalecenie: wyłączyć ENABLE_BUNDLE_SYNC_SPREAD (flags.json, "
                 "hot-reload) i wrócić z analizą — strażnik KOORD przekroczony.")

    msg = (
        "📊 Werdykt 2-dniowy: kara za rozjazd worka + bezpiecznik floty\n"
        f"Po włączeniu (od 12.06 20:33, z poprawką progu): {post['n']} decyzji.\n"
        f"• KOORD: {fmt(post['koord_pct'])}% (przed: {fmt(pre['koord_pct'])}%)\n"
        f"• KOORD 'wszyscy poniżej progu': {fmt(post['low_per_day'])} dziennie "
        f"(przed: {fmt(pre['low_per_day'])})\n"
        f"• Rozjazd gotowości zwycięskiego worka: mediana {fmt(post['spread_med'])} min "
        f"(przed: {fmt(pre['spread_med'])}), >10 min: {fmt(post['spread_over10_pct'], '.0f')}% "
        f"(n={post['spread_n']}, karanych zwycięzców {post['pen_winners']})\n"
        f"• Obciążenie floty: mediana {fmt(post['ewma_med'], '.2f')} zlec./kuriera, "
        f"max {fmt(post['ewma_max'], '.2f')}; kar -40: {post['lg_pen']}, próg 2,7 "
        f"przekroczony w {post['ewma_over27']} decyzjach\n"
        f"{rekom}"
    )
    print(datetime.now(timezone.utc).isoformat(), '\n', msg)
    send_admin_alert(msg)


if __name__ == '__main__':
    main()
