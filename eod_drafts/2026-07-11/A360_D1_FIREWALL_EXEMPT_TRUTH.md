# A360-D1 FIREWALL-EXEMPT-TRUTH

Status: **IMPLEMENTED + TESTED ON BRANCH — merge HOLD do odczytu at-214
2026-07-13 12:15 UTC**

Branch/worktree/base: `engine/a360-d1-firewall-exempt-truth` /
`/root/a360_d1_wt/dispatch_v2` / `e0fd1e4`.

Commit kodu/testow: `e75c4a8` (`feat(firewall): separate preexisting decision
impact`).

## Problem i aktualny dowod

`core/invariant_firewall.py` rozpoznawal carried picked-up bez nowego detouru
jedynie przez `exception_reason=PRE_EXISTING_PICKED_UP_NO_NEW_DETOUR`, lecz taki
wiersz nadal zwiekszal `violation_count`, pozostawal w globalnym statusie
`VIOLATION` i oskarzal biezaca decyzje. Read-only odczyt rotacyjny ostatnich 24 h
potwierdzil ten stan w realnym ledgerze: 4 fizyczne naruszenia mialy ten marker,
ale status decyzji nadal `VIOLATION`. Dane wrazliwe i identyfikatory nie byly
zapisywane do raportu.

Baseline przed pierwsza edycja, kanoniczny DEFAULT pod wspolnym flockiem:
**5087 passed, 27 skipped, 10 xfailed, 0 failed, 147 warnings w 224.30 s**.

## Mapa kompletnosci przed edycja

| Miejsce | Rola | Writer / consumer | Dotkniete | Powod | Dowod/test |
|---|---|---|---|---|---|
| `core/invariant_firewall.py` modele, klasyfikacja R6/R27/SLA i agregacja | zrodlo prawdy przyrzadu | writer `RuleVerdict` | TAK | root cause: physical breach i odpowiedzialnosc decyzji byly jednym statusem | goldeny PASS/EXEMPT/INTRODUCED/UNKNOWN, provenance, mutation |
| `dispatch_pipeline.PipelineResult.rule_verdict` | obserwacyjny carrier | writer/consumer | N-D | carrier juz istnieje i jest dolaczany po selekcji | identity/parity |
| `dispatch_pipeline._attach_final_rule_verdict` | jedyny kanoniczny hook | writer | N-D semantycznie; TAK tylko fallback schematu | nie dodajemy drugiego hooka; trzy fallbacki musza miec ten sam kontrakt | single-call oraz awarie evaluatora/importu |
| `core.decide.decide` | publiczna fasada | consumer/delegat | N-D | deleguje do publicznego `assess_order` | istniejacy wiring |
| `core.gates.early_bird` | wewnetrzny kontrfaktyk | consumer `_assess_order_impl` | N-D | celowo omija finalne haki; zewnetrzna decyzja dostaje firewall raz | regression/parity |
| `shadow_dispatcher._serialize_candidate` LOCATION A | alternatywy | serializer | N-D | alternatywa nie jest finalnym planem; kopiowanie statusu zwyciezcy fabrykowaloby oracle | A/B: metryki kandydatow bez zmian, finalny verdict raz top-level |
| `shadow_dispatcher._serialize_rule_verdict` + `_serialize_result` LOCATION B | finalny wynik | serializer | TAK tylko fallback schematu; istniejacy transport bez zmiany | `to_dict()` przenosi nowe pola, fallback nie moze klamac starym kontraktem | typed/dict/failure + parity |
| `shadow_dispatcher._tick` -> `_append_decision` | transport rekordu | writer JSONL | N-D | istniejacy pojedynczy call-site | realny tick do tmp JSONL; probe odpiecia musi byc RED |
| `core.jsonl_appender.append_jsonl` | atomowy append | writer I/O | N-D | transport nie filtruje pol; poza allowlista | tmp JSONL |
| realny `shadow_decisions.jsonl` | ledger produkcyjny | writer runtime / read-only dowod | N-D | sobotni blackout, brak zapisu live | tylko zredagowany agregat baseline |
| `tools.ledger_io.iter_shadow_decisions` | kanoniczny rotation-aware reader | consumer | N-D | zachowuje caly nested dict; poza allowlista | reader skierowany na tmp ledger |
| panel/Telegram i pozostale raporty | konsumenci rekordu | consumer | N-D | nie czytaja semantycznie `rule_verdict`; display poza lane | jawne ryzyko future visibility |

