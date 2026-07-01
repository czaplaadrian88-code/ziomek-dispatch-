# FAZA E — adwersaryjna weryfikacja (lens A: REFUTER)
ROOT: objm-shadow-canary-twins-alltick | KLASY E,B,G,I,N,M | R3 | P1(stated)
WERDYKT: CONFIRMED (kernel) | is_source=true | is_open=true | severity-przeszacowane→P2

## KERNEL (NIE DO OBALENIA — dowód drugą metodą)
peak_verdict._g2c_note(g2c) headlineuje metrykę ALL-TICK, a #6a (397a665) naprawił TYLKO monitor.
- DANE: dispatch_state/objm_lexr6_peak_verdict_2026-06-29.txt — w JEDNEJ wiadomości:
  l.19 gate `per-decyzja 3.7% (6/163)` (PRAWDA, monitor po #6a) vs l.10 headline `peak 25.2%, obserwować trend` (ALL-TICK) → 25.2/3.7 = 6.8x (≈ "x7-11").
- monitor.log live: per-decyzja 2.9-5.0% vs all-tick 17.1-19.2% (×~4-6).
- SCHEDULE: `at -c 200` → Fri Jul 3 18:10 UTC uruchamia objm_lexr6_peak_verdict.py + Telegram (nie --dry-run) = "checkpoint at-200". Durable 06-26/28/29 = leci wielokrotnie, NIE martwy one-shot.
- GIT: f112fa0 peak_verdict (06-26 12:03) < 397a665 #6a (06-29 07:05, touched monitor+tests ONLY). Twin pominięty.
- peak_verdict.py:71-72 liczy g2c=all-tick niezależnie; l.81 headline. To WŁASNY nietknięty kod = źródło, nie symptom.

## OBALONE (2 z 3 satelitów bundla)
1. shadow 3-krotka vs kanon 4-krotka (pipeline:1097-1126, objm_lexr6:40-46) = INERT:
   - call-site 6249: `if C.flag("ENABLE_OBJM_LEXR6_SELECT_SHADOW", False)` = False → _objm_lexr6_shadow NIE wykonuje się.
   - kanon 4-krotka tylko gdy ENABLE_POST_SHIFT_OVERRUN_PENALTY (common.py:1891 env default "0"=OFF; brak w flags.json; brak drop-inu; brak w env dispatch-shadow) → kanon też 3-krotka. Zero dywergencji live.
   - zamrożenie ŚWIADOME+udokumentowane (at#152 "walidacji NIE ruszać", budzik at#156). By-design.
2. G2b stale-baseline (monitor:327) — sprzeczne z C7 ("G2b STOP=poprawna bramka NIE bug"). Baseline canary JEST z założenia zamrożony pre-flip (06-25, ack_alert=89.13). Brak tod-awareness=realna drobna słabość, ale NIE to jest kłamiący przyrząd. By-design.

## DISSENT/severity
- LIVE silnik (_objm_lexr6_d2_pick, SELECT=True) używa KANONU objm_lexr6.lex_qual → decyzje zunifikowane/poprawne. Defekt wyłącznie w READ-ONLY narzędziu werdyktu.
- headline NIE zasila GO/STOP/WARN (overall z M.gates = poprawiony per-decyzja); poprawna liczba (l.19) w TEJ SAMEJ wiadomości. Szkoda = mylna NARRACJA dla człowieka na checkpoincie Jul 3, nie skażona decyzja. → P2, nie P1.
- "x7-11" zależne od danych (06-29=6.8×; własny label monitora ×3.5). Kierunek OK, liczba luźna.

## consolidation_target (zasadny dla kernela)
peak_verdict._g2c_note ma dostać per-decyzja (reorder_dec/n_dec ±match) jak monitor — JEDNO źródło. To jedyna realna naprawa; G2b/shadow = NIE ruszać (by-design/inert).
