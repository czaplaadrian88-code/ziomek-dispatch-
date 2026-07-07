# S27-A — WERDYKT O2-K1 (`ENABLE_O2_CAPZ_RESEQ`)

> **Autor:** Sprint 27-A (FLIPMASTER, dyżur na żądanie ①) · **Zamknięty:** 2026-07-07 19:26 UTC (21:26 Warsaw, wt) · **Tryb: READ-ONLY** (0 flipów / 0 restartów / 0 zapisów flags.json / 0 Telegram).
> **Flip oceniany:** `ENABLE_O2_CAPZ_RESEQ = True`, LIVE od **2026-07-07 19:05:00 UTC** (21:05 Warsaw, off-peak). Wykonany przez sesję flipową (skrypt `flip_o2k1_20260707.py`, ACK Adriana z sprintu multiagent).

## 🟢 WERDYKT: ZDROWY (early-window, 0 uwag krytycznych)

Flip czysty, silnik bez błędów, reguła wpięta i wykonuje się, parytet OFF↔ON udowodniony pomiarem, bliźniacza ścieżka (plan-recheck) zdrowa. **Brak jakiegokolwiek sygnału regresji.** Okno na moment werdyktu = ~21 min; **domknięte monitorem at-212 do pełnej godziny (65 min, 0 błędów — sekcja niżej).** Off-peak, niski wolumen → werdykt „ZDROWY z obserwacją do domknięcia 2-dniowego" (peak Śr 08.07 pokaże `applied>0`; patrz „Co jeszcze dojrzeje").

---

## Dowody (nie deklaracje)

| # | Kontrola | Wynik | Ocena |
|---|---|---|---|
| 1 | Flaga LIVE | `ENABLE_O2_CAPZ_RESEQ=True`; flags.json mtime **19:05:00 UTC** = czas flipu; 273 klucze (272+1, klucz DOPISANY); backup `flags.json.bak-pre-o2k1-20260707-2105` obecny | ✅ |
| 2 | Bramka off-peak dotrzymana | log flipu: 18:50 **HOLD PEAK** (Warsaw 20:50 → nie flipnął), 19:05 **FLIP OK** (Warsaw 21:05, off-peak) | ✅ (guard zadziałał) |
| 3 | Silnik żywy, bez restartu/crasha | `dispatch-shadow` active, **NRestarts=0**, MainPID 3531955 stabilny od 06:47 UTC (przed flipem = brak restartu przy flipie, zgodnie z HOT) | ✅ |
| 4 | Błędy w journalu od flipu | `journalctl dispatch-shadow --since 19:05` → **0** error/traceback/exception/critical (51 linii logu = normalny tick) | ✅ |
| 5 | Świeżość decyzji | shadow_decisions.jsonl ostatni rekord **19:20:14 UTC** (oid 486234, verdict PROPOSE) — silnik produkuje żywe decyzje | ✅ |
| 6 | **ON≠OFF udowodnione** | metryka `o2_capz` w shadow_decisions: **0 rekordów PRZED** flipem (19:05), **3 rekordy PO** — reguła emituje telemetrię tylko gdy wpięta = efekt flagi realny, ~1 tick jak obiecano | ✅ |
| 7 | Reguła się wykonuje | agregat po flipie (3 decyzje, 100% pokrycia): `considered=282`, `blocked_by_cap=89` (cap wieku carried działa), `applied=0`, `detour_min=0.0`, `overage_saved_min=0.0` (spójne z applied=0) | ✅ (logika żywa) |
| 8 | Brak carried-first / regres | `journalctl dispatch-shadow` od flipu: **0** trafień `carried.first / regres / warn / hard.reject / sla_breach` | ✅ |
| 9 | Bliźniacza ścieżka | `dispatch-plan-recheck.timer` active, biegi 19:11/19:16/19:21/19:26 wszystkie „Finished successfully", **0 błędów** od flipu (ta sama reguła dziedziczona w `_sweep`) | ✅ |

