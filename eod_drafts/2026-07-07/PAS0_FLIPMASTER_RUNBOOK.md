# PAS 0 — FLIPMASTER RUNBOOK (dyżur na żądanie, model ①)

> **Autor:** Pas 0 (FLIPMASTER) · **Zbudowany:** 2026-07-07 ~18:15 UTC (20:15 Warsaw, wt) · **Tryb budowy: READ-ONLY** (żaden flip/restart/Telegram/at-job NIE wykonany).
> **Cel:** gotowa, jednoznaczna procedura na moment, gdy Adrian da **osobny, jawny ACK** dla konkretnego flipu. Aktywacja = odpalenie sesji z tym plikiem + ACK.
> **⛔ WSZYSTKIE 4 FLIPY WSTRZYMANE decyzją Adriana.** Nic w tym pliku nie jest zgodą — ACK musi paść osobno per flip.
> **Wyłączność:** flags.json ma JEDNEGO pisarza = Pas 0 / dyżurny FLIPMASTER. Protokół #0 ETAP 6 obowiązuje.
>
> **📌 ROZSZERZENIE Sprint 27-A (2026-07-07 19:26 UTC = 21:26 Warsaw, READ-ONLY):** (1) **O2-K1 FLIPNIĘTY 19:05 UTC** — werdykt zamknięty = **🟢 ZDROWY** (osobny plik `S27A_o2k1_verdict.md`; tabela §1 + §7 zaktualizowane). (2) Dodane **2 procedury** dojrzewające w Sprincie 27: **§9 route-order** `ENABLE_ROUTE_ORDER_UNIFIED` (⚠ MERGE+restart, nie hot) i **§10 conditional-ETA** `ENABLE_ETA_CELL_RESIDUAL_CORRECTION` (HOT po 27-C). Nadal: **WSZYSTKIE flipy WSTRZYMANE — każdy = osobny jawny ACK Adriana przy dojrzałej bramce.**

---

## 0. STAN NA TERAZ (zweryfikowany na żywo 18:00–18:12 UTC; delta 19:26 niżej w §1/§7/§9/§10)

- **Czas:** 2026-07-07 **18:12 UTC = 20:12 Warsaw, wtorek.**
- **PEAK TERAZ:** 🔴 **TAK — dinner peak (17–21 Warsaw).** Off-peak wraca **po ~21:00 Warsaw (19:00 UTC)**. Sob 16–21 też peak. **W peaku NIE flipujemy** — mimo że flipy są HOT (bez restartu), zmieniają ŻYWE propozycje (dispatch-shadow zasila realną konsolę/1-klik → [[ziomek-shadow-is-live-proposals]]), więc reguła peak obowiązuje.
- **Silnik żywy:** `dispatch-shadow` = active, `dispatch-panel-watcher` = active. flags.json = 272 klucze, 0600 root, mtime 06-07 22:14.
- **HEAD** `39fb1c9` (mode-layer T2.4). Refaktor core/ ŻYWY (core/decide.py, candidates.py, planner.py, scorer.py, gates.py…).

### Mechanizm flipu — HOT potwierdzony u źródła
`common.flag(name, default) = load_flags().get(name, default)` oraz `common.decision_flag(name)` (flags.json → stała modułu → False) czytają **flags.json przy KAŻDYM wywołaniu (hot-reload)**. Kanon wartości = flags.json (wspólny cross-proces: shadow long-running + plan-recheck/czasowka/resweep = oneshot fresh process). **Wszystkie 4 flipy = HOT, ZERO restartu.** Efekt ≤1 tick (~60 s). Klucze O2/K5 **NIE ISTNIEJĄ** w flags.json (dopisanie); K4b istnieje =False (zmiana wartości).

