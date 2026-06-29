# FRESH-SANITY-DATE-01 — decyzja zamknięta (2026-06-08)

**Problem:** `ENABLE_OBJ_PICKUP_FRESHNESS` miał sanity „~06.06, off jeśli słabo" — bez
at-joba/zapisanej decyzji. Data minęła, flaga wisiała =1 w shadow. Zamknięte dziś.

## Ustalenia
1. **Auto-werdykt 06-06** (`eod_drafts/2026-05-30/obj_fresh_verdict.md`): ogon odbioru >10min
   20.5% vs baseline 17.5% (Δ+3pp) → „NIE spadł → FLAGA OFF". (baseline skonfundowany pre-V4)
2. **Rygorystyczny replay 06-01** (`eod_drafts/2026-06-01/OBJ_FRESH_variant_verdict.md`, 190 tras
   parowanych): **V6 (freshness OFF + R6-deliv ON) = najlepszy na KAŻDEJ metryce** carry/R6/geometrii
   (carry>35 31→14%, R6 breaches 79→36, front 45→32%). Koszt = odbiór >10min 11→25%, zgodny z
   dyrektywą Adriana „odroczony odbiór OK, nie wozić". Rekomendacja była V6, wdrożono konserwatywny V4.
3. **Architektura (SKORYGOWANE — wcześniejsze „shadow-only" było BŁĘDNE)**: `shadow_dispatcher`
   (dispatch-shadow) LICZY `assess_order` z flagami objective → pisze `shadow_decisions.jsonl`;
   `telegram_approver` (dispatch-telegram) czyta ten log OGONEM (shadow_tailer) i wysyła PROPOSE
   koordynatorowi. „Shadow" = nazwa historyczna Fazy 1 (Ziomek imituje koordynatora, koordynator
   wciąż RĘCZNIE zatwierdza), NIE czysty obserwator. ⇒ ta flaga **ZMIENIA realne propozycje**.
   Bezpiecznik: człowiek gateuje każdą propozycję. Restart dispatch-shadow = ~5s przerwy w
   ocenianiu (event_bus resume z kolejki, 0 zgubionych). dispatch-telegram NIE restartowany.

## Decyzja: ENABLE_OBJ_PICKUP_FRESHNESS = OFF (shadow V4→V6)
Wykonane 2026-06-08 17:00 UTC: zakomentowana linia w `dispatch-shadow.service.d/override.conf`
(+ komentarz uzasadniający), daemon-reload, restart dispatch-shadow (NRestarts=0, ExecMainStatus=0,
ortools 51ms, login fresh). Backup `override.conf.bak-pre-freshness-off-2026-06-08`. R6 soft ZOSTAJE ON.
Rollback: odkomentuj linię + reload + restart.

## Po co (następny krok)
Shadow zbiera teraz dane V6 na żywych peakach = krok 2 z planu 06-01 („shadow-validation V6 przed
flipem live"). Po kilku peakach: jeśli V6 trzyma carry/R6 → kandydat do PROMOCJI na live
(ustawić `ENABLE_OBJ_R6_SOFT_DEADLINE=1` w dispatch-telegram — to OSOBNA decyzja, dotyka realnych
propozycji, wymaga ACK + off-peak restart). Powiązane: CARRY-OVERLAP-01.
