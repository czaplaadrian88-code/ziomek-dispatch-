---
name: ziomek-cto
description: "Prowadzi sesję CTO przez pełny cykl pracy nad Ziomkiem (dispatch_v2) — rozpoznanie stanu (brief), mapa kompletności planowanej zmiany (scope), mechaniczna bramka Definition-of-Done na diffie (dod), szablon handoffu do memory (handoff). Użyj na STARCIE każdej sesji zmieniającej silnik/feasibility/scoring/selekcję/kanon/flagi/metryki, przed commitem zmiany Ziomka, przy pytaniu „czego nie zgubiłem / które bliźniaki\", oraz przy zamykaniu sesji. Także gdy pada hasło: protokół #0, ETAP 3, mapa kompletności, DoD, bliźniaki, handoff."
---

# ziomek-cto

Cykl pracy CTO nad Ziomkiem: **rozpoznanie → decyzja co robić → zmiana protokołem #0
→ dowody → handoff**. Driver mechanizuje dwa kroki, które dotąd istniały tylko jako
prozę w protokole i wracały jako bugi, gdy sesja je pominęła:

- **`scope`** — ETAP 3 protokołu: *mapa kompletności* z kuratorowanego rejestru
  bliźniaków (`references/twins-registry.json`, seed: `ZIOMEK_ARCHITECTURE.md §4` +
  Załącznik A protokołu). Bezpiecznik na wzorzec #2 „fix w 1 z N bliźniaczych
  ścieżek", który wracał ≥4×.
- **`dod`** — twarde checki DoD egzekwowalne exit-kodem (flaga ON≠OFF, metryka
  serializowana, parytet bliźniaków, dowody regresji/replay/rollbacku).

Driver: `.claude/skills/ziomek-cto/driver.py` — wołaj go ŚCIEŻKĄ (sam lokalizuje
repo i swoje pliki, działa z dowolnego cwd); ścieżki w tym dokumencie są względne
wobec `dispatch_v2/`.

## Kompozycja (nie dubluj — wywołuj)

| potrzeba | skill |
|---|---|
| stan usług / werdykt strażnika / przecieki / flagi / suita | **run-dispatch-v2** (`brief` wywołuje jego `health` — ziomek-cto NIE ma własnej kopii) |
| niezależna ślepa recenzja kandydata przed promocją | **ziomek-blind-review** (`dod` = mechaniczna brama; razem tworzą parę: twarde checki + świeży recenzent) |
| mapa zmian / DoD / brief CTO / handoff | **ziomek-cto** (ten skill) |

## Cykl sesji CTO

```bash
cd /root/.openclaw/workspace/scripts/dispatch_v2
D=.claude/skills/ziomek-cto
python3 $D/driver.py brief          # 1. START SESJI: zdrowie (delegacja) + HOLD/P0 + shadow-joby + recon multi-sesji
python3 $D/driver.py scope "równe traktowanie no-GPS"   # 2. PRZED ZMIANĄ: mapa kompletności (ETAP 3)
#   ... zmiana idzie protokołem #0 (ETAP 0→7) — poza zakresem drivera ...
python3 $D/driver.py dod $D/fixtures/fixture-diff-complete.diff --evidence $D/fixtures/fixture-evidence-complete.txt
#   ^ 3. PRZED COMMITEM: bramka DoD (exit 1 = STOP); podstaw WŁASNY diff/ref + evidence
python3 $D/driver.py handoff --temat "..." --wynik "..."       # 4. KONIEC: szablon wpisu do memory
```

Zweryfikowane wyjścia (2026-07-17):

- `brief` → sekcja 1 to dosłowny output `run-dispatch-v2/driver.sh health` (rc w nagłówku), np.
  `# ostatni tick (…15:53:06…): verdict=OK / pytest: 5162 passed, … 0 failed`; dalej: 23 otwarte
  nagłówki HOLD/P0/CURRENT z `todo_master.md`, `atq` + timery `review|verdict` obok wskaźnika na
  `shadow-jobs-registry.md`, `git log -10`/`status`/`tmux ls`/świeże `.bak` (recon C1), kolejka P1→P6.
- `scope "równe traktowanie no-GPS"` → klasa `rowne-traktowanie-pozycji` z **8/8** miejscami
  (F1.7, `_selection_bucket`, `_demote_blind_empty`, `_best_effort_fastest_pickup_key`,
  `drive_min_calibration`, `auto_assign_gate` G7, `reassignment_forward_shadow`, `feed.py` konsoli),
  każde z żywą weryfikacją grep (`OK (×N)` / `DRYF-SYMBOLU` / `BRAK-PLIKU`) i rubryką
  `[ TAK / N-D + powód ]` do wypełnienia.
- `dod fixtures/fixture-diff-incomplete.diff` → **exit 1**, m.in.
  `FAIL flaga ENABLE_CTO_FIXTURE_DEMO: test ON≠OFF` + `FAIL bliźniaki [selekcja-tiebreak]`.
