# Z-P1-05 Faza A — Kanoniczna tożsamość kuriera (RAPORT)

**Sprint 4, agent B (builder).** Data: 2026-07-10. Branch: `sprint4/z-p1-05-identity` (baza `c2bde58`).
Pakiet `dispatch_v2/identity/` — **nowy, additywny, READ-ONLY, nieużywany przez runtime**. Rollback = revert commita.

## Co zbudowane

Pakiet `identity/` (8 modułów) czytający 10 istniejących źródeł tożsamości i składający **jeden rekord per CID** (CID = klucz kanoniczny, `str`), z aliasami wersjonowanymi per źródło. Zero edycji istniejących plików, zero runtime/flag/serwisów, zero zapisu do `dispatch_state`/`flags.json`.

| Plik | Rola |
|---|---|
| `identity/normalize.py` | Kontrakt `norm` (BEZ diakrytyki) + DWIE strategie resolvera odtworzone 1:1: `resolve_worker` (×10/×5, `bare_key_strict`) i `resolve_panel_roster` (×10/×10). Semantyki NIE zunifikowano (Faza B). |
| `identity/schema.py` | `CourierRecord` (cid str, aliasy/full_name per źródło, tier, `pin_present`+`pin_last2` — nigdy pełny PIN, active/excluded/is_coordinator) + walidacja. |
| `identity/sources.py` | 10 read-only loaderów, `default_paths()` late-bound, każdy loader z jawną ścieżką (C17). sqlite `courier_api.db` opcjonalny (brak → pomiń + adnotacja). |
| `identity/registry.py` | `build_registry(bundle)` scala źródła → `{cid: CourierRecord}`; `resolve(name, profile)`, `by_cid`, `all_records`; fail-open. |
| `identity/collisions.py` | 6 walidatorów (a-f), poison bare-keys WYLICZANY, sito skrót-vs-pełne. |
| `identity/report.py` | CLI raport + `--parity` (registry vs legacy, read-only). |
| `identity/onboarding.py` | onboard/offboard; default `--dry-run`; `--apply` za env `IDENTITY_ONBOARD_ALLOW=1`; komponuje `courier_admin.add_new_courier` (NIE reimplementuje). |
| `identity/__init__.py` | Publiczne API. |

## Dowody na żywych danych (READ-ONLY)

### 1) Raport kolizji/braków — `python -m dispatch_v2.identity.report` (state=canon, repo=canon)

Rejestr: **65 rekordów (CID), 121 aliasów, 54 CID z >1 aliasem, koordynator ['26'], 10 excluded** — zgodne z mapą A2.

| Kontrola | Wynik | Uwaga |
|---|---|---|
| (a) znormalizowany alias → >1 CID | **0** | migracja „no-dots" trzyma (zgodne z A2) |
| (b) bare-key poison | **8** | `Adrian→21, Edward→267, Gabriel→179, Grzegorz→500, Koordynator→26, Krystian→61, Marek→207, Patryk→75` — dokładnie 8 z A2 |
| (c) rozjazd full-name cross-source | **3** | cid 370 (Kuba/Jakub), cid 376 (Paweł Ściepko vs SC — diakrytyka), **cid 504 (grafik „Artsem Kmets" vs accounting „Artsem Kmieć" — transliteracja, NOWO ujawnione: A2 enumerował 2, sito znalazło 3.)** |
| (d) brak courier_names | **19** | `492, 523-527, 530-531, 533-543` (onboardowani po 06-10; onboarding nie pisze courier_names) |
| (d) brak tieru | **0** | (cid 26 wirtualny wykluczony poprawnie) |
| (e) duplikat/orphan PIN | **0 / 0** | 60 PIN-ów, spójne z A2 |
| (f) rozjazd git↔live daily kurier_full_names | added `Darek os`,`Kacper Sz`; removed `Dawid Kr`; changed 0 | niescommitowana zmiana usera w kanonie (A2 §C) |

### 2) Parytet resolverów — `report.py --parity` (177 nazw: 121 aliasów + 56 grafik)

```
worker      : 177/177 match, 0 mismatch
panel_roster: 177/177 match, 0 mismatch
```

**Parytet 1:1 potwierdzony** na wszystkich żywych nazwach dla OBU profili. Side-effecty legacy wyłączone przed importem (`common.setup_logger`→null, `state.append_match_debug_log`→no-op, `telegram_utils.send_admin_alert`→no-op); `find -newermt '-2 min'` na `dispatch_state` = pusty (zero zapisów).

### 3) Testy hermetyczne (fixtury anonimizowane, zero odczytu dispatch_state)

- `tests/test_identity_registry_zp105.py` — 17 testów: kontrakt norm (diakrytyka niezłożona), worker (exact/score/bare/tie), panel_roster, **case rozbieżności worker↔panel (×5 vs ×10, wynik różny — dowód że oba profile zachowane)**, bare_key_strict, build_registry (prowieniencja/flagi/PIN-redakcja), fail-open, sqlite-opcjonalny, parytet legacy na fixturach.
- `tests/test_identity_collisions_zp105.py` — 9 testów: a-f + sito skrót-vs-pełne (Kuba/Jakub=konflikt, „Jakub Ol"⊆„Jakub Olchowski"=sito, diakrytyka=konflikt).
- `tests/test_identity_onboarding_zp105.py` — 6 testów: dry-run diff 5 plików, blokady kolizji, ostrzeżenie poison, **gate `--apply` (bez env→odmowa; blocking→odmowa; z env+czysto→komponuje spy, PIN zredagowany)** — realny zapis NIGDY nie uruchomiony.

Fixtury reprodukują klasy: goły-klucz-poison, Kuba/Jakub (podwójny alias), diakrytyka Ś vs ascii, brak nazwy, brak tieru, duplikat/orphan PIN, `_meta`, koordynator (flaga + cid 26), para git/live.

## Odłożone do Fazy B

Wpięcie registry w runtime (courier_resolver/common/telegram/worker/daily_accounting/panel); unifikacja dwóch resolverów (×10/×5 vs ×10/×10); podmiana 6 inline kopii `_norm`; backfill/wycofanie `courier_names.json`; konsolidacja zdenormalizowanego `courier_name` w courier_api.db; jakikolwiek `--apply`/zapis. Zero zmian CID i historycznych rozliczeń.

## Znaleziska do decyzji Adriana (nie naprawiane w tym sprincie)

1. **cid 504 (Artsem Kmieć/Kmets)** — trzeci rozjazd full-name (transliteracja) poza znanymi 370/376. Do ujednolicenia kanonu imienia w Fazie B.
2. **19 CID bez `courier_names`** — onboarding od 06-10 nie zasila tego pliku; kandydat do backfillu lub formalnego wycofania (Faza B).

## Rollback

`git revert <commit>` — pakiet nie jest importowany przez runtime, więc rewert jest bezskutkowy dla działającego silnika.
