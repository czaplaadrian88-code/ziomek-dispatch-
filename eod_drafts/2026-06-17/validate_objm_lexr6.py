#!/usr/bin/env python3
"""VALIDATION (Faza 1 → bramka §6 specu) — czyta ŻYWĄ telemetrię objm_lexr6_* zebraną od flipu
17.06 i sprawdza czy selekcja D2 jest czystym zyskiem. Uruchamiane lokalnie na serwerze (musi
czytać shadow_decisions.jsonl tej maszyny). Werdykt: czy budować Fazę 2 (live-flip)."""
import json, glob, os, gzip
from datetime import datetime

LOGS = "/root/.openclaw/workspace/scripts/logs"
# glob WSZYSTKIE rotacje (7 dni może objąć .jsonl + .1 + .2.gz); post-flip rekordy filtrowane
# po obecności pola objm_lexr6_best_cid, więc stare rotacje bez pola są nieszkodliwe.
FILES = sorted(glob.glob(f"{LOGS}/shadow_decisions.jsonl*"))

def _iter_lines(path):
    """Yield linie rotacji logu jako text — gzip dla .gz, zwykle dla reszty.
    logrotate kompresuje stare rotacje (.gz); plain open() na .gz wcześniej
    wywalał cały run UnicodeDecodeError przy `for line in f` (poza try/except).
    errors='replace' + per-rotację try/except = jedna uszkodzona rotacja nie
    kładzie całej walidacji (fail-soft, spójne z resztą skryptu)."""
    opener = gzip.open if path.endswith(".gz") else open
    try:
        with opener(path, "rt", encoding="utf-8", errors="replace") as f:
            for line in f:
                yield line
    except Exception as e:
        print(f"[pominięto rotację {path}: {e!r}]")
MIN_DECISIONS = 200          # próg istotności
MIN_NET_GAIN = 50.0          # Σ(R6+committed) musi spaść o ≥50 min (improvement)
MAX_REGR_RATE = 0.01         # <1% flipów może pogarszać twardą regułę

def num(x, d=0.0):
    try: return float(x) if x is not None else d
    except: return d

def run():
    n=0; flips=0; regr=0
    sR6=sCom=sNew=sW=0.0
    feas_flips=0
    for path in FILES:
        if not os.path.exists(path): continue
        for line in _iter_lines(path):
            if not line.strip(): continue
            try: r=json.loads(line)
            except: continue
            b=r.get("best") or {}
            if "objm_lexr6_best_cid" not in b: continue   # tylko post-flip telemetria
            n+=1
            if not b.get("objm_lexr6_flip"): continue
            flips+=1
            dR6=num(b.get("objm_lexr6_d_r6_breach")); dCom=num(b.get("objm_lexr6_d_committed"))
            sR6+=dR6; sCom+=dCom
            sNew+=num(b.get("objm_lexr6_d_new_late")); sW+=num(b.get("objm_lexr6_d_idle"))
            if dR6>1.0 or dCom>1.0: regr+=1   # D2-pick gorszy na twardej osi = regresja
            if str(b.get("feasibility","")).upper() in ("YES","MAYBE"): feas_flips+=1

    net = sR6+sCom
    regr_rate = regr/n if n else 0.0
    g1 = net <= -MIN_NET_GAIN
    g2 = regr_rate < MAX_REGR_RATE
    g3 = n >= MIN_DECISIONS
    # n<MIN_DECISIONS = za mało danych (np. odpalenie tuż po nocnej rotacji logu, gdy
    # świeży .jsonl ma kilka linii) → to NIE jest HOLD bramek, tylko brak próby.
    # Bez tego rozróżnienia walidator wysyłał fałszywy "HOLD | flipy 0/3" (2026-06-25).
    insufficient = not g3
    if insufficient:
        verdict = f"INCONCLUSIVE — za mało danych (n={n} < {MIN_DECISIONS}, prawdop. tuż po rotacji logu)"
    elif g1 and g2:
        verdict = "PASS — buduj Fazę 2 (live-flip za ACK)"
    else:
        verdict = "HOLD — bramki nie spełnione"

    out=[]
    out.append(f"# WALIDACJA objm-lexr6 D2-shadow — {datetime.now().isoformat(timespec='seconds')}")
    out.append("")
    out.append(f"Decyzje z telemetrią (od flipu 17.06): **{n}**")
    out.append(f"Flipy D2≠live: **{flips}** ({100*flips/n:.1f}% decyzji)" if n else "brak danych")
    out.append(f"  z czego bierze kuriera feasible (na czas): {feas_flips}")
    out.append("")
    out.append("## Bramki (spec §6)")
    out.append(f"- G1 Σ(d_r6_breach+d_committed) = **{net:.0f} min** (cel ≤ -{MIN_NET_GAIN:.0f}) → {'✅' if g1 else '❌'}")
    out.append(f"     (R6-breach {sR6:.0f} min, committed {sCom:.0f} min)")
    out.append(f"- G2 regresje = {regr}/{n} = **{100*regr_rate:.2f}%** (cel < {100*MAX_REGR_RATE:.0f}%) → {'✅' if g2 else '❌'}")
    out.append(f"- G3 próbka ≥ {MIN_DECISIONS}: **{n}** → {'✅' if g3 else '❌'}")
    out.append(f"- KOSZT (do akceptacji Adriana): new-pickup-late {sNew:+.0f} min, idle {sW:+.0f} min (ujemne = też zysk)")
    out.append("")
    out.append(f"## WERDYKT: {verdict}")
    out.append("")
    out.append("⚠ Brama outcome (czy +new-late psuje R6 NOWYCH zleceń downstream) = osobny join z")
    out.append("backfill_decisions_outcomes_v1.jsonl — sprawdź ręcznie przed Fazą 2.")
    txt="\n".join(out)
    print(txt)
    rp=f"/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-17/VALIDATION_RESULT.md"
    with open(rp,"w") as g: g.write(txt+"\n")
    print(f"\n[zapisano {rp}]")

    # opcjonalnie: powiadom panel (notify_router LOW → zakładka Powiadomienia), fail-soft.
    # Przy n<MIN_DECISIONS (artefakt rotacji logu) NIE wysyłaj — to brak danych, nie alarm.
    try:
        import sys; sys.path.insert(0,"/root/.openclaw/workspace/scripts")
        from dispatch_v2.telegram_utils import send_admin_alert
        if insufficient:
            print(f"[notify pominięte: za mało danych n={n}<{MIN_DECISIONS} — artefakt rotacji, nie alarmuję]")
        else:
            send_admin_alert(f"[objm-lexr6 walidacja] {verdict} | net {net:.0f}min | flipy {flips}/{n} | regr {100*regr_rate:.1f}%", priority="low")
    except Exception as e:
        print(f"[notify pominięte: {e!r}]")

if __name__=="__main__":
    run()