- `dod fixtures/fixture-diff-complete.diff --evidence fixtures/fixture-evidence-complete.txt` → **exit 0**.

## Kontrakt `dod`

Wejście: plik unified-diff **albo** ref gitowy (diff liczony `master...ref`).
Dowody wykonania podajesz plikiem `--evidence` (linie `klucz: wartość`):
`regresja:` (musi zawierać „0 failed"), `e2e:`, `pozytywny-wplyw:` (lub
`bajt-identycznosc:` dla refaktoru), `rollback:`, oraz jawne linie `N-D: <pliki> — <powód>`
dla świadomie niedotkniętych bliźniaków (linia MUSI wymieniać pliki, których dotyczy).
Wzorzec: `fixtures/fixture-evidence-complete.txt`.
Każdy FAIL → exit 1 → **zmiana częściowa = NIEZAKOŃCZONA**.

## Rejestr bliźniaków = dane, nie kod

`references/twins-registry.json` — klasy zmian (plikowe i proceduralne) → miejsca →
symbol grepowany na żywo. Kod się przeniósł → `scope` pokaże `DRYF-SYMBOLU` → popraw
WPIS (plik danych), nie driver. Nowa klasa bliźniaków wykryta w sesji → dopisz ją TU
i (jeśli to luka protokołu) do `memory/ziomek-change-protocol.md` — protokół jest żywy.
Uszkodzony/pusty rejestr = exit 2 (fail-closed): pusta mapa wyglądałaby jak „nic do
sprawdzenia", czyli kłamiący przyrząd (C9).

## Gotchas

1. **`dod` PASS ≠ DoD zaliczony.** To brama na *oczywistą* niekompletność (heurystyki
   na diffie). Pełny DoD = `ZIOMEK_DEFINITION_OF_DONE.md` (7 ptaszków + anty-entropia),
   niezależna ocena = `ziomek-blind-review`. `dod` nie zwalnia z myślenia — blokuje
   tylko wstyd, który już się zdarzał.
2. **`scope` nie wypełnia tabeli za ciebie.** Rubryka `[ TAK / N-D + powód ]` to twoja
   robota (ETAP 3); rejestr mówi GDZIE patrzeć, nie CO zdecydować.
3. **`brief` niczego nie orzeka** (C10): liczby z przyrządów są ufne dopiero po
   oracle-kalibracji; statusy z dokumentów to hipotezy z chwili T (C15).
4. Klasyfikacja `scope` jest keywordowa — temat-nowość może nie trafić: wtedy
   `--klasa <nazwa>` albo dopisz keywords do rejestru. Exit 3 = „sklasyfikuj ręcznie",
   celowo NIE zgaduje.
5. Marker `N-D` liczy się **per plik**: linia z `N-D` musi wymieniać dany plik
   (basename wystarczy); goły token „N-D" bez plików NIE wyłącza parytetu (zacieśnione
   po ślepej recenzji 17.07). Powód nadal oceni dopiero ślepa recenzja — nie oszukuj
   bramki, którą sam sobie postawiłeś.
6. `dod` na refie gitowym wymaga czystego dostępu do `git` (bez `ZIOMEK_CTO_NO_LIVE`);
   na pliku `.diff` działa hermetycznie.

## Zakres i bezpieczeństwo

Całość **read-only**: driver nie deployuje, nie flipuje flag, nie restartuje usług,
nie pisze do memory ani żywego stanu (`handoff` tylko drukuje szablon). Wszystko, co
mutuje, idzie protokołem #0 (`memory/ziomek-change-protocol.md`, ETAP 0→7) za ACK
właściciela; ODR-002 — żaden skill nie nadaje execution authority. Świadomie POZA
zakresem: `flags`/diagnostyka usług (→ run-dispatch-v2), recenzja kandydata
(→ ziomek-blind-review).

## Selftest (egzekwowany co noc)

```bash
.claude/skills/ziomek-cto/selftest.sh   # 17/17 PASS
```

Oracle zewnętrzny, nie autowalidacja: komplet 8 bliźniaków no-GPS **z protokołu #0**
(scope musi zwrócić 8/8), odrzucenie fixture-diffa bez testu ON≠OFF / przyjęcie
kompletnego (wymogi = ETAP 4/5/7 #0), dowód delegacji `brief`→run-dispatch-v2 (mock +
grep anty-reimplementacyjny) oraz mutation-proby (C13/C14): usunięty bliźniak z rejestru
i wycięty dowód regresji MUSZĄ być łapane. Wpięty w nocną regresję
(`tests/test_skills_selftest.py`) — regresja drivera zapali ALERT strażnika.

## Rollback

Usuń katalog skilla (`rm -rf .claude/skills/ziomek-cto`) i zrewertuj commit skilla
(`git revert <sha>`) — revert cofa też wpis w `tests/test_skills_selftest.py` (parametr
`ziomek-cto`) i wiersz w `.claude/skills/README.md`. Po rollbacku re-seed manifestu
strażnika (`--update-manifest`, fail-closed), bo nodeid selftestu zniknie z suity.
