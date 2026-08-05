# BRIEF (sesja 297, CTO Fable) — SUWAK AUTONOMII jako STAŁY CZYTELNIK shadow-review (read-only)

## KIM JESTEŚ / GDZIE PRACUJESZ
Agent-budowniczy. Worktree: `/root/worktrees/dispatch_v2/active/20260805-suwak-reader-297-cto`
(gałąź `wt/suwak-reader-297-cto-20260805`, base master `06e4d5c39`). Pracujesz WYŁĄCZNIE w tym worktree.
⛔ NIE wolno: pisać do żywego stanu/logów silnika, dotykać flags.json live, systemd/timerów, telegramu; merge = CTO.

## KONTEKST
Owner od 19.07 chce „suwaka autonomii" = 2 uczciwe liczby: (1) ile Ziomek zrobiłby sam (auto-assign wariant D),
(2) ile niezgody z człowiekiem to redystrybucja niedoboru floty vs realna różnica wyboru.
04.08 powstał JEDNORAZOWY composer read-only: `/root/artifacts/suwak-composer-20260804/suwak_2_liczby.py`
(+ `SUWAK_WYNIK.md`, `suwak_wynik.json` — przeczytaj wszystkie 3). Zadanie = zamienić jednorazowy skrypt w STAŁEGO,
cyklicznego czytelnika pod istniejącym `shadow-review.timer` (dobowy, read-only, --no-telegram), żeby suwak
liczył się CODZIENNIE sam i budował szereg czasowy.

## ZADANIE
1. **Zbadaj mechanikę shadow-review**: jak timer odpala przegląd, jak są zarejestrowane istniejące czytelniki
   (moduł shadow_review / lista readerów), gdzie piszą wyniki. ⚠ NIE odpalaj `objm_lexr6_smoke_verdict`
   (ma auto-rollback flagi — nie dotykaj). `ziomek_time_route` = relikt, ignoruj.
2. **Przenieś logikę composera do repo** jako moduł czytelnika (np. `shadow_review/` lub tam, gdzie żyją
   czytelniki — dopasuj do istniejącej konwencji rejestracji), zachowując metodologię i uczciwość liczb
   (licznosci n, okna, segmenty pool<=2 / pool>=3 — patrz SUWAK_WYNIK.md; nie zmieniaj definicji metryk bez
   odnotowania w raporcie).
3. **Wyjście = szereg czasowy**: append-only jsonl (np. `logs/suwak_autonomii.jsonl` w konwencji pozostałych
   artefaktów shadow-review — sprawdź gdzie czytelniki piszą swoje wyniki i trzymaj się tej konwencji) +
   czytelny snapshot md/json dnia. Zapis atomowy, append, zero kasowania.
4. **Odporność:** wyjątek/braki danych w suwaku NIE może wywalić całego biegu shadow-review (fail-soft z
   logiem WARNING); brak korpusu danego dnia = rekord z null + powodem, nie crash.
5. **Read-only wobec źródeł:** czyta `outcomes_clean_shadow.jsonl`, learning_log itd.; NICZEGO w nich nie zmienia.
6. **Testy hermetyczne** (tmp_path, syntetyczny korpus): poprawność 2 liczb na znanym korpusie (policz ręcznie
   oczekiwane), segmentacja pool, fail-soft, append-only, brak zapisu poza własnym wyjściem.
7. **Jeden bieg ręczny read-only** w trybie jednorazowym na żywych danych z wyjściem do worktree (NIE do żywych
   logs/) — porównaj wynik z `suwak_wynik.json` z 04.08 (powinno być spójne co do metodologii; różnice od nowego
   dnia danych wyjaśnij w raporcie).

## REGRESJA
`cd /root/worktrees/dispatch_v2/pkgroot/20260805-suwak-reader-297-cto/dispatch_v2 &&
/root/.openclaw/venvs/dispatch/bin/python -m pytest tests/ -q` — baseline PRZED, delta = tylko twoje testy, 0 failed.

## DELIVERABLES (NA DYSK, PRZED KOŃCEM — obowiązkowo)
1. Commit(y) na gałęzi (jawny pathspec). 2. `RAPORT_AGENTA.md`: jak wpięty czytelnik (dokładny mechanizm
rejestracji), format wyjścia, wynik biegu ręcznego vs 04.08, liczby testów, pełny SHA, co się stanie przy
pierwszym biegu timera po merge (to ważne dla CTO — merge aktywuje czytelnik przy następnym tiku dobowym).