### Interpretacja `applied=0`
Nie jest to sygnał ostrzegawczy. O2-K1 to **wąska reguła**: reseq aplikuje się tylko gdy detour ≤8 ∧ carried ≤20 ∧ gain ≥2 ∧ SLA nie rośnie. Korpus (at-208, 1463 unikatowych worków z 03-07→06-07 incl. peak Pn) już udowodnił **pozytywny wpływ**: Z=20 improved **10,0%**, med ΔO2 9,65, detour med −2,33 / p90 5,84 ≤ cap 8, **regres_o2=0**. W oknie ~21 min off-peak (21:05–21:26 Warsaw, 3 decyzje reseq-eligible) statystycznie oczekiwane `applied≈0`. To, co widzimy na żywo — `considered=282`, `blocked_by_cap=89`, `applied=0`, `detour=0`, `overage=0` — jest wewnętrznie spójne i potwierdza: maszyneria działa, bramka cap gatuje, żaden reseq nie zakwalifikował się (jeszcze), 0 błędów, 0 regresji. **Dowód „warto" leży w korpusie; żywo mamy potwierdzenie „bez szkody".**

## Uwaga cosmetic (nie-blokująca)
`flip_o2k1_20260707.log` ma linie backup+FLIP OK **zdublowane** z identycznym mikrosekundowym znacznikiem (efekt wiring at-joba: `log()` pisze do pliku **i** stdout at-joba dopisuje ten sam stdout do tego samego pliku). **Nie jest to podwójny flip** — potwierdzone: 273 klucze (jeden dopisany), jeden plik backup, flaga=True, a skrypt ma guard NO-OP (linie 31–33). Artefakt logu, do zignorowania.

## ✅ Monitor at-212 — WYKONANY (domknięcie 1h, dopisane 20:22 UTC)
`monitor_o2k1_20260707.log` (bieg **20:10:00 UTC**, `atq` puste = job wykonany i wyczyszczony): **`MONITOR O2-K1 [OK]: flaga=True | shadow_errors_65min=0 | ostatni_shadow_decision_ts=2026-07-07T20:06:11`.** Pełne okno 65 min po flipie = **0 błędów shadow**, silnik produkuje świeże decyzje (20:06, ~4 min przed monitorem). **Bramka „monitor 1h" (procedura §3c runbooka) = ZALICZONA CZYSTO.** (Log ma linię zdublowaną — ten sam artefakt wiring at-joba co przy flipie, nie podwójny bieg.)

## Co jeszcze dojrzeje (domknięcie werdyktu — NIE blokuje)
2. **`applied>0` na żywo** pojawi się z powrotem wolumenu (peak Śr 08.07) — telemetrię reseq w peaku warto sprawdzić w werdykcie 2-dniowym (plan 27-D „O2-K1 werdykt 2 dni: bias/regres z shadow po flipie").
3. **Higiena spójności:** `o2_narrow_rule_replay_verdict.txt` na dysku nosi datę 05-07 „GO wstępny"; finalne GO żyje w `logs/bundle_calib_review_l0.log` (at-208). Przy okazji dopisać 1 linię potwierdzenia at-208 do verdict.txt (spójność) — **nie blokuje**.

## Rollback (gdyby werdykt 2-dniowy się pogorszył)
HOT: `ENABLE_O2_CAPZ_RESEQ=False` (lub usuń klucz) → ≤1 tick, OFF=bajt-parytet. Głęboki: `git revert 3947276` + restart shadow (za ACK). **Wykonanie rollbacku = poza tym uruchomieniem (wszystkie akcje wstrzymane, tylko na ACK Adriana).**

---
**Powiązane:** `PAS0_FLIPMASTER_RUNBOOK.md` §3 (procedura O2-K1) + §1 (tabela gotowości) · `SPRINTY_27_28_PLAN.md` 27-A/27-D · [[ziomek-shadow-is-live-proposals]] · [[shadow-jobs-registry]].
