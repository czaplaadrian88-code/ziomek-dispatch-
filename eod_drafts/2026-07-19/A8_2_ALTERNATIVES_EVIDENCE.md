# A8-2 — kompletność `best + alternatives`

Data: 2026-07-19 UTC
Branch: `fix/audit-a8-alternatives`
Worktree: `/root/wt-audit-a8-pkgroot/dispatch_v2`
Base: `486bac4682e8a5e7e696ba7efc4d5acbd08d62bc`
Model tier: `sol`
Effort: `xhigh` — P1 psujący wejście werdyktu kalibracji ETA i dotykający wspólnego kontraktu ledgera.
Dokładny wariant modelu: nieatestowany/dziedziczony; bieżący interfejs sesji nie udostępnia wiarygodnego pola modelu do potwierdzenia.

## Problem i dowód

`shadow_dispatcher._serialize_result` budował `alternatives` jako
`result.candidates[1:]`. To zakładało `best is candidates[0]`, choć trzy legalne
ścieżki `core/selection.py` łamią to założenie:

- best-effort OBJM może wybrać obiekt ze środka listy bez jej przestawienia;
- `solo_fallback` tworzy nowy obiekt `best`, a w `candidates` może pozostać
  odrzucony wariant tego samego kuriera;
- `no_solo_candidates` zwraca `best=None`, więc pierwszego elementu nie wolno
  odcinać.

Skutek: ledger mógł zgubić niewybranego kuriera, zdublować wybranego i skierować
`eta_calibration_logger` do fallbacku `matched_courier=False` mimo obecności
realnego kuriera w ocenionej puli.

Read-only replay kanonicznym `tools.ledger_io.iter_shadow_decisions` za ostatnie
3 dni potwierdził materialność bez wypisywania PII: `563` rekordy, `1`
`solo_fallback`, i dokładnie ten rekord miał `best` powtórzony w
`alternatives`.

## Mapa kompletności ETAP 3

| Miejsce | Rola | Writer/consumer | Dotknięte | Powód | Test/dowód |
|---|---|---|---|---|---|
| `core/selection.py` zwykłe ścieżki `top` | kształt wyniku | writer | N-D | `best==candidates[0]`; zachowanie pozostaje bez zmian | byte-parity winner-first |
| `core/selection.py` best-effort OBJM | kształt rozbieżny | writer | N-D | fix u wspólnego źródła ledgera, bez zmiany selekcji | winner ze środka + mutation probe |
| `core/selection.py` `solo_fallback` | kształt rozbieżny | writer | N-D | należy usunąć bliźniaka po `courier_id`, nie zmieniać decyzji | test solo-twin |
| `core/selection.py` `no_solo_candidates` | kształt rozbieżny | writer | N-D | `best=None` oznacza pełną pulę alternatyw | test no-solo |
| `dispatch_pipeline.PipelineResult` | schema granicy | writer | N-D | schema bez zmian | istniejące testy PipelineResult |
| `shadow_dispatcher._serialize_result.best` | LOCATION B | writer | N-D | serializacja best bez zmian | parytet A/B + byte-parity |
| `identity.schema.canon_cid` | wspólny kanon tożsamości | consumer | TAK | deduplikacja używa istniejącej normalizacji, m.in. `200 == 200.0`; brak lokalnej kopii resolvera | test reprezentacji numerycznych |
| `shadow_dispatcher._alternative_candidates` | LOCATION A / pool | writer | TAK | iteracja całej puli, wykluczenie best i deduplikacja niewybranych CID z zachowaniem kolejności | 8 nowych testów |
| `shadow_dispatcher._serialize_candidate` | serializer alternatywy | writer | N-D | pola i schema bez zmian | serializer cluster |
| `core/jsonl_appender` / `shadow_decisions.jsonl` | persystencja | writer | N-D | API zapisu bez zmian; rekord zmienia się tylko w wadliwych kształtach | full regression + replay |
| `eta_calibration_logger` | join realnego kuriera | consumer | N-D | konsumuje tę samą schemę, dostaje kompletną pulę | E2E `matched=True` |
| `telegram_approver` | propozycje/przyciski | consumer | N-D | schema bez zmian; pula staje się unikalna i kompletna | proposal/panel cluster |
| narzędzia replay/ML/statystyki | konsumenci generyczni | consumer | N-D | wszystkie czytają `best + alternatives`; brak nowego pola | pełna regresja |
| `tools/ledger_io` | rotation-aware oracle | consumer | N-D | użyty wyłącznie read-only do dowodu materialności | agregat 563/1/1 |
| `shadow-jobs-registry` / at#220 | przyszły werdykt ETA | consumer operacyjny | N-D | aktualizacja wspólnej pamięci i decyzja o jobie należą do MAIN | HOLD do deployu i świeżego okna |

Każde miejsce zwrócone przez `ziomek-cto scope` jest TAK albo N-D. Klasa
`nowa-metryka` jest N-D: nie dodano klucza ani producenta metryki. Klasa
`artefakt-werdykt` jest dotknięta pośrednio: test joinu ETA i rotation-aware
replay stanowią oracle; sam job nie został zmieniony.

## Zmiana zachowania

- `best`, verdict, reason, score, feasibility, plan i wszystkie reguły HARD/SOFT
  pozostają bajtowo niezmienione.
