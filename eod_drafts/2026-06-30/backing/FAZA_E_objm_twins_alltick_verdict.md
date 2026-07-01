# FAZA E — adwersaryjna weryfikacja ROOT `objm-shadow-canary-twins-alltick`

LENS B (refuter: udowodnij inertność / latencję-za-flagą-OFF / zero-wpływu-live / fix net-szkodliwy).
TRYB READ-ONLY. Werdykt: **CONFIRMED** — is_source=TRUE, is_open=TRUE (refutacja NIE wyszła na rdzeniu; udała się tylko częściowo na severity + sub-claim #3).

## Co sprawdzone DRUGĄ metodą

1. **Świeży grep źródła g2c (numery zdryfowane):**
   - `peak_verdict.py:68-72` → `ro=len(reorder_oids & shadow_oids); g2c=100*ro/n_orders` — BAJT-identyczne z monitorowym `reorder_orders_alltick` (`canary_monitor.py:287-290`), który monitor SAM etykietuje „diagnostyka, ZAWYŻONE ×~3,5" (`:339`).
   - Headline przez `_g2c_note(g2c)` (`:37-46`, woła `:81`). Monitor-gate G2c używa `reorder_pct` PER-DECYZJA (`:278-285,337-341`). → asymetria bliźniaków u ŹRÓDŁA, nie na renderze.

2. **Git provenance fixu (oracle twinów):** `git show --stat 397a665` ruszył TYLKO `tools/objm_lexr6_canary_monitor.py`(+44) + test. `peak_verdict.py` NIE tknięty (mtime 26.06 12:02). Fix poszedł do monitora, nie do bliźniaka.

3. **at -c 200:** PENDING `Fri Jul 3 18:10` → `objm_lexr6_peak_verdict.py >> ...checkpoint_2026-07-03.log` **bez `--dry-run`** → zapisze durable txt + Telegram z zawyżonym headline. Instrument decyzji Fazy-4 (ON-na-stałe vs rollback ŻYWEJ flagi `ENABLE_OBJM_LEXR6_SELECT=True`). → is_open potwierdzone, re-manifestacja 03.07.

4. **ORACLE — durable txt SAM SOBIE PRZECZY (3 przebiegi):**
   - 29.06: headline `≈ POŚREDNIO: peak 25.2%` (all-tick) vs gate w TYM SAMYM pliku `per-decyzja 3.7% (6/163) | all-tick 41/163=25.2% (ZAWYŻONE ×3,5)`.
   - 26.06 headline `NADAL WYSOKO 54.7%`; 28.06 `NADAL WYSOKO 62.1%`. Per-decyzja realne ~3,7-5,6% → **×7-11 zawyżka POTWIERDZONA empirycznie**. Headline = liczba, którą własny gate nazywa „inflated diagnostic".

5. **Efektywny stan flag (common.decision_flag + flags.json):** `ENABLE_POST_SHIFT_OVERRUN_PENALTY=False`, `ENABLE_OBJM_LEXR6_SELECT=True`, `ENABLE_OBJM_LEXR6_SELECT_SHADOW=False`.

6. **Call-site cienia (`dispatch_pipeline.py:6249-6250`):** `_objm_lexr6_shadow` wołany TYLKO gdy `ENABLE_OBJM_LEXR6_SELECT_SHADOW` → =False → **cień NIE jest w ogóle wykonywany na żywo**. Żywy selektor `_objm_lexr6_d2_pick` (`:5995`) używa KANONU `_OL.lex_qual` (flag-aware). Sub-claim #3 (3-krotka vs 4-krotka) PODWÓJNIE inertny: (a) cień nie biega, (b) POST_SHIFT=False → kanon i tak zwraca 3-krotkę bajt-identyczną.

## Werdykt rdzenia
Nie do refutacji: peak_verdict headline kłamie all-tick, fix 397a665 (git) ominął bliźniaka, at-200 odpali go znów 03.07, 3 durable txt to oracle ×7-11. is_source=TRUE (kod `:37-46,68-72`), is_open=TRUE (at-200 pending, plik nietknięty). Fix (czytać `reorder_pct` per-decyzja jak monitor) jest trywialny i NIE net-szkodliwy → ta ścieżka refutacji też pada.

## DISSENT (uczciwa reszta refutacji — przeciw severity/„szkodliwe")
- **Zero wpływu na żywy dispatch.** 3 instancje to READ-ONLY przyrządy (R3). Selektor żywy = kanon flag-aware. Monitor self-opis „ZERO wpływu na decyzje/panel".
- **Sub-claim #3 NIE jest aktywnym kłamstwem — to uzbrojony latentny most.** Wymaga PODWÓJNEGO flipa (SELECT_SHADOW ON + POST_SHIFT ON). Dziś 0 rozjazdu, funkcja nie wołana; docstring objm_lexr6.py dokumentuje freeze jako intencjonalny pod at#152. Wrzucenie go do „otwartego kłamiącego przyrządu" zawyża stan bieżący.
- **Prawda korygująca jedzie w tym samym artefakcie:** `build_report` full drukuje każdy gate z „per-decyzja 3.7%" — at-200 Telegram/txt ma realną liczbę linię niżej. Defekt = mylący NAGŁÓWEK, nie ukryte kłamstwo.
- **G2b stale-baseline:** STOP jest C7-uznaną POPRAWNĄ bramką (SEED_XREF), baseline (captured_at=None, n=138, 26.06) = znana single-day oś, nie nowy defekt.
- Wniosek: P1 wysokie jak na narzędzie-werdykt read-only; ale is_source/is_open twardo trzymają (oracle+git+at-job), więc NIE schodzę do PLAUSIBLE.