### KOMENDA FLIPU — wzorzec (atomic temp+fsync+rename, 0600, bez jq, bez heredoca)
```bash
# 1) BACKUP (ZAWSZE PIERWSZE)
cp /root/.openclaw/workspace/scripts/flags.json \
   /root/.openclaw/workspace/scripts/flags.json.bak-pre-<FLIP>-$(date +%Y%m%d-%H%M)

# 2) SET (podmień KLUCZ; True=ON / False=OFF-rollback)
/root/.openclaw/venvs/dispatch/bin/python -c "import json,os,tempfile; p='/root/.openclaw/workspace/scripts/flags.json'; d=json.load(open(p)); d['<KLUCZ>']=True; fd,t=tempfile.mkstemp(dir=os.path.dirname(p)); f=os.fdopen(fd,'w'); f.write(json.dumps(d,indent=2,ensure_ascii=False)); f.flush(); os.fsync(f.fileno()); f.close(); os.replace(t,p); os.chmod(p,0o600)"

# 3) VERIFY (klucz obecny + wartość)
/root/.openclaw/venvs/dispatch/bin/python -c "import json; print('<KLUCZ> =', json.load(open('/root/.openclaw/workspace/scripts/flags.json')).get('<KLUCZ>','<<BRAK>>'))"
```
> Pure edit flags.json → **NIE py_compile** (żaden .py nietknięty). Walidacja = krok 3 (JSON parsuje + klucz OK). ROLLBACK = ten sam SET z `=False` (hot, ≤1 tick). Backup pre-flip pełny bazowy: `flags.json.bak-pre-s2-2026-07-05`.

---

## 1. TABELA GOTOWOŚCI (flip → status na 2026-07-07 18:12 UTC)

| Flip | Klucz | Typ | Werdykt | Bramki spełnione? | STATUS |
|---|---|---|---|---|---|
| **O2-K1** | `ENABLE_O2_CAPZ_RESEQ` | dopisz, HOT | **GO potwierdzony** (at-208 Pn 19:30, korpus 1463 worki, Z=20 improved 10,0%, regres 0, p90 detour 5,84≤8) | verdykt ✓ · testy ✓ · hot ✓ · off-peak ✓ · backup ✓ | 🟢 **ON — LIVE 2026-07-07 19:05 UTC** (21:05 Warsaw, off-peak; ACK Adriana). Werdykt wczesny = **ZDROWY** (`S27A_o2k1_verdict.md`); domknięcie 2-dniowe ~09.07 (peak Śr). |
| **O2-K2** | `ENABLE_SLA_GATE_READY_ANCHOR` | dopisz, HOT | prereqi przypięte; parytet picku **MEASURED** (n=24, kierunek 3/3, 0 regres; re-pomiar 19:31 identyczny — `S27D_o2k2_reparity.md`) | parytet picku ✓ · **O2-K1 ON ✓** · **L3 ≥2d ✗ (do ~08.07 12:35)** | 🔴 **CZEKA** — O2-K1 już ON; brakuje tylko okna L3 ≥2d + ACK. Parytet picku zaliczony. |
| **K4b** | `ENABLE_ETA_LOAD_AWARE` | =True, HOT | **PASS z TRADE-OFFEM**: bias −3,73→+0,42, celność +1,4pp, **KOSZT p90 +6,1→+10,7** | werdykt ✓ · testy ✓ · **jawna akceptacja trade-offu p90 przez Adriana ✗** · off-peak ✗ | 🔴 **CZEKA** — wymaga **jawnej zgody Adriana na kompromis p90** (nie sam ACK flipa). |
| **K5** | `PENDING_RESWEEP_LIVE` | dopisz, HOT | ZBUDOWANE (testy 7/7, probes 3/3 KILLED); geometria ON = bramka OK | geometria ON ✓ · testy ✓ · **dry-run na żywych wiszących ✗ (05.07 hanging=0 → no-op)** · off-peak ✗ | 🔴 **CZEKA** — najpierw **dry-run driver na realnych wiszących** (dzień, poza peakiem) → dopiero ACK. |
| **route-order** | `ENABLE_ROUTE_ORDER_UNIFIED` | dopisz, **⚠ MERGE+RESTART (NIE hot)** | — | ⛔ **KOD NIE ISTNIEJE** (moduł `route_order.py` niezacommitowany/utracony) · dowód 0-diff ✗ | ⛔ **ZABLOKOWANY U ŹRÓDŁA** — patrz §9 + `S27B_routeorder_proof.md`. Flaga nie ma konsumenta; flip niemożliwy przed odbudową modułu. |
| **conditional-ETA** | `ENABLE_ETA_CELL_RESIDUAL_CORRECTION` | =True (dopisz), **HOT** | shadow zbiera `eta_cell_corrected_min` od 05-07; W0.5 E-7-GO | HTML-escape fix ✓ (27-C, worktree `s27c-eta-html-escape` b3e91da — **merge za ACK**) · karta +5,14% MAE ✗ (okno ~09.07) · off-peak(hot) | 🔴 **CZEKA** — patrz §10. Fix u źródła gotowy; brakuje karty dowodowej +5,14% (okno domyka ~09.07) + merge + ACK. |

