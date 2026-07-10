# E — Review BRANCH B (sprint4/z-p1-05-identity, commit e39fb8d)

**WERDYKT: APPROVE** — pakiet czysto additywny, wierny legacy 1:1, hermetyczny, zero regresji, read-only potwierdzone empirycznie. Brak znalezisk P0/P1. Dwa drobiazgi (nie blokują).

## Zakres (a) — OK
- `git diff --numstat c2bde58..HEAD`: 22 pliki, KAŻDY `+X / -0` (czysto additywny, suma +2236). Zero modyfikacji istniejących plików.
- Zero plików CHRONIONYCH (identity/ to nowa, wolna przestrzeń; testy `test_identity_*_zp105` unikalne; fixtury w `tests/fixtures/identity/`; raport w `eod_drafts/`).
- Write-set == B_plan.md §1 (8 modułów + 3 testy + 10 fixtur + 1 raport). Dokładnie.

## Runtime-nietykalność (FAZA A) — OK
- Grep: ŻADEN istniejący moduł nie importuje `dispatch_v2.identity` (jedyne wystąpienia poza pakietem = docstringi/argparse prog w samym pakiecie).
- Zero zmian nośników flag (branch ich nie dotyka). Pakiet inertny do Fazy B.
- `py_compile` 8 modułów + 3 testy: OK.

## Wierność normalizacji + obu strategii (b) — OK, 1:1
Porównanie źródeł legacy (przeczytane) z `identity/normalize.py`:
- `norm(s)=(s or "").strip().rstrip(".,;:").lower()` — identyczne z 6 inline kopiami; BEZ składania diakrytyki (test: `norm("Ściepko")!=norm("Sciepko")`). ✓
- `resolve_worker` == `shift_notifications/worker.py:resolve_cid` (l.118-224): exact→ci-exact→score ×10/×5, goły klucz=1, remis=None. Jedyna RÓŻNICA (świadoma, udokumentowana w docstringu + plan §2): port jest PURE — pomija legacy side-effect `state_mod.append_match_debug_log` (log debugowy, NIE wpływa na zwracany cid). Wartość zwracana 1:1.
- `resolve_panel_roster` == `panel_roster.py:_score/match_name_to_cid` (l.141-210): prefiks nazwiska ×10 DWUKIERUNKOWO, remis=ambiguous. `norm`==`_norm_token`. 1:1.
- `bare_key_strict` == `new_courier_pairing._resolve_cid_trusted` (l.252-280): dla nazwy wielowyrazowej odsiewa klucze jednowyrazowe przed scoringiem; wejście jednowyrazowe rozwiązywane normalnie. 1:1.
- **Parytet empiryczny (mój niezależny run): 177/177 match na OBU profilach, 0 mismatch** (== live_parity.txt B).
- Test `test_worker_and_panel_roster_diverge_by_design` = prawdziwy dowód BEHAWIORALNY (C13): konstruuje przypadek gdzie worker (×5 rev) REMISUJE→None, a panel (×10 rev) rozwiązuje→701. Dowodzi że OBA profile zachowane i NIE zunifikowane.

## Onboarding (c) — OK
- Default `--dry-run` (diff 5 plików). ✓
- `--apply` PODWÓJNIE bramkowany: `args.apply AND os.environ["IDENTITY_ONBOARD_ALLOW"]=="1"`. `--apply` bez env → rc 2 (REFUSED); blocking z env → rc 3; tylko czysto+obie bramki → kompozycja. ✓
- KOMPONUJE `courier_admin.add_new_courier` przez lazy import (onboarding.py:187), sygnatura `add_new_courier(int(cid), name)` — NIE reimplementuje zapisu. `derive_alias` też lazy-import (l.40-41). ✓
- Testy dowodzą: spy NIGDY nie wołany bez env / na blocking; z env+czysto → spy(601,"Nowy Ktos"); PIN "1234" NIE w outpucie, `pin_last2`="34". ✓

