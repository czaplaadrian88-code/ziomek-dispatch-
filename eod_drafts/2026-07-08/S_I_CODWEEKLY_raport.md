# SPRINT I — cod-weekly: korzeń nawracającego FAILED w poniedziałki (split-tydzień)

**Sesja:** tmux 42. **Worktree:** `wt-codweekly`, branch `fix/codweekly-split-week`, baseline `8760ee6`.
**Commit:** `7d0d197`. **Status: ZAMKNIĘTE, czeka na ACK do merge (sekwencyjny).**

---

## 1. Diagnoza (empiryczna, ETAP 0)

Log produkcyjny `logs/cod_weekly.log` — run 06.07 06:00 (GROUND-TRUTH):
```
Target week: 2026-06-29 → 2026-07-05 (29.06-05.07.2026)
find_target: payday=08-07-2026, segments=2, candidates=['DD']
TARGET COLUMN FAIL (struktura niejednoznaczna): Oczekiwano 2 kandydatów dla rozbitego tygodnia, znaleziono 1
→ exit 1 (OnFailure)
```

**Korzeń:** tydzień krosujący miesiąc = 2 segmenty. W arkuszu był blok TYLKO jednego
segmentu (kol DD), drugiego brakowało. `find_target_cod_columns` dla rozbitego
tygodnia leciało `len(candidates)!=2` → **`AmbiguousTargetError`**. `cmd_write`
traktował Ambiguous jako „struktura niejednoznaczna" → gałąź, która **NIGDY nie
auto-tworzy** (świadoma decyzja: Ambiguous ≠ auto-create) → exit 1, domykane ręcznie.

To był **błąd klasyfikacji**: partial-split (1 z 2 bloków) NIE jest niejednoznacznością
struktury — to **brakujący blok konkretnego segmentu**, jednoznacznie rozwiązywalny.
Kluczowe: **auto-create JEST już LIVE** (`COD_WEEKLY_AUTOCREATE_BLOCK=1`, drop-in, ACK
Adriana 05.07) — full-missing-block już się samo-naprawia; partial-split omijał go
przez misklasyfikację jako Ambiguous.

Inne poniedziałkowe FAILE w logu (`empty_check_fail`, single-segment `candidates=[]`)
to ODRĘBNE przypadki (kolumna już wypełniona / pełny brak bloku — ten drugi już
obsłużony actionable+auto-create). Poza zakresem Sprintu I (split-tydzień).

## 2. Fix (u źródła, kompletny — 10 warstw cod-weekly)

**Zasada:** partial-split JEDNOZNACZNY → auto (utwórz TYLKO brakujący segment,
istniejący nietknięty → zero duplikatów); genuine niejednoznaczność → graceful
actionable, nie crash.