**Wniosek (delta 19:26 UTC):** **O2-K1 = ON** (flip 19:05 wykonany, werdykt zdrowy). Pozostałe: **O2-K2** czeka tylko na okno L3 (~08.07 12:35) + ACK; **conditional-ETA** — fix u źródła gotowy (27-C), czeka na kartę +5,14% (~09.07) + merge + ACK; **route-order ZABLOKOWANY** (brak kodu — §9); **K4b/K5** bez zmian.

---

## 2. K2 / K3 — status kontekstowy (JUŻ ON, nie flipuję; potwierdzenie bramek)

- **K2 `ENABLE_LEXQUAL_GEOMETRY_TIEBREAK` = True** (flip 05-07 18:51). **Okno obserwacji 2d domyka się ~07-07 18:51 UTC (≈ TERAZ).** **WERDYKT JUŻ WYDANY (plik `dispatch_state/lexqual_geometry_k2_obs_verdict_2026-07-07.txt`, mtime 12:33 UTC): 🟢 ZIELONY / PARETO** — replay live 2d: 16 flipów, 100% poprawy spreadu, med −3,21 km, Δr6=0, picki>8km 26→22; quant=1 potwierdzony gorszy. **K2 zostaje ON na stałe → domyka L6.C ETAP-5.** To gwarantuje przesłankę geometrii dla K5 (C3).
- **K3 `ENABLE_ENGINE_CLAIM_LEDGER` = True.** Marker **`claim_ledger_applied` żywy top-level** w shadow_decisions (208/500 ostatnich rek.), `REPO_COST_SWALLOW`=0/48h (fail-loud milczy = brak trucizny). Zdrowy.

---

## 3. PROCEDURA — O2-K1 `ENABLE_O2_CAPZ_RESEQ` (🟡 GOTOWY do ACK)

Wąska reguła cap-Z reseq: reseq PLANU tylko gdy (a) detour ≤ `O2_CAPZ_DETOUR_MAX_MIN`=8 (b) carried ≤ `O2_CAPZ_Z_MIN`=20 (c) gain ≥ `O2_CAPZ_MIN_GAIN_MIN`=2 (d) SLA nie rośnie; carried-first via `lock_first` (bajt-parytet z bruteforce). Trójka feasibility↔route_simulator↔plan_recheck dziedziczy przez 1 return; objm_lexr6 dziedziczy (bez własnego solve). Kod OFF=bajt-parytet.

**(a) KOMENDA** — wzorzec §0, `<KLUCZ>=ENABLE_O2_CAPZ_RESEQ`, `<FLIP>=o2k1`, wartość `True`. HOT, dopisanie klucza. Bez restartu.

