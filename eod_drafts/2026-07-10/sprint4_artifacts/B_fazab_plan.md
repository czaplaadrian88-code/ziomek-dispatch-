# B_fazab_plan — Z-P1-05 Faza B (delegacja 1:1, ZERO zmiany zachowania) — RAPORT PLANU

Worktree: `/root/sprint4_wt/wt-fazab/dispatch_v2` (branch `sprint4/identity-faza-b` @ `44017e1`). pkgroot_fazab → worktree (potwierdzone). `identity/` NA MASTER (8 modułów). Import `identity.normalize` = tylko submoduły identity, **zero heavy-deps, NIE ciągnie common → brak cyklu** (zweryfikowane w interpreterze). Faza 1 = plan; ZERO edycji do „GO".

## USTALENIA KRYTYCZNE (zmieniają write-set vs brief)

1. **`common.py` NIE MA kopii norm `.,;:` na 44017e1.** Loose-grep całego pliku: jedyne `rstrip` to `rstrip(",.")` w parserze adresów (l.2032/2038/2041/2100/2107) — INNA semantyka (czyszczenie tokenów adresu), INNY zestaw znaków. Panel_packs w common.py (l.1739) nie ma name-norm. Wniosek: „common.py:~1259 inline" z briefu = STALE (snapshot A2 sprzed refaktoru; kopia panel_packs żyje TYLKO w `courier_resolver.py:1259`). **common.py → NIE w write-secie** (flaguję do potwierdzenia).

2. **`resolve_cid` ma side-effect logowania ASERTOWANY testami.** `tests/test_resolve_cid_score_based.py` (16 testów) — Test 15 sprawdza `RESOLVE_CID_AMBIGUOUS_RESOLVED` w logu, Test 16 `RESOLVE_CID_AMBIGUOUS_TIE`. Więc `resolve_cid` NIE może stać się cienkim `return identity.resolve_worker(...)` (identity nie loguje). Delegacja MUSI zachować logikę logowania w workerze.

3. **`match_name_to_cid` ma testy asertujące wyniki** (`tests/test_new_courier_pairing.py:84-123`: Choiński→530, Wrona→527, Kulaszewski→531, itd.) — delegacja `_score` musi być bajt-identyczna.

4. **`ncp.resolve_cid` monkeypatchowany 4×** (`test_new_courier_pairing.py`:169/270/356/375) — `_resolve_cid_trusted` MUSI dalej wołać modułowy `resolve_cid` (patch działa), NIE identity wprost.

## MAPA „KTO MONKEYPATCHUJE / IMPORTUJE PODMIENIANE SYMBOLE" (grep-dowody)

| Symbol | Kto dotyka | Wpływ na delegację |
|---|---|---|
| `ncp.resolve_cid` | `test_new_courier_pairing.py`:169/270/356/375 (setattr → lambda / snw.resolve_cid) | `_resolve_cid_trusted` zostaje wołaniem modułowego `resolve_cid` → patch działa |
| `panel_roster.fetch_active_roster` | `test_new_courier_pairing.py`:160/276/401/451 | NIE dotykam fetch_active_roster → bez wpływu |
| `pr.match_name_to_cid` (wyniki) | `test_new_courier_pairing.py`:84-123 (asercje cid) | `_score` delegacja bajt-identyczna → wyniki bez zmian |
| `worker.resolve_cid` + log | `test_resolve_cid_score_based.py`:47-160 (16, w tym log 15/16) | worker zachowuje `scored`+logowanie; deleguję TYLKO scoring |
| `pa._resolve_cid` | `test_parcel_assign.py`:6/21/27/35 | INNY symbol (parcel_assign) — NIE w zakresie |
| `worker.resolve_cid` import | `new_courier_pairing.py:85`, `identity/report.py:164`, `test_identity_registry_zp105.py:221` | dostają zdelegowany worker; --parity post-zmianie = worker≡identity (dalej 177/177) |
| `_norm`/`_norm_token`/`_score` cross-module import | **BRAK** (grep pusty) | bezpieczne — trzymam symbole jako cienkie wrappery |

## WRITE-SET (edycja ISTNIEJĄCYCH plików — sama delegacja, bez zmiany zachowania)