Nie znaleziono niezbednego pliku poza allowlista. LOCATION A pozostaje swiadomie
N-D: finalny invariant dotyczy wylacznie wybranego planu LOCATION B.

## Kontrakt zmiany

- `status` opisuje wplyw biezacej decyzji: `EXEMPT_PREEXISTING`,
  `VIOLATION_INTRODUCED`, `PASS` oraz uczciwe `UNKNOWN`/`NOT_APPLICABLE`;
- `physical_status` zachowuje widocznosc realnego przekroczenia finalnego planu;
- wszystkie nowe liczniki maja jawna jednostke `rule_variant_rows` i nazwe
  `*_rule_variant_row_count`: to NIE jest liczba unikalnych zlecen. Jeden order
  moze dac osobny wiersz R6 i SLA, a R27 osobny wiersz dla kazdego wariantu;
- kazdy fizyczny breach zachowuje `rule_id`, wartosc, limit, mode i source oraz
  dostaje status wplywu i etap provenance;
- picked-up carried dostarczany nie pozniej niz pickup nowego zlecenia jest
  `EXEMPT_PREEXISTING`; sama dostawa za pickup nowego bez pre-decision lub
  counterfactual baseline jest `UNKNOWN`, nigdy domyslnie introduced;
- `VIOLATION_INTRODUCED` wymaga dowodu input `<= limit` i final `> limit`;
  w obecnym minimalnym wiring taki dowod z konstrukcji istnieje dla nowego
  orderu (nie istnial przed decyzja), nie dla carried bez kontrfaktyku;
- carried bez wystarczajacego dowodu przyczynowosci jest `UNKNOWN`, nigdy
  automatycznie introduced;
- paczka zwolniona polityka pozostaje osobnym `EXEMPT_POLICY`, nie pre-existing;
- plain-dict `rule_verdict.v1` pozostaje legacy bez reinterpretacji: jego
  `status=VIOLATION` nie jest mapowany na v2. Tylko schema v2 posiada
  `physical_status`, status odpowiedzialnosci i jawna jednostke licznikow;
- enforcement pozostaje `NONE`; verdict, winner, score, feasibility i plan nie
  sa konsumowane przez instrument.

## Testy i wydanie

### Dowody celowane

- focused core/wiring/A360-D1: **36 passed, 0 failed**;
- rozszerzony klaster firewall + SLA-preexisting + oba serializery + append +
  canonical reader: **92 passed, 1 skipped, 0 failed**;
- parity: po hooku i serializerze zachowane sa verdict, tozsamosc best/planu,
  score i sequence; LOCATION A zachowuje metryki alternatywy i nie dostaje
  finalnego verdictu LOCATION B;
- realny hermetyczny `_tick` zapisuje v2 przez prawdziwe `_append_decision` do
  tmp JSONL, a `ledger_io.iter_shadow_decisions` zachowuje schema, status,
  physical_status, provenance i jednostke licznikow;
- mixed ledger v1+v2: plain-dict v1 pozostaje bitowo semantycznie legacy
  (`status=VIOLATION`, bez `physical_status`/`count_unit`), v2 zachowuje wlasny
  slownik statusow.

### Mutation probes

Kazda mutacja byla wykonana przez `apply_patch` i uruchomiona na waskim oracle.
Mutacje przywracano osobnymi `apply_patch`; po odtworzeniu zweryfikowano brak
markerow mutacji, czysty `git diff --check` i ponownie zielony klaster.

1. `EXEMPT_PREEXISTING -> VIOLATION_INTRODUCED`: **RED, 2 failed / 1 passed**.
2. Brak baseline carried: `UNKNOWN -> VIOLATION_INTRODUCED`: **RED,
   1 failed / 1 passed**; zabija false accusation wskazane przez review.
3. Odpiecie `_append_decision(shadow_log_path, record)`: **RED, 1 failed**;
   canonical reader dostal 0 zamiast 1 rekordu.

### Pelna regresja i checkery

