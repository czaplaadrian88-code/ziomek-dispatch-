# Resweep G5/G6 — kandydat shadow do review

Data: 2026-07-22  
Gate: `resweep.g5-g6-shadow`  
Branch: `resweep-g5g6`  
Base: `06925c4c3e6408ccc44c075aa528a569d4fc564b`  
Implementacja: `a076f74e`

## Wynik

Kandydat domyka przyrząd G5 i kontrfaktyczny guard G6 bez zmiany aktywnego
zachowania. Nie zmieniono `flags.json`, nie włączono `PENDING_RESWEEP_LIVE`,
nie wykonano deployu, restartu ani operacji na żywym stanie/usłudze.

G5 używa kandydata pierwotnego z tego samego `PipelineResult`, z którego pochodzi
globalna alokacja. Nowy rekord JSONL zawiera `proposed_km`,
`new_km_to_pickup` i `delta_km = new - proposed`. Gdy którejś odległości nie da
się ustalić, `delta_km=null`, a reviewer liczy to jako `missing_rows`.

G6 utrzymuje per `order_id` tylko pseudonimowe CID, timestamp i licznik zmian w
`pending_resweep_pingpong_state.json`. Shadow state jest zapisywany pod `fcntl`
przez temp→fsync→rename→fsync katalogu. Kontr-podmiana na poprzedniego kuriera
jest oznaczana `would_pingpong_block=true`, jeśli nie spełnia jednocześnie 2×
zwykłego marginu i 10 min cooldownu. Oba parametry mają konfigurowalne klucze,
ale brak kluczy w `flags.json` zachowuje powyższe defaulty. Gdy bieżący kurier
wypadł z feasible, HARD ma pierwszeństwo i hystereza nie blokuje ruchu.

Po przyszłym flipie LIVE guard nie ufa symulacji shadow: rekonstruuje ostatnią
faktycznie wykonaną podmianę wyłącznie z `pending[].resweep_live`. Błąd oceny
historii w LIVE jest fail-closed. Ten kod nie jest zgodą na flip.

## PII i konsumenci

Nowe rekordy resweep JSONL nie zapisują restauracji, adresu dostawy ani nazw
kurierów. Pozostają pseudonimowe `order_id`/CID oraz metryki. Jedyny konsument
starych pól opisowych, `pending_global_resweep_review.py`, używa teraz CID i
zachowuje fallback dla historycznych rekordów. Health scoreboard czyta tylko
niezmienione pola techniczne.

## Mapa kompletności

| Miejsce | Rola | Dotknięte | Dowód / N-D |
|---|---|---:|---|
| `tools/pending_global_resweep.py` | writer G5, evaluator/state G6, przyszły guard LIVE | TAK | testy km, A→B→A/A→B→C/cooldown/HARD, OFF parity |
| `tools/pending_global_resweep_review.py` | konsument G5/G6 po 48 h | TAK | agregat `g5_g6`, CID fallback |
| `tools/flag_lifecycle_registry.json` | lifecycle dwóch parametrów G6 | TAK | checker 522/522, 0 błędów |
| `systemd/dispatch-pending-resweep-shadow.service` | opis dozwolonych zapisów procesu | TAK | JSONL + atomowy stan G6, zero pending/orders |
| `claim_ledger.py` | bliźniak alokacji/claimów | N-D | guard tylko konsumuje istniejące `cand_scores`; claimy bez zmian |
| `shadow_dispatcher.py` | gorąca ścieżka/claim-ledger | N-D | nie jest writerem ani consumerem historii G6 |
| panel/Telegram/global_alloc serializer | pozostali konsumenci | N-D | schema decyzji i akcje komunikacyjne bez zmian |

## Testy i delta

- Celowany baseline: `22 passed`.
- Celowany po zmianie: `22 passed`; ten sam zbiór nodeidów, rozszerzone kontrakty.
- Szeroki klaster wszystkich testów importujących resweep: `109 passed,
  1 deselected`. Deselect to istniejący
  `test_tick_claim_ledger_on_off`; na bazowym SHA pada identycznie, bo mock nie
  zwraca `latency_ms`, a ścieżka błędu próbuje odczytać hostowy `config.json`.
- Pełny baseline klonu: kolekcja zatrzymana przez 11 błędów środowiskowych/import
  i 4 skipy. Pełny przebieg po zmianie: dokładnie te same 11 błędów i 4 skipy.
  Kanoniczny venv dispatch jest niedostępny w sandboxie tej sesji; pełna zielona
  suita pozostaje obowiązkową bramką review.
- `py_compile` do `/tmp` + import obu zmienionych modułów: PASS.
- Lifecycle checker: 522/522 curated, 0 errors.
- Mechaniczny `ziomek-cto dod`: PASS z jawnymi N-D i ograniczeniem suity.
- Entropy dashboard: exit 0, sentinel-as-data 0; pozostałe wartości to zastany
  audit-baseline bez żywych plików.

Seeder lifecycle uruchomiono z obowiązkowym `--merge`, lecz pusty harnessowy
`flags.json` próbował skurczyć rejestr 520→307. Ten mechaniczny wynik odrzucono;
przywrócono bazowy plik i dodano wyłącznie dwa jawne, sprawdzone wpisy G6.

## Odczyt po 48 h

Read-only, bez Telegrama:

```bash
/root/.openclaw/venvs/dispatch/bin/python \
  -m dispatch_v2.tools.pending_global_resweep_review \
  --jsonl /root/.openclaw/workspace/dispatch_state/pending_global_resweep.jsonl \
  --no-telegram
```

W JSON odczytać `g5_g6`:

- G5: `missing_rows` powinno być 0; `positive_delta_rows` pokazuje podmiany,
  w których nowy kurier miał dalej do pickupu; `delta_km_median/max` pokazują
  skalę. Bramka „nie dalej” wymaga `positive_delta_rows=0` na adekwatnym korpusie.
- G6: `state_error_rows` powinno być 0; `return_attempt_rows` jest mianownikiem,
  `would_block_rows` licznikiem kontr-podmian zatrzymanych przez proponowany guard.
  Trzeba zestawić to z historycznym 17% oscillation i sprawdzić false-positive,
  zwłaszcza `pingpong_hard_escape_current_infeasible`.

## Rekomendacja do CTO

ACCEPT do niezależnego review i świeżego 48 h shadow, ale nadal NO-GO dla LIVE.
Reviewer powinien: (1) uruchomić kanoniczną pełną suitę i potwierdzić brak driftu
manifestu; (2) sprawdzić atomowość/state race; (3) niezależnie odtworzyć golden
A→B→A, A→B→C, cooldown i infeasible escape; (4) po 48 h ocenić G5/G6 wraz z
liczebnością korpusu. Dopiero potem osobny owner ACK może objąć flip/deploy/restart.

Rollback kodu: `git revert` commitów kandydata. Rollback runtime/danych nie jest
potrzebny na tym etapie, bo nie wykonano żadnej operacji LIVE.