| Warstwa | Plik | Zmiana |
|---|---|---|
| Wyjątek | `sheet_writer.py` | `PartialSplitBlockError(NoTargetColumnError)` z `found` + `missing_segments` |
| Detekcja primary (payday) | `sheet_writer.find_target_cod_columns` | split: `>segments` kandydatów → Ambiguous (genuine); partial (0<found<total) → PartialSplitBlockError; duplikat miesiąca / malformed → Ambiguous/ValueError (bez zmian) |
| Detekcja twin (zakres, E5) | `sheet_writer.find_target_column_auto` | zbiera found+missing zamiast raise na 1. braku; partial → PartialSplitBlockError; pełny brak → NoTargetColumnError |
| Auto-create | `sheet_writer.build_week_block_plan` / `ensure_week_block` | `segments_override` → tworzy TYLKO brakujące bloki |
| Routing | `run_weekly.cmd_write` | handler `PartialSplitBlockError`: ON→auto-create missing-only→retry→zapis obu→exit 0; OFF→actionable+exit 1 |
| Resilient | `run_weekly.find_target_cod_columns_resilient` | partial z primary → próbuj auto-detect (zakres); gdy on też partial → propaguj (obsługa retry po auto-create, istniejący blok bez payday) |
| Preflight | `run_weekly.cmd_preflight` | partial-aware soft-fail (nazywa tylko brakujący segment) |
| Alert | `run_weekly._build_target_fail_alert` | `missing_segments` → instrukcja o TYLKO brakującym bloku („nie duplikuj istniejących") |

**Bliźniaki RAZEM:** obie ścieżki detekcji (payday-primary + range-autodetect) oraz obie
powierzchnie actionable (cmd_write + preflight) — pokryte w jednym przejściu.

## 3. Zachowane inwarianty (ETAP 2)
- **Genuine ambiguity** (duplikat bloku, malformed range) → nadal `AmbiguousTargetError`/`ValueError` → exit 1, NIGDY auto-create (test PS5).
- **FALA1 intent** (OnFailure/staleness na braku bloku): fallback auto-create OFF → exit 1 zachowany.
- **Idempotencja:** `write_cod_column_skip_filled` (skip-already-filled) nietknięty; auto-create tworzy tylko nagłówki, kolumna COD pusta.
- **Zero utraty danych:** brak zapisu gdy brak bloku (OFF); dopisywalne później.

## 4. Dowody (ETAP 4 + 5)
- **8 nowych testów** (`tests/test_cod_weekly_missing_block.py`, dispatch-venv, REALNY `find_target`+`cmd_write` przez wstrzyknięty gspread — testuje kod worktree przez self-location):
  - PS1 detekcja partial ≠ Ambiguous · PS2 auto-create **missing-only** + zapis obu (exit 0) · PS3 OFF actionable no-write (exit 1) · PS4 dry-run · PS5 genuine ambiguity nadal exit 1 · **PS6 mutation-check ON≠OFF** · PS7 twin auto-detect (payday pusty) + e2e.
- **Replay GROUND-TRUTH 06.07** (PS2/PS6): dokładny grid z loga (29-30.06 obecny, 01-05.07 brak, payday 08-07-2026) → z auto-create LIVE **exit 0** (samo-naprawa) zamiast exit 1. To pozytywny wpływ: metryka „poniedziałkowy split-week exit code" 1→0.
- **Pełna regresja** (kanoniczna ścieżka `dispatch venv, pytest tests/`): **4498 passed (+8), 27 skipped** vs baseline **4490 passed**. Jedyny fail = **ten sam pre-existing** `test_grafik_fetch_schedule::...[fetch]` (Sprint H, live-parity, POZA ZAKRESEM — obecny w baseline). **Zero nowych regresji.**

## 5. Deploy / rollback
- **NIE tknięto:** silnik decyzyjny, `fetch_schedule` (Sprint H), współrzędne (Sprint F), `flags.json`, żaden `--write` do żywego arkusza (tylko unit-testy z MagicMock ws), żaden restart usług.
- **Auto-create już LIVE** (drop-in `COD_WEEKLY_AUTOCREATE_BLOCK=1`) → po merge partial-split samo-naprawia się od najbliższego split-tygodnia. Bez nowej flagi/ACK do włączenia.
- **Merge:** sekwencyjny **po ACK** (branch `fix/codweekly-split-week` @ `7d0d197`; kod prod biega z KANONU, więc do merge nic się nie zmienia w produkcji).
- **Rollback:** `git revert 7d0d197` (lub `.bak-pre-partial-split-2026-07-08` w `cod_weekly/`). Partial-split wróci do exit 1 (stan sprzed).

## 6. Otwarte pytanie do Adriana (opcjonalne, nie blokuje)
Cel „exit-kod odróżniający błąd od czekam na blok" zrealizowano przez **samo-naprawę
(exit 0)** dla resolvable partial-split (auto-create LIVE) vs **exit 1** dla genuine
błędu. Świadomie NIE wprowadziłem osobnego kodu „czekam" (np. exit 3), bo:
(a) sprzeczałby się z FALA1 (OnFailure/staleness MAJĄ łapać brak bloku — świadoma
decyzja 02.07); (b) w praktyce auto-create ON zamienia „czekam" na exit 0.
**Jeśli chcesz mimo to osobny kod „czekam na blok"** (np. z `SuccessExitStatus=3`
tłumiącym OnFailure dla brakującego bloku) — to zmiana kontraktu systemd + odwrócenie
FALA1 → wymaga Twojego ACK. Powiedz i dołożę (drop-in = propozycja, instalacja = ACK).
