# Fala #7 sentinel-as-data (2026-07-18 wieczór) — EVIDENCE

GO Adriana: „Dawaj falę #7 sentinel-as-data". Cel fali (szkielet/audyt 360 ch.16):
skolapsować kanał sentinela pozycji do nazwanych obron, zachować byte/decision
parity i **ZMNIEJSZYĆ miernik entropii #7** (rano: 11 żywy silnik).

## Wykonane (11 → 0)

| Miejsca | Zmiana |
|---|---|
| `courier_resolver` ×5 (:1075/:1674/:1683/:1737/:1789) | 5 rozsianych przypisań `cs.pos=BIALYSTOK_CENTER` + pos_source → JEDEN helper `_synthetic_pos_fallback(cs, source, shift_start_min)` — **wymusza parę pos+pos_source** (koniec klasy „pozycja-fikcja bez labelu"); labele/logi call-site'ów bajt-w-bajt |
| `chain_eta` ×2 (:128/:149) | nazwany kanał `_center_pos_fallback()`; wartości/warningi bez zmian |
| `dispatch_pipeline` :1619 | gałąź -1000 przepisana BEZ tworzenia sentinela; parytet wyników na wszystkich klasach wejść (None/()/(0.0,y)/(None,y) → -1000 jak dotąd, (None,y) szło przez except na ten sam wynik) |
| `dispatch_pipeline` :~3507 + :~4212 | `or (0.0,0.0)` inline → **JEDYNY producent** `_osrm_guard_sentinel_coords()` (backstop guardu OSRM #81 z nazwą-obroną; pass-through parytet z `tuple(x or (0,0))`) |

**ORACLE #7 PO FALI: 0 żywy silnik** (+4 instrumenty w tools/ — poza celem, odnotowane).
Trwały strażnik: `tests/test_s7_sentinel_wave.py::test_entropy7_oracle_zero_in_live_engine`
(AST-oracle w suicie — regres #7 od teraz łamie testy).

## Dowody parytetu

- **Testy 4/4** (`test_s7_sentinel_wave.py`): oracle=0 · wartości helperów identyczne
  (CENTER/(0,0)/pass-through) · resolver zawsze para pos+source (+shift_start_min) ·
  parytet gałęzi -1000 (6 klas wejść).
- **world_replay_gate (korpus 24h, n=217): krytyczne=0** (werdykt/best_cid/best_score
  100% zgodne). 37 miękkich `pool_total −1` + 39 missów OSRM — **BISEKCJA 4-punktowa
  (f705f57 przed wszystkim / 543251f / a6a3c2e / bd82716 / working-tree z falą i ze
  stashem fali): diff IDENTYCZNY wszędzie** → różnice NIE pochodzą z fali (ani z
  żadnego dzisiejszego kodu) — patrz finding niżej. Kontrola: diff(bez fali)==diff(z falą).
- Pełna regresja (frozen): → Wyniki końcowe. (Pierwszy bieg „baseline" unieważniony:
  wystartował przed edycjami i biegł W TRAKCIE — 4 fałszywe faile mid-run; klasa
  błędu powtórzona z B2 → wniosek procesowy w Lekcjach.)

## 🆕 FINDING SYSTEMOWY (nie z tej fali; klasa #15/C10 — do backlogu)

**`world_record` NIE nagrywa `courier_last_pos.json` (store TTL 25 min)** → replay
czyta ŻYWY store ⇒ kandydaci no_gps dostają w replayu inną pozycję niż przy nagraniu
⇒ inne wywołanie OSRM ⇒ miss ⇒ kandydat-infeasible wypada z puli (`pool_total −1`;
best/feasible nietknięte — stąd krytyczne=0). **Nocny night-guard PARITY 191/191 to
częściowo „parity-bo-noc"** (o 02:00 store martwy — zero dryfu). Świeże okno 3h w
dzień: 22/77 soft + 24 missy — dryf szybki (TTL!). Fix = snapshot store w rekordzie
+ redirect w replayu (mały osobny sprint recordera, za ACK).

## DoD — tokeny

regresja: 5188 passed / 0 failed / 27 skipped / 8 xfailed (EXIT=0; frozen, wspólny bieg z fixem recordera)
e2e: world_replay_gate przez PEŁNĄ fasadę decide() na 217 realnych nagranych decyzjach — krytyczne=0 (werdykt/zwycięzca/score bit-parity); soft-klasa udowodniona bisekcją jako środowiskowa (recorder-gap), nie kodowa
replay: kontrfaktyk bisekcyjny: replay tych samych rekordów na 4 commitach + z falą/bez fali (stash) → diffy IDENTYCZNE = fala wnosi ZERO różnic decyzji; pozytyw fali = miernik entropii #7: 11→0 (cel fali z definicji szkieletu „entropia niżej") + strukturalnie wymuszona para pos+pos_source
rollback: git revert commitu fali (parytet tożsamościowy — bez flag; żadnych zmian stanu)

N-D: objm_lexr6.py — zero producentów sentinela pozycji (oracle AST skanuje CAŁY żywy silnik → poison=0 obejmuje ten plik); fala zmienia tylko PRODUKCJĘ sentineli, nie selekcję
N-D: auto_assign_gate.py — jw. (konsument pozycji, nie producent sentinela; oracle=0)
N-D: drive_min_calibration.py — jw. (oracle=0)
N-D: tools/reassignment_forward_shadow.py — jw. (oracle=0; instrumenty poza definicją miernika #7)
N-D: feasibility_v2.py — zero sentineli pozycji (oracle), nietknięty
N-D: core/candidates.py — jw.
N-D: core/selection.py — jw.
N-D: plan_recheck.py — jw.
N-D: osrm_client.py — guard #81 (konsument sentinela) celowo BEZ zmian — backstop działa jak dotąd
N-D: tools/ 4 instrumenty z sentinelami — poza definicją miernika (#7 liczy żywy silnik); ewentualna higiena instrumentów = osobno

## Wyniki końcowe

- Finalna pełna regresja (frozen): **5188 passed / 0 failed / 27 skipped / 8 xfailed** (EXIT=0, 299s) — wspólny bieg fala #7 + fix recordera. ⚠ Pierwszy „frozen" bieg (6 failed) był ZATRUTY przez zagnieżdżony worktree `dispatch_v2/wt-s7bisect-pkgroot` (guardy skanowały jego kopie — środowisko, nie kod); po `git worktree remove --force`+prune guardy 34/34 i pełna suita czysta.
- Restart: at#219 (19:05 UTC) obejmie falę razem ze startem cienia ETA (jeden restart shadow po peaku).