**(b) BRAMKA PRE-FLIP**
- [ ] **ACK Adriana** dla O2-K1 (jawny, osobny).
- [x] Werdykt GO **potwierdzony** — `bundle_calib_review_l0.log` (at-208, Pn 06-07 19:30): korpus λ=0 1463 uniq worki (od 03-07 07:46, incl. peak Pn), **Z=20 improved 10,0%, med ΔO2 9,65, detour med −2,33/p90 5,84 ≤ cap 8, regres_o2=0**. Zgodny z wstępnym GO z 05.07, czystszy ogon. (Nagłówek „−59 km/d" z briefu = pochodna 10% × oszczędność detour — prymitywy potwierdzone, sama liczba km/d nie jest w logu.)
- [ ] **OFF-PEAK** (NIE 11–14, 17–21 Warsaw, NIE sob 16–21). Teraz peak → czekać do ~21:00 Warsaw.
- [x] Testy zielone: `test_o2_capz_reseq_2026_07_02` 20/20 + pas O2/geometria/K5 = **82/82 (bieg 18:12 UTC, 7,9 s)**. **Przy flipie ponów** krótki bieg + pełną regresję jeśli od dziś były commity.
- [ ] **Backup** flags.json wykonany (krok 1 §0).
- [x] HOT potwierdzony (route_simulator_v2:902 `_Cz.flag`, feasibility_v2:861 metryka `o2_capz`).

**(c) MONITOR 1h po flipie**
- `journalctl -u dispatch-shadow --since "10 min ago" | grep -iE "error|traceback|exception"` → **oczekiwane PUSTE.**
- Latencja: `journalctl -u dispatch-shadow --since "15 min ago" | grep latency_ms | tail` → p95 bez skoku (size-guard ≤8 stopów = brak ryzyka latencji).
- Carried-first / SLA: brak nowych ostrzeżeń carried-first-guard; `regres_o2` w kolejnym biegu `bundle_calib_shadow` = 0.
- Wizualnie: 2–3 propozycje multi-order na konsoli mają sensowny (nie dłuższy) porządek stopów.
- Dziedziczenie: `dispatch-plan-recheck` bez błędów (ta sama reguła w _sweep).

**(d) ROLLBACK** — HOT: SET `ENABLE_O2_CAPZ_RESEQ=False` (lub usuń klucz) → ≤1 tick. Głęboki (gdyby podejrzenie kodu): `git revert 3947276` + restart shadow (za ACK). OFF=bajt-parytet, więc hot-off = pełny powrót.

---

## 4. PROCEDURA — O2-K2 `ENABLE_SLA_GATE_READY_ANCHOR` (🔴 CZEKA)

Bramka 35-min SLA kotwiczona na READY (pickup_at→READY) zamiast NOW; realna zmiana decyzji. **SEKWENCJA: O2-K1 MUSI być ON PIERWSZY** (pod O2-K1=ON klucz porównań plan_recheck jest sla-free z konstrukcji — `test_o2_k2_plan_recheck_parity`).

**(a) KOMENDA** — wzorzec §0, `<KLUCZ>=ENABLE_SLA_GATE_READY_ANCHOR`, `<FLIP>=o2k2`, `True`. HOT, dopisanie.

**(b) BRAMKA PRE-FLIP**
- [ ] **ACK Adriana** dla O2-K2.
- [ ] **O2-K1 = ON** (warunek twardy sekwencji).
- [ ] **L3 ≥2 dni obserwacji** od 06-07 12:35 → **domyka ~08-07 12:35 UTC.**
- [x] **Parytet picku n≥10 MEASURED** (A1 dziś, `o2_k2_pick_parity_verdict.txt` mtime 17:59): **n=24, changed 3 (12,5%), kierunek 3/3 K2-ok, 0 w złą stronę.** ⚠ ciężar próby na Pn (20/24) — opcjonalna korroboracja po kolejnym peaku (nie warunek).
- [ ] OFF-PEAK · [ ] Backup · testy `test_o2_k2_*` zielone (w biegu 82/82).
- ⛔ **NIE flipować surowego O2** (pułap 23,5% freshness-blind łamie carried-first — werdykt 02.07 podtrzymany).

**(c) MONITOR 1h** — jak O2-K1 + specyficznie: liczba ready-breachy nie rośnie (kierunek parytetu ON-pick ≤ ready-breachy); po godzinie ponów `python -m dispatch_v2.tools.o2_k2_pick_parity` (z cwd `scripts/`, venv dispatch) → kierunek K2-ok utrzymany. Journal 0 błędów.

**(d) ROLLBACK** — HOT: `=False`. Głęboki: `git revert 3947276` (wspólny commit z O2-K1 — revert zdejmuje OBA; przy rollbacku tylko O2-K2 użyj hot-off).

---

## 5. PROCEDURA — K4b `ENABLE_ETA_LOAD_AWARE` (🔴 CZEKA na akcept trade-offu)

Bufor optymizmu nogi ODBIORU z tabeli kalibracji (`eta_load_aware_calib.json`) — oś OBIETNICY (przesuwa `eta_pickup_utc`/`travel_min`), **feasibility_v2 NIETKNIĘTE** (GATE-STRICTER = osobny pas, inwersja HARD, NIE tu). Hook żywy: `core/candidates.py:617-633` (`decision_flag("ENABLE_ETA_LOAD_AWARE")`).

**(a) KOMENDA** — wzorzec §0, `<KLUCZ>=ENABLE_ETA_LOAD_AWARE`, `<FLIP>=k4b`, `True` (klucz istnieje=False → zmiana). HOT.

**(b) BRAMKA PRE-FLIP**
- [ ] **JAWNA AKCEPTACJA ADRIANA TRADE-OFFU**, nie sam ACK flipa. Werdykt `eta_load_aware_replay_verdict.txt` (out-of-sample 03-07→06-07, n=415): bias med **−3,73→+0,42**, celność |err|≤5 **45,8%→47,2%**, **KOSZT: p90 +6,14→+10,71 min** (ogon obietnicy gorszy). Kryteria PASS, ale to kompromis „lepsze centrowanie kosztem ogona".
- [ ] OFF-PEAK · [ ] Backup · [x] testy `test_eta_load_aware_l51` w biegu 82/82.
- [x] HOT via `decision_flag` (flags.json kanon). SHADOW zbiera `eta_la_buffer_min` bezwarunkowo od 05-07 18:44.

**(c) MONITOR 1h** — bias obietnicy odbioru centruje ku 0 (ON przesuwa `eta_pickup_utc`); p90 ogona akceptowalnie wyższy (spodziewane +10,7). Sprawdź, że **feasibility NIE zmieniła się** (kwalifikacja kandydatów bez zmian — z konstrukcji). `journalctl -u dispatch-shadow` 0 błędów. Po godzinie ponów `tools/eta_load_aware_replay.py` → kierunek bias utrzymany.

**(d) ROLLBACK** — HOT: `=False` → wraca oś obietnicy bez bufora. Głęboki: `git revert e020766` (+ merge kontekst) + restart za ACK.

---

## 6. PROCEDURA — K5 `PENDING_RESWEEP_LIVE` (🔴 CZEKA na dry-run)

Live-resweep = **podmiana propozycji dla KONSOLI/1-klik** (`pending_proposals.json`), **NIE Telegram** (wyciszony 26.06 — nietykalny). `_live_apply` za `FLAG_LIVE ∧ live_gate_open()` (bramka geometrii L6.C — MUSI zostać ON). Bezpieczniki: `PENDING_RESWEEP_MARGIN`=15, `MAX_HANGING`=8, `LIVE_MAX_ACTIONS_PER_TICK`=3, fcntl RMW + TOCTOU-guardy, fail-soft totalny.

**(a) KOMENDA** — wzorzec §0, `<KLUCZ>=PENDING_RESWEEP_LIVE`, `<FLIP>=k5`, `True`. HOT (timer `dispatch-pending-resweep-shadow` żywy, kod inertny do flipu; `ENABLE_PENDING_RESWEEP` już ON).

**(b) BRAMKA PRE-FLIP**
- [ ] **ACK Adriana** dla K5.
- [ ] **DRY-RUN na REALNYCH WISZĄCYCH** (obowiązkowy — 05.07 trafił hanging=0 → no-op, brak dowodu SWAP-ów na żywych danych):
  `/root/.openclaw/venvs/dispatch/bin/python eod_drafts/2026-07-05/k5_dryrun_driver.py`
  Uruchamiać **w dzień roboczy z wiszącymi, POZA peakiem**. Oczekiwane: realne SWAP-y (old_cid→new_cid, sensowne delta/reason), **żywy `pending_proposals.json` NIETKNIĘTY** (driver pisze w /tmp, assert anty-prod). hanging=0 → wynik = no-op (informacja, powtórz gdy są wiszące).
- [x] **Geometria ON** (K2 zielona) — bramka `live_gate_open()` przejdzie.
- [ ] OFF-PEAK · [ ] Backup · [x] testy `test_pending_resweep_live_k5` 7/7 w biegu 82/82.

**(c) MONITOR 1h** (pierwsza godzina NADZOROWANA)
- `journalctl -u dispatch-pending-resweep-shadow --since "10 min ago" | grep -iE "live_act|live_acted|error"` → akcje live obecne, 0 błędów.
- `pending_proposals.json`: obecność wpisów `resweep_live` {ts, old_cid, new_cid, delta_vs_now, reason} przy podmienionych.
- **Konsola po akceptacie podmienionej propozycji:** kanon planu = **NOWY kurier** (`panel_watcher._save_plan_from_pending`), `PANEL_OVERRIDE` NIE krzyczy fałszywie.
- ⚠ **C3 ZALEŻNOŚĆ:** geometria MUSI zostać ON. Flip geometrii OFF przy K5 ON → `live_gate_open()` HOLD + warning, akcje ustają (bezpieczne, ale świadome).

**(d) ROLLBACK** — HOT: `PENDING_RESWEEP_LIVE=False` (lub usuń) → akcje live ustają ≤1 tick, resweep wraca do shadow-only. Głęboki: `git revert 9989d79` + restart za ACK.

---

## 7. WERYFIKACJA FLAG NA ŻYWO vs OCZEKIWANIA (higiena stanu)

Odczyt flags.json 18:00 UTC. **ZERO rozbieżności** — 8/8 zgodne:

| Flaga | flags.json | Oczekiwane (brief) | Zgodność |
|---|---|---|---|
| ENABLE_LEXQUAL_GEOMETRY_TIEBREAK | `True` | True (K2) | ✅ |
| ENABLE_ENGINE_CLAIM_LEDGER | `True` | True (K3/lock) | ✅ |
| ENABLE_ETA_QUANTILE_R6_BAGCAP | `True` | True (gold furtka) | ✅ |
| ENABLE_ETA_LOAD_AWARE | `False` | False | ✅ |
| PENDING_RESWEEP_LIVE | `False` | False | ✅ |
| ENABLE_O2_CAPZ_RESEQ | **BRAK** | BRAK (=OFF) | ✅ |
| ENABLE_SLA_GATE_READY_ANCHOR | **BRAK** | BRAK (=OFF) | ✅ |
| ENABLE_ETA_CELL_RESIDUAL_CORRECTION | **BRAK** | BRAK (=OFF) | ✅ |

**Potwierdzenie „BRAK = OFF" u źródła:** kod-default `False` dla wszystkich 3 (common.py:491/515/516/569 — `ENABLE_ETA_CELL_RESIDUAL_CORRECTION`/`ENABLE_O2_CAPZ_RESEQ`/`ENABLE_SLA_GATE_READY_ANCHOR`/`ENABLE_ETA_LOAD_AWARE`), a `flag()`/`decision_flag()` przy braku klucza zwracają default → **genuinnie OFF.** Żadna „kłamiąca" flaga.

### Uwagi higieniczne (nie-rozbieżności flag, ale do świadomości)
1. **Werdykt O2-K1 na dysku** (`o2_narrow_rule_replay_verdict.txt`) wciąż nosi datę 05-07 „GO wstępny" (2,6 dnia). **Potwierdzenie finalne żyje osobno** w `logs/bundle_calib_review_l0.log` (at-208, Pn 19:30, 3,5+ dnia = GO). `atq` PUSTE (at-205/206/208 wykonały się i wyczyściły). Rekomendacja: przy flipie O2-K1 dopisać 1 linię potwierdzenia at-208 do verdict.txt (spójność), ale **nie blokuje** — dowód GO jest twardy.
2. **Night-guard `world_replay_gate` (dziś 02:00): DIFFS n=88, 12 krytycznych** — ale to **znana klasa luki nagrywania world_record v0 / score-drift** (ten sam best_cid, delta score ±20–40; jedyna realna zmiana decyzji = 485927, już oflagowana do diagnozy v1). Okno replayowało dane Pn sprzed wdrożenia v1 (v1 żywy od 07-07 00:42). **Informacyjny, NIE blokuje flipów.**
3. **Adopcja 5b:** verdict `gps_delivery_validation` (dziś 18:01) pokazuje **n=1051 dostaw z fizyczną prawdą geofence** (median klik−fizyczny +2,15 min) — DUŻO zdrowiej niż „1/551" z 05.07. ⚠ liczy przyjazdy z geofence (GPS serwerowy), niekoniecznie = penetracja apki v2 vc60. **5b bramkuje feas_carry #483000, NIE moje 4 flipy** — sygnalizuję jako pozytyw-do-weryfikacji (Pas A3).

---

## 8. SEKWENCJA WYKONANIA (gdy przyjdą ACK-i)
1. **O2-K1** (po ~21:00 Warsaw + ACK) → obserwacja.
2. **O2-K2** — dopiero po O2-K1 ON **i** L3 ≥2d (~08-07 12:35) + ACK (parytet picku już OK).
3. **K5** — po dry-runie na żywych wiszących (dzień, off-peak) + ACK; geometria trzymana ON.
4. **K4b** — po jawnej akceptacji trade-offu p90 przez Adriana + off-peak.

**Każdy krok = osobny jawny ACK Adriana. Peak/telegram/at-job = poza zakresem bez OK.** Rollback każdego = wartość wstecz w flags.json (HOT, ≤1 tick).

**Kolejność zaktualizowana (delta Sprint 27):**
1. **O2-K1** — ✅ WYKONANE (ON 19:05 UTC). Obserwacja 2d → domknięcie werdyktu ~09.07.
2. **O2-K2** — po L3 ≥2d (~08-07 12:35) + ACK (O2-K1 ON ✓, parytet picku ✓).
3. **conditional-ETA (§10)** — po merge 27-C + karcie +5,14% (~09.07) + ACK. HOT.
4. **K5** — po dry-runie na żywych wiszących + ACK.
5. **K4b** — po jawnej akceptacji trade-offu p90 + off-peak.
6. **route-order (§9)** — ⛔ ZABLOKOWANY: najpierw ODBUDOWA modułu `route_order.py` (osobny sprint/agent), potem dowód 0-diff, potem MERGE+restart + ACK. NIE flip flagą — kod nie istnieje.

---

## 9. PROCEDURA — route-order `ENABLE_ROUTE_ORDER_UNIFIED` (⛔ ZABLOKOWANY U ŹRÓDŁA — NIE hot, MERGE+restart)

> ⛔ **BLOKER KRYTYCZNY (zweryfikowany 2026-07-07 19:2x UTC, 27-B):** moduł `route_order.py` i flaga `ENABLE_ROUTE_ORDER_UNIFIED` **NIE ISTNIEJĄ w żadnej gałęzi, historii git ani na dysku.** Gałąź `worktree-agent-a8a36495468ae05f0` z handoffu jest na `39fb1c9` (mode-layer T2.4) — **bez route_order.py**. Praca komponentu C sprintu multiagent 07.07 (deklarowana w `SPRINT_MULTIAGENT_PLAN_0707.md:40` jako „route_order.py PURE + mapa 5 luster + parytet dziesiątki tys. porównań") **nie została zacommitowana/utrwalona.** Skutek: **dowód 0-diff nie może być wykonany — nie ma czego flipować.** Pełna analiza + mapa migracji: `S27B_routeorder_proof.md`.

**⚠ Ten flip jest INNEJ NATURY niż O2/K4b/K5:** to **zmiana KODU** (nowy moduł + delegacja luster), NIE dopisanie klucza do flags.json. Wymaga **MERGE do master + restart `dispatch-plan-recheck` + `dispatch-shadow`** (nie hot-reload — flaga bramkuje ścieżkę importu modułu, nie tylko wartość odczytywaną co tick). Dlatego procedura poniżej = szkielet „gdy moduł powstanie", NIE gotowy flip.

**(a) WARUNEK WSTĘPNY (przed jakąkolwiek procedurą flipu):**
- ⛔ **ODBUDOWA / ODZYSKANIE `route_order.py`** (osobny sprint — nucleus = promocja istniejącego czystego `route_podjazdy.py`, który JUŻ jest wspólnym źródłem apki+parytetu; patrz `S27B` §3). Bez tego kroki (b)–(d) są bezprzedmiotowe.

**(b) BRAMKA PRE-FLIP (gdy moduł istnieje):**
- [ ] `route_order.py` + `ENABLE_ROUTE_ORDER_UNIFIED` zmergowane do master (przegląd + ACK).
- [ ] **Dowód REPLAY 0-DIFF** na żywym korpusie 2 dni: `ENABLE_ROUTE_ORDER_UNIFIED` ON vs OFF = **identyczna projekcja kolejności** `[(typ, sorted(order_ids))]` (nie tylko golden — na żywych bagach). To dowód „warto (jedno źródło) + bez regresji (0 diff)".
- [ ] Testy `test_route_order_*` zielone (golden + live-parity) + pełna regresja.
- [ ] Monitor `ziomek_time_route_monitor` Q3 = 0 mismatch (obecnie ✓ ZIELONY, ale wygasa **10-07** — jego następcy = testy golden/live-parity, bez wygaśnięcia).
- [ ] OFF-PEAK · [ ] Backup flags.json + tag pre-merge.

**(c) KOMENDA** — **NIE** wzorzec §0 (nie sama flaga). Sekwencja: `git merge` gałęzi route-order do master → `py_compile` → testy → **restart `dispatch-plan-recheck` + `dispatch-shadow`** (ACK) → dopiero potem SET `ENABLE_ROUTE_ORDER_UNIFIED=True` (jeśli architektura wymaga flagi ON po restarcie; jeśli moduł jest domyślną ścieżką po merge — flaga może być zbędna). Zależne od finalnego kształtu modułu.

**(d) MONITOR 1h** — `ziomek_time_route_monitor` Q3 dalej 0 mismatch; konsola↔apka↔silnik projekcja identyczna; `journalctl` obu serwisów 0 błędów; wizualnie porządek stopów niezmieniony (0-diff = z definicji identyczny).

**(e) ROLLBACK** — `ENABLE_ROUTE_ORDER_UNIFIED=False` (jeśli flaga bramkuje) + restart; głęboki: `git revert <merge>` + restart obu serwisów za ACK.

**Migracja pozostałych luster (PO flipie silnika, każde osobno — `S27B` §3):** K3 `fleet_state._build_route` (panel, osobny deploy — najtrudniejsze, cross-repo), K4 `RouteLogic.kt` (apka — tylko pin `PICKUP_MERGE_MIN=10`, konsumuje kolejność wprost), 5. prymityw `courier_orders._repair_dropoffs_after_pickups` (konwergencja bliźniaka `kind_key`↔`type`). Silnik `plan_recheck._apply_canon_order_invariants` **ZOSTAJE źródłem** (relax/lex/no-return = logika decyzyjna, NIE do czystego renderera).

---

## 10. PROCEDURA — conditional-ETA `ENABLE_ETA_CELL_RESIDUAL_CORRECTION` (🔴 CZEKA na kartę + merge; HOT)

Korekta ETA per-komórka floty (slot×solo/worek) + **warstwa RESTAURACJI** (addytywna) z `dispatch_state/eta_cell_residual_map.json`. **Oś OBIETNICY** (koryguje przewidywaną ETA dostawy) — **feasibility_v2 / R6 NIETKNIĘTE** (SOFT nie osłabia HARD). Konsument: `shadow_dispatcher.py:560` (`calib_maps.eta_cell_residual_correct`, dziś SHADOW: `_eta_cell_corrected_shadow` liczony zawsze, `eta_cell_correction_flag` odzwierciedla stan). HOT via `C.flag` (flags.json kanon).

**(a) KOMENDA** — wzorzec §0, `<KLUCZ>=ENABLE_ETA_CELL_RESIDUAL_CORRECTION`, `<FLIP>=condeta`, `True` (dopisanie klucza). HOT, bez restartu.

**(b) BRAMKA PRE-FLIP:**
- [ ] **ACK Adriana** dla conditional-ETA.
- [x] **Fix u źródła HTML-escape (27-C)** — `calib_maps.eta_cell_residual_correct` robi `html.unescape` przed lookupem (parytet z generatorem `eta_cell_residual_build:114`). **Gałąź `s27c-eta-html-escape` commit `b3e91da`** — ⚠ **MERGE do master za ACK** (poza tym flip trafi w mapę bez warstwy restauracji dla 3 restauracji z encjami = 89/712 decyzji w oknie 07-07). Test parytetu ON≠OFF zielony, mutation-probe KILLED, regresja zielona.
- [ ] **KARTA DOWODOWA +5,14% MAE** na oknie hold-out 2d (do ~09.07): CI nieobejmujące 0, breach bez wzrostu, kierunek bias ku 0. Dowód POZYTYWNEGO wpływu (ETAP-5) — bez tego flip = tylko „bez regresji".
- [ ] OFF-PEAK (mimo HOT — zmienia żywą obietnicę na konsoli/1-klik) · [ ] Backup flags.json.
- [x] SHADOW zbiera `eta_cell_corrected_min` bezwarunkowo od 05-07 18:44 (parytet z produkcyjnym konsumentem).

**(c) MONITOR 1h po flipie** — obietnica ETA dostawy centruje ku realnej (MAE ↓ na świeżym oknie); **feasibility BEZ zmian** (kwalifikacja kandydatów, R6 — z konstrukcji nietknięte); `journalctl -u dispatch-shadow` 0 błędów; `grep -c eta_cell_corrected_min shadow_decisions.jsonl` rośnie; warstwa restauracji trafia dla nazw z encjami (po fixie 27-C).

**(d) ROLLBACK** — HOT: `ENABLE_ETA_CELL_RESIDUAL_CORRECTION=False` (lub usuń klucz) → ≤1 tick, wraca surowa obietnica. Fix 27-C (unescape) jest niezależny od flagi (poprawia tylko trafność mapy) — zostaje.