- Dla zwykłego `best==candidates[0]` cały rekord jest bajtowo identyczny.
- Dla rozbieżnych kształtów `alternatives` zawiera każdego niewybranego kuriera
  dokładnie raz, w oryginalnej kolejności, i nie zawiera wariantu o kanonicznym
  `courier_id == best`.
- Dla `best=None` zachowana jest cała lista kandydatów.
- Dwa różne wadliwe obiekty bez CID nie są po cichu scalane; bez tożsamości
  można bezpiecznie rozpoznać tylko ponowne wystąpienie tego samego obiektu.

## Dowody testowe

N-D: `core/candidates.py` — zmiana nie dotyka producenta Candidate ani metrics; naprawiany jest wyłącznie wybór elementów już gotowej listy na granicy serializacji.
N-D: `scoring.py` — score, kolejność rankingowa i agregacja wyniku pozostają bez zmian; filtr używa wyłącznie kanonicznego `courier_id` po zakończonej selekcji.
regresja: baseline 5133 passed / post 5141 passed; failed: 0 nowych względem baseline, absolutnie identyczne 9 znanych failujących nodeidów harnessu po obu stronach.
e2e: `_serialize_result` → rekord `best + alternatives` → `eta_calibration_logger.pick_prediction`; realny kurier z dawnego `candidates[0]` kończy z `matched=True`, a stara mutacja daje `matched=False`.
pozytywny-wplyw: mutation replay `5 failed, 3 passed` na starym `[1:]` i `8 passed` po fixie; live rotation-aware oracle potwierdza 1/1 wadliwy `solo_fallback`, a zwykły winner-first pozostaje bajt-identyczny.
rollback: `git revert <commit-A8-2>`, pełna suita i jeden kontrolowany restart `dispatch-shadow`; brak migracji danych.

- `py_compile shadow_dispatcher.py tests/test_a8_alternatives_integrity_2026_07_19.py` — PASS.
- Nowe testy integralności: `8 passed`.
- Mutation probe (`result.candidates` → `result.candidates[1:]`): oczekiwane
  `5 failed, 3 passed`; po odtworzeniu fixa `8 passed`.
- Rozszerzony klaster serializery + ETA + proposal/panel: `178 passed, 1 skipped,
  1 xfailed`.
- Pełny baseline `HERMETIC_STRICT=1`: `5133 passed, 74 skipped, 7 xfailed,
  9 failed` w 305.05 s.
- Pełna regresja po zmianie: `5141 passed, 74 skipped, 7 xfailed, 9 failed`
  w 293.98 s.
- Zbiory dziewięciu failujących nodeidów są identyczne. To znany szum
  pkgroot-harnessu: `conftest_flag_strip_guard` ×3, `flag_doc_coverage` ×3 i
  trzy istniejące `script_run`. Delta: `+8 passed`, `0` nowych failów.
- `git diff --check` — PASS.

Pierwszy świeży blind review poprawnie wykrył trzy niezamknięte brzegi:
duplikat niewybranego CID, reprezentację `200` vs `200.0` i dwa różne obiekty
bez CID. Wszystkie trzy mają teraz osobne testy i wspólną implementację opartą
na `identity.schema.canon_cid`.

Kandydat po remediacji został przypięty SHA-256
`ed9f10c71dc4f009153ed3433827fc45ec9193d8f3ae6139c1e62ef7b26f3225`;
manifest bundla potwierdził `pin_verified=true`. Dwóch nowych reviewerów
(`model_tier=sol`, `effort=high`, dokładny wariant nieatestowany przez interfejs)
oceniło wyłącznie ślepy bundle pod różnymi kątami. Oba niezależne werdykty:
`CLEAN`, `findings=[]`; oba przeszły `ziomek-blind-review check`.

## Wydanie i rollback

Nie wykonano żadnego merge, push, deploy, restartu, zmiany flagi ani danych
runtime. Produkcja nadal emituje stary kształt do czasu osobnej bramki live.

Wydanie wymaga przez MAIN:

1. synchronizacji z aktualnym `master` i ponownej pełnej regresji na kanonie;
2. jawnego biznesowego ACK na deploy/restart `dispatch-shadow`;
3. jednego kontrolowanego restartu, health/PID/NRestarts/fingerprint oraz smoke
   świeżego `solo_fallback`/OBJM, jeśli taki przypadek wystąpi;
4. oznaczenia chwili odcięcia — historyczne wadliwe rekordy pozostają
   historyczne i nie powinny być przepisywane;
5. ponownej oceny at#220 wyłącznie na świeżym oknie po wdrożeniu albo jawnego
   opisania mieszanej jakości danych.

Rollback: `git revert <commit-A8-2>`, ponowna pełna suita i jeden kontrolowany
restart `dispatch-shadow`. Brak migracji i brak danych do cofnięcia. Awaryjny
rollback przed deployem to po prostu niewłączanie commitu.

## Pozostały P1

C3-01 nie jest częścią tego commitu. Wymaga osobnego sprintu obejmującego
`reconciliation/auto_resync.py` oraz wszystkie bliźniacze ścieżki
`emit/emit_audit → update_from_event` w `panel_watcher.py`. Dedup zapisu eventu
nie może blokować ponowienia aplikacji do `orders_state`; statystyka sukcesu nie
może rosnąć po nieudanym apply. To szersza zmiana gorącego writera i nie wolno
jej doklejać do minimalnego fixa serializera.