## Runy (d) — wykonane, read-only potwierdzone
- `report.py` (live, state=canon): records=65, aliasy=121, multi-alias=54, coord=['26'], 10 excluded; kolizje bare-key=8, divergence=3 (**370/376/504**), missing_names=19, missing_tier=0, dup/orphan PIN=0/0; git-live added=2/removed=1/changed=0. **Dokładnie == deklaracja B + live_report.txt.**
- `report.py --parity`: 177/177 oba profile, exit 0.
- **Dowód READ-ONLY (kluczowy, bo serwer PRODUKCYJNY z żywymi serwisami):**
  - Neutralizacja side-effectów szczelna na poziomie kodu: worker `from ...import state as state_mod` (l.39) → parity patchuje TEN moduł `.append_match_debug_log`=no-op PRZED importem worker; dostęp `state_mod.append_match_debug_log` w call-time trafia w no-op.
  - Test bracketowany: świeży parity (105 ms) dodał **0 linii** do `courier_match_debug.jsonl` (585061→585061).
  - `flags.json`: 0 plików nowszych niż marker (nietknięty).
  - 22 pliki dispatch_state + 5 logów „nowsze niż marker" = 100% churn żywych serwisów (marker sprzed moich runów o minuty; dominująca schema `MATCH_*` pochodzi z `schedule_utils`, którego parity NIGDY nie woła; heartbeat świeży 09:15:41).

## py_compile (e) — OK

## Raport (f) — zgodny ze stanem
- Liczby żywe == mój run. Uczciwie ujawnia **cid 504 (Artsem Kmets/Kmieć)** jako TRZECI rozjazd poza znanymi 370/376 z A2 (genuine find, nie błąd). Rollback udokumentowany.

## Bezpieczeństwo — OK
- PIN wyłącznie last2 wszędzie (schema `pin_last2`, collisions, redakcja w onboarding). Fixtury syntetyczne (PIN-y okrągłe 1000/2000/9999, nazwiska zmyślone). Zero tokenów/sekretów.
- Testy hermetyczne: jawne ścieżki fixtur, NIGDY `default_paths()`/live dispatch_state.

## Pułapki protokołu — OK
- **C17**: zero path-literal defaultów w sygnaturach (`default_paths(state_root=None...)`, loadery z jawnym `path`).
- **C12e**: testy self-lokalizują (`Path(__file__).parent/"fixtures"/"identity"`), zero hardcode ścieżek worktree. Jedyny literał `/root/.openclaw/.../dispatch_state` = fallback env w `default_paths()` (l.53), env/arg-override, nietknięty przez testy — poprawne miejsce dla kanonu, NIE naruszenie.
- **C13**: asercje behawioralne (divergence ×5/×10; kolizje na strukturalnych findings z fixtur), nie tekstowe.
- Determinizm: outputy collisions wszystkie sortowane; testy przez by_cid/count → brak flake.

## Testy (moje vs deklaracja)
- Pełna suita w worktree (pkgroot_identity, flags.snapshot): **4742 passed / 24 skipped / 10 xfailed / 0 failed** (120s, exit 0).
- == deklaracja B (4742/24/10/0) == baseline 4710 + 32 nowych. Zero regresji.
- Collection: dokładnie 32 nowe testy identity.

## Znaleziska
- **P0: brak. P1: brak.**
- **P2 / drobiazgi (nie blokują):**
  1. Raport §3: liczby testów per-plik (17/9/6) ≠ faktyczne (16/8/8). SUMA 32 poprawna → deklaracja 4742 trzyma. Czysta nieścisłość dokumentacyjna.
  2. `report.py:build_report` — kolejność `coordinator_cids` i `all_records()` pochodzi z iteracji `set` w `build_registry` → niedeterministyczna między runami (kosmetyka JSON; testy/kolizje niewrażliwe bo sortowane). Opcjonalnie posortować dla stabilnych diffów.