Wszystkie pytest uruchomiono tylko przez venv dispatch z
`DISPATCH_UNDER_PYTEST=1`, `ZIOMEK_SCRIPTS_ROOT=/root/a360_d1_wt` i
`PYTHONPATH=/root/a360_d1_wt`. Pelne przebiegi byly osobne i pod
`flock /tmp/ziomek_full_regression.lock`.

| Bramka | Wynik | Porownanie |
|---|---|---|
| baseline DEFAULT przed edycja | 5087 passed, 27 skipped, 10 xfailed, 0 failed, 147 warnings / 224.30 s | zamrozony punkt odniesienia |
| final DEFAULT | **5095 passed, 27 skipped, 10 xfailed, 0 failed, 147 warnings / 217.58 s** | +8 nowych przypadkow, identyczne skip/xfail/fail |
| final HERMETIC_STRICT=1 | **5045 passed, 77 skipped, 10 xfailed, 0 failed, 147 warnings / 202.18 s** | hermetyczna kwarantanna +50 skip vs DEFAULT; 0 HERMETIC-GUARD |

- `py_compile` + import check: PASS, schema `rule_verdict.v2`;
- `tools/canon_static_check.py`: PASS; `--selftest`: 10/10 sond KILLED;
- `tools/flag_lifecycle_check.py --repo-hermetic`: PASS, **505/505**, 0 bledow;
- `git diff --check`: PASS;
- branch-local entropy (jawnie nadpisany ROOT worktree): metryki automatyczne bez
  pogorszenia, `dead-flag=1`, `sentinel żywy=11`, pozostale wartosci audytowe
  bez zmiany. Naglowek toola historycznie mowi „zywy silnik”, lecz pomiar byl
  wykonany na `/root/a360_d1_wt/dispatch_v2`.

Pre-edit STRICT nie byl osobno mierzony, wiec raport nie fabrykuje porownania
STRICT-do-STRICT; rozstrzygajacy baseline porownawczy to wykonany pre-edit
DEFAULT powyzej.

## Operacje live, flagi i rollback

- zero zmian `flags.json`, systemd, runtime state, danych lub logow live;
- zero deployu, restartu i enforcementu; `RuleVerdict.enforcement` pozostaje
  `NONE`, a nowa klasyfikacja nie ma konsumenta decyzyjnego;
- incydent bootstrapu: jednorazowo otwarto `/proc` procesu i przepuszczono
  zawartosc przez allowlistowy filtr pieciu nazw; wynik byl pusty, bez
  wyswietlenia sekretow ani PII. Po korekcie bezpieczenstwa wprowadzono zakaz
  dalszych odczytow environ/cmdline; wszystkie pozniejsze potwierdzenia uzywaly
  tylko niewrazliwego `FLAG_FINGERPRINT` i celowanych properties;
- backup kodu = zamrozona baza `e0fd1e4` + jawny commit lane'a; danych nie
  backupowano, bo zadne dane nie sa modyfikowane;
- rollback po commicie: `git revert e75c4a8`; nie wymaga migracji,
  flagi ani restartu, dopoki lane nie jest wdrozony;
- chronionych/cudzych plikow nie dotknieto; write-set pozostaje dokladnie
  allowlista lane'a.

## Otwarte ryzyka i HOLD

1. Carried breach za pickup nowego orderu bez pre-decision/counterfactual
   baseline pozostaje uczciwie `UNKNOWN`. Dalsza promocja do introduced wymaga
   osobnego, zwalidowanego baseline'u; finalna kolejnosc sama nie wystarcza.
2. Panel i Telegram nie konsumują semantycznie `rule_verdict`; D1 gwarantuje
   truth w kanonicznym JSONL/readerze, nie nowa powierzchnie UI.
3. Rekordy v1 i v2 beda wspolistniec. Consumer musi branchowac po `schema` i
   nigdy mapowac v1 `VIOLATION` na v2 `VIOLATION_INTRODUCED`.
4. **HOLD:** nie merge'owac do mastera, nie deployowac i nie restartowac przed
   odczytem at-214 2026-07-13 12:15 UTC oraz decyzja integratora. Sobota pozostala
   ops-blackout.

Push i commit raportu sa osobnym dowodem lane'a; ich wynik jest w finalnym
handoffie integratora. Kod do rollbacku pozostaje jednoznacznie `e75c4a8`.
