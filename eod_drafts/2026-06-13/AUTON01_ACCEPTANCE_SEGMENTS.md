# AUTON-01 × PANEL_AGREE — analiza segmentów acceptance (12/13.06, nocna sesja)

**Cel (z promptu nocnego):** AUTO ma celować w podzbiór o WYSOKIM acceptance —
skonfrontować bramki AUTON-01 z żywymi danymi PANEL_AGREE od 10.06 19:00.
**Dane:** 245 akcji (69 AGREE / 176 OVERRIDE = **28,2%**), join z ostatnią decyzją
PROPOSE per order w shadow_decisions (245/245 sjoinowane, 0 braków); margin =
`auto_route_score_margin` z kontekstu klasyfikatora (semantyka Z-10, flaga ON od 10.06).
Caveat: n małe (2 dni), AGREE = zgodność w MOMENCIE przypisania.

## 1. Trzy twarde fakty dla designu/E7

**(a) Klasyfikator Fazy 7 NIE wybiera podzbioru o wysokim acceptance.**
Acceptance per auto_route: AUTO **27% (4/15)** · ALERT 30% (38/125) · ACK 26% (27/105).
Klasa AUTO = dokładnie średnia. Próg flipu „≥75% acceptance w podzbiorze would_auto"
jest dziś nieosiągalny o czynnik ~3. (Spójne z F7-baseline: AUTO miało historycznie
NAJGORSZĄ zgodność 13% — Z-10 poprawił ją do średniej, nie powyżej.)

**(b) Pełen stos bramek AUTON-01 redukuje populację do ~zera.**
Aproksymacja `route==AUTO ∧ pool_feasible≥3 ∧ score≤90` na 245 decyzjach: **n=1, 0% AGREE.**
Przy tym tempie bramka „≥200 decyzji would_auto w shadow" = ~rok zbierania. Po dodaniu
G7 (pos informed — gps w best to dziś 14/140/dzień, patrz GPS_ADOPTION_DIAGNOSIS.md)
populacja spada w praktyce do zera. **Wniosek: telemetria `would_auto_assign` będzie
~zawsze false; kalibracja E7 musi pracować na ROZKŁADZIE `auto_block_reasons`
(które bramki wycinają ile), nie na podzbiorze przechodzącym wszystko.**

**(c) Whitelist T1 (gold/std+) celuje w najgorsze tiery acceptance.**
Per tier: **gold 16% (16/101)** · std+ 33% (8/24) · **std 38% (33/87)** · new 41% (7/17) ·
slow 31% (5/16). Live potwierdza i wzmacnia anomalię z baseline (gold 12,7% < std+ 25,3%).
Koordynator systematycznie NIE daje goldom tego, co proponuje Ziomek (gold = najgłębsze
worki → człowiek rotuje gdzie indziej; hipoteza B3-rotacja). **Rekomendacja do E7:
T1-AUTO startowo std/std+, NIE gold/std+** — lub wyjaśnić anomalię gold przed flipem.

## 2. Pozostałe cięcia (n=245)

| Segment | Acceptance |
|---|---|
| slot peak (11-14/17-20) | 30% (41/138) |
| slot off | **38% (22/58)** |
| slot high_risk 14-17 | **12% (6/49)** — zaostrzenie HIGH_RISK w klasyfikatorze ma poparcie w danych |
| pos pre_shift | 41% (12/29) |
| pos gps | 36% (9/25) |
| pos post_wave / last_assigned | 32-33% |
| pos last_picked_up | 18% (12/65) |
| pos no_gps | 13% (3/23) |
| best_is_score_top True/False | 28% / 29% (C7 bez wpływu na acceptance) |
| **margin Z-10**: <0 / [0,5) / [5,15) / [15,30) / [30,60) / ≥60 | 29% / 36% / 15% / 36% / 36% / **21%** |
| score: <0 / [0,30) / [30,60) / [60,90) / ≥90 | 30% / 36% / 25% / 25% / 23% |

**Margin NIE przewiduje acceptance** (najwyższy margin ≥60 → 21%, niski [0,5) → 36%).
To lustrzane odbicie tezy Bartka 2.0 („score nie przewiduje wyniku") po stronie
zgodności z człowiekiem: progi margin/score jako rdzeń bramki AUTO selekcjonują
co najwyżej PEWNOŚĆ RANKINGU, nie zgodność ani jakość. Najlepsze segmenty acceptance
to cechy KONTEKSTU: tier std × slot off **53% (9/17)**, std × peak 40% (23/58),
pre_shift/gps position — nie wysokość marginu.

## 3. Co z tym zrobić (wejście do E7 at#131 17.06; zero zmian tej nocy)

1. Kalibrację AUTO oprzeć na **rozkładzie auto_block_reasons** + acceptance per
   ODBLOKOWYWANY segment (zdejmowanie bramek po jednej), nie na czekaniu aż
   pełny stos przepuści 200 decyzji.
2. Rozważyć **acceptance-first targeting**: startowy podzbiór AUTO = segmenty
   empirycznie wysokiego acceptance (std/std+ × off-peak × pos informed/pre_shift),
   z progami margin jako bezpiecznikiem wtórnym — zamiast margin-first.
3. Anomalia gold (16%) do wyjaśnienia PRZED jakimkolwiek flipem — może być
   artefaktem rotacji B3 (fairness), wtedy AUTO na goldach aktywnie psułoby
   dystrybucję zarobków floty.
4. Bramka G7 (pos informed) przy dzisiejszej adopcji GPS (1 nadawca) = de facto
   wyłącznik całości — patrz GPS_ADOPTION_DIAGNOSIS.md §5: adopcja apki jest
   prerekwizytem autonomii, nie równoległym wątkiem.
5. Po 7 dniach telemetrii would_auto/auto_block_reasons powtórzyć tę analizę
   na większym n (skrypt: join learning_log PANEL_* × shadow per oid — 1:1
   odtwarzalny z tego pliku).

---
*Read-only; surowe rekordy joinu: /tmp/auton01_rows.json (245). Cross-ref:
AUTON01_DESIGN.md §4 (bramki flipu), R04_ENFORCE_VERDICT.md (tiery), lekcja #188.*