**Krok 1 — norm (9 sites, 4 pliki) → `identity.normalize.norm`** (każdy site bajt-równoważny: `(x or "").strip().rstrip(".,;:").lower()`):
- `courier_info.py:27` `_norm` → ciało `return norm(s)` (symbol zostaje).
- `panel_roster.py:141` `_norm_token` → `return norm(tok)`.
- `telegram_approver.py:1922` nested `_norm(x)` → `return norm(x)`; `:2770` `norm(k)`; `:2774` `norm(_nick_raw)` (+import top-level).
- `courier_resolver.py:1259/1285/1289/1301` (panel_packs) → `norm(...)` (+import).

**Krok 2 — resolver worker** (`shift_notifications/worker.py:resolve_cid`): wewnętrzne obliczenie score (`if len(atokens)==1: score=1; else ×10/×5`) → `score = score_worker_alias(full_name, alias)` (+import). ZACHOWUJĘ: exact, exact-ci, `first_lc`, sort, remis→None, OBA logi (TIE/RESOLVED). Bajt-identyczne (score_worker_alias = port 1:1 tego bloku).

**Krok 3 — resolver panel_roster** (`panel_roster.py:_score`): ciało → `return score_panel_roster(full_name, roster_name)`. `match_name_to_cid` bez zmian (woła `_score`).

**NOWY test:** `tests/test_norm_delegation_zp105b.py` — property/example: `norm` == stara formuła inline na korpusie (diakrytyka Ś, trailing `.,;:`, None, whitespace, mixed-case) + wrappery `_norm`/`_norm_token`/`_score` delegują.

## ŚWIADOMIE POZA / ODŁOŻONE (flaguję)

- **common.py** — brak kopii na 44017e1 (dowód wyżej). Nic do zmiany.
- **`new_courier_pairing._resolve_cid_trusted`** — BEZ bezpośredniej delegacji: komponuje (już-zdelegowany) modułowy `resolve_cid` + filtr goły-klucz identyczny z identity.bare_key_strict. Bezpośrednie wołanie identity (a) ominęłoby 4 monkeypatche `ncp.resolve_cid`, (b) zgubiłoby logowanie workera → ZMIANA zachowania. Zostaje (korzysta tranzytywnie). Decyzja: PYTAM lidera czy wymusić — inaczej zostawiam.
- **Krok 4 briefu** (przełączenie czytelników PLIKÓW kurier_ids/courier_names w courier_resolver/telegram/sla_tracker na Registry) — ODŁOŻONE (hot path, wyższe ryzyko). NIE dotykam w tej turze.
- Unifikacja profili ×10/×5 vs ×10/×10; konsolidacja courier_api.db — poza zakresem.
- `parcel_assign._resolve_cid` — osobny resolver, poza zakresem.

## COMMITY (każdy osobno; każdy: py_compile + testy konsumenta + pełna suita 0 failed)
- **A:** norm delegacja (4 pliki, 9 sites) + `test_norm_delegation_zp105b.py`.
- **B:** worker.resolve_cid scoring → identity.
- **C:** panel_roster._score → identity.
Stopka `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`, jawne ścieżki, NIE pushuję.

## DOWÓD PARYTETU (metoda)
- **Krok 0 Fazy 2 (przed edycją):** snapshot pristine `worker.resolve_cid` + `panel_roster.match_name_to_cid` na 177 żywych nazwach (121 aliasów + 56 grafik) → `golden_before.json` (read-only).
- **Po każdym commicie:** re-snapshot + diff == golden_before (0 różnic) = zerowa zmiana zachowania na żywych danych.
- **Po całości:** `report.py --parity` live read-only = **177/177 oba profile** (spójność worker≡identity≡registry) + pełna suita **0 failed** + testy konsumenta zielone (test_resolve_cid_score_based 16/16 z logami, test_new_courier_pairing panel_roster).

## RYZYKA
- Import: `from identity.normalize import norm/score_*` — stdlib-only, bez cyklu z common (zweryfikowane).
- telegram nested `_norm(x)` bez guardu `(x or "")` → identity.norm to None-safe NADzbiór; dla osiągalnych wejść bajt-identyczny (None nieosiągalny, dziś by crashował).
- worker: score_worker_alias przelicza `parts/first` per-alias (perf pomijalne, ~121 aliasów; wynik identyczny).
- Baseline masterowy: 4773 passed / 27 skipped / 10 xfailed / 0 failed — oceniam LISTĄ (3 skipy zegarowe test_preshift_window ~17:00 Warsaw), nie sumą.

**Czekam na „GO".** Otwarte pytanie do decyzji: czy wymusić bezpośrednią delegację `_resolve_cid_trusted` (kosztem 4 monkeypatchy — musiałbym przepisać też te testy) czy zostawić kompozycję (rekomendacja: zostawić).
