# C07 — drive_speed_overshoot (lane C RUNTIME-ORACLE) — backing

**Agent:** C07-drive-speed · **Lane:** C (oracle, C9/C11) · **READ-ONLY** · 2026-06-30 ~17:00 UTC
**Instrument:** `tools/drive_speed_overshoot_verdict.py` · **Flaga silnika:** `ENABLE_DRIVE_SPEED_TIER_CORRECTION` (efektywnie **OFF** — niezależny odczyt flags.json)
**Werdykt oracle: `validated`** (instrument NIE kłamie pod realnym stanem flagi; fix `20dec97` realnie domyka fałszywy CLEAN). + 1 **mina re-flipu** u źródła (klasa K/N) niezamknięta.

DoD: ZERO edycji/restartów/flipów. Narzędzie NIE odpalane przez `main()` (pisze do `dispatch_state/`); zaimportowano TYLKO `compute()` (read-only). Druga metoda = własny recompute + replay starej logiki `.bak`. Output do scratchpad/stdout. ≥2 odpalenia.

---

## 0. Co miałem zwalidować (z promptu)
1. czy werdykt **poprawnie N/A** (flaga OFF) a NIE fałszywe CLEAN;
2. czy **`--flip-end` domyka okno**;
3. czy **kohorta ON ma górną granicę**;
4. zwalidować **„naprawione 20dec97"**;
5. **`DRIVE_SPEED_MULT_BY_TIER<1.0`** w `common.py` = mina re-flipu (klasa K/N).

Wszystkie 5 rozstrzygnięte pomiarem (poniżej).

---

## 1. METODA (oracle, druga niezależna prawda)
- **Method-1 (instrument):** import `tools/drive_speed_overshoot_verdict.py`, wołanie `compute(flip, flip_end)` 2× w 2 osobnych procesach (determinizm). `main()` NIE wołany → zero zapisu do `dispatch_state/`.
- **Method-2 (niezależny recompute):** własny kod w `scratchpad/c07_oracle.py` — surowy odczyt `ziomek_pred_calibration.jsonl` + `courier_tiers.json` + `flags.json`, własny split kohort [<flip] / [flip,flip_end) / [flip,∞) / [≥flip_end], własna mediana bias. NIE dzieli kodu z narzędziem.
- **Replay starej logiki `.bak-pre-5-flipend`:** odtworzony inline (ON=[flip,∞), brak `_flag_on`) na BIEŻĄCYCH danych → pokazuje werdykt, który stara wersja by wypluła.
- **Tripwire-inwarianty:** ten sam zbiór tierów (AFFECTED={gold,std+,std}); kohorta ON ⊂ [flip,flip_end]; cross-check M1≡M2.
- **CAVEAT prawdy:** `delivered_at` = prawda-PRZYCISKOWA (Warsaw-naive), `delivery_pred_last` = UTC-aware. Bias = **proxy-certified**, nie GPS-ground-truth (zgodne z RECON/A4 §7). Werdykt i tak N/A → caveat niewiążący operacyjnie DZIŚ.

---

## 2. WYNIKI POMIARU (RUN1/RUN2, deterministyczne)

### 2a. Flaga + werdykt narzędzia (realny stan)
- `ENABLE_DRIVE_SPEED_TIER_CORRECTION = False` (niezależny odczyt flags.json) ✓
- `compute()` 2× → **verdict = "N/A"** oba razy, `r1==r2` (identyczny dict) ✓ deterministyczny.
- Ścieżka: `drive_speed_overshoot_verdict.py:131-136` — `if not _flag_on(...) → res["verdict"]="N/A"; return` **PRZED** liczeniem CLEAN/ALARM. Data-niezależne: N/A wypada niezależnie od kohorty. ✓
- Nota narzędzia poprawnie steruje: „korekta NIE jest LIVE… NIE wskrzeszać… właściwy lewar = dwell-parytet PLAN_RECHECK_TIER_DWELL".

### 2b. Cross-check M1≡M2 (kohorta, kontrfaktycznie flaga forced-ON in-memory, okno bounded)
| Kohorta | Method-1 (tool) | Method-2 (indep) | match |
|---|---|---|---|
| baseline (<flip) | n=683 bias_med=-4.7 late=17.3% | n=683 bias_med=-4.7 late=17.3% | ✓ |
| ON [flip,flip_end) | n=10 bias_med=-5.8 late=20.0% | n=10 bias_med=-5.8 late=20.0% | ✓ |

**MATCH = True** (n + bias_med obu kohort). Instrument liczy DOKŁADNIE to, co deklaruje.

### 2c. `--flip-end` domyka okno + górna granica kohorty ON
Kontrfaktycznie (flaga forced-ON in-memory, BEZ dotykania flags.json):
- `flip_end` SET → **ON n=10**
- `flip_end` None → **ON n=903** (zachowanie STAREJ wersji)
- => `--flip-end` **kurczy ON 903→10**, wyklucza **893** dostaw po rollbacku (flaga była OFF dla nich). ✓
- Górna granica jest **domyślna** (`FLIP_END_DEFAULT="2026-06-26T17:40:00+00:00"`, argparse default `:180-181`) — bare-invocation TEŻ ma bound; trzeba jawnie `--flip-end ""` by go zdjąć.
- Weryfikacja graniczna: 10 dostaw kohorty bounded mają `delivered_at` ∈ [19:25:22, 19:40:00 Warsaw] = [17:25:22Z,17:40:00Z], od 19:26:52 do 19:39:40 — ostatnia (cid=509, 17:39:40Z) tuż wewnątrz granicy 17:40:00, **nic nie przecieka** poza okno. ✓

### 2d. Dowód że fix `20dec97` realnie zamknął fałszywy CLEAN (replay starej logiki na BIEŻĄCYCH danych)
- STARA logika `.bak-pre-5-flipend` (ON=[flip,∞), brak `_flag_on`) → na obecnych danych werdykt = **CLEAN** („korekta trafna, zostaw").
- Ten CLEAN policzony na **903 dostawach**, z czego **893 (98,9%) to dostawy PRZY FLADZE OFF** (po rollbacku 17:40 UTC). Klasyczny fałszywy CLEAN → przyszła sesja „wskrzesiłaby" świadomie cofniętą, mis-targeted korektę.
- **Kompounding w czasie:** między RUN1→RUN2 (sekundy) ON-unbounded urósł 902→903 (żywy kolektor dopisuje). Stara logika absorbowałaby KAŻDĄ przyszłą dostawę w „ON" w nieskończoność; fix zamraża okno na 10. ✓
- `git show 20dec97`: 1 plik, +45/-5, dodaje `_flag_on` (`:43-49`), `FLIP_END_DEFAULT` (`:36`), bound `[flip,flip_end)` (`:104-108`), N/A-when-OFF (`:131-136`), N/A-when-small+flip_end (`:140-146`), arg `--flip-end` (`:180-181`). Wdrożony plik == wersja po-fixie (przeczytany). ✓

**Werdykt p.1-4: VALIDATED.** Fix prawdziwy, obecny w żywym narzędziu, oracle potwierdza N/A pod realnym OFF, a stara ścieżka udowodnioną fałszywą-CLEAN. Dwie warstwy obrony: (A) flaga-OFF→N/A (pierwotna, data-niezależna — TO trzyma poprawność DZIŚ); (B) flip_end-bound (wtórna, działa gdy flaga ON).

---

## 3. p.5 — MINA RE-FLIPU `DRIVE_SPEED_MULT_BY_TIER<1.0` (źródło, klasa K/N) — NIEZAMKNIĘTA

`common.py:2188-2194`:
```
DRIVE_SPEED_MULT_BY_TIER = {'gold':0.78, 'std+':0.82, 'std':0.82, 'slow':1.0, 'new':1.0}
```
- Bramka: `common.py:2197 speed_mult_for_tier(tier)` → `:2207-2209` `if not flag("ENABLE_DRIVE_SPEED_TIER_CORRECTION", False): return 1.0` else per-tier. **Szczelna** — sprawdziłem WSZYSTKICH konsumentów, żaden nie sięga po dict bezpośrednio z pominięciem bramki:
  - `feasibility_v2.py:811-812,818,953` — `C.speed_mult_for_tier(courier_tier)` (gated).
  - `plan_recheck.py:667` — `_C.speed_mult_for_tier(_tier)` (gated; fallback `:673`→1.0 w except).
  - `route_simulator_v2.py:253` param `drive_speed_mult=1.0`, aplikacja `:408` `(dur_s/60.0)*drive_speed_mult` (mnoży czas jazdy OSRM).
- **Stan DZIŚ: inert** (flaga OFF → wszędzie 1.0, zero zmiany decyzji). Brak żywego wpływu.
- **DLACZEGO MINA:** to **hot-reload** flaga (bez restartu, bez zmiany kodu). Jeden flip ON → 0.78/0.82 natychmiast ściskają nogę jazdy o 18-22% w feasibility + plan_recheck → ETA optymistyczniejsze.
- **Sprzeczność z VALIDOWANYM ustaleniem 29.06** (`ziomek-calibration-2026-06-29`): noga **jazdy ma ~0 błędu** (motion ~OSRM, czerwcowy bias −1.37), a źródło optymizmu to **POŚLIZG ODBIORU**, nie tempo jazdy. Sam komentarz w `plan_recheck.py:653-654` to potwierdza: „drive jest OK — motion ~OSRM, czerwcowy bias −1.37". Re-flip = wstrzyknięcie optymizmu w **złą warstwę** (ściska już-trafną nogę) = dokładnie awaria, którą rollback 26.06 cofnął.
- **Jedyny guard-rail = nota w werdykt-txt** („NIE wskrzeszać"), czyli plik tekstowy — NIE blok kodowy. Wartości siedzą uzbrojone.
- **Klasa: K** (uśpiona/szczątkowa stała kalibracyjna z 26.06 sprzeczna z walidacją 29.06) **+ N** (próg niezgodny z walidowanym ustaleniem). **Severity P3** (latentne, flaga OFF, zero żywego wpływu; luka = brak twardego guarda + sprzeczność z kanonem).

### 3a. Podmina N — zaszyte `FLIP_DEFAULT`/`FLIP_END_DEFAULT` (okno 26.06)
`tools/...verdict.py:31` `FLIP_DEFAULT="2026-06-26T17:25:22+00:00"`, `:36` `FLIP_END_DEFAULT=...17:40:00`. Gdyby ktoś re-flipnął flagę ON w NOWYM czasie i odpalił werdykt **bez** nowych `--flip/--flip-end`, narzędzie zmierzy STARE okno 26.06 (10 dostaw, CLEAN) zamiast nowego flipa → mylący CLEAN utwierdzający „zostaw ON". Kompounduje minę re-flipu. **Severity P3.**

---

## 4. INSTANCJE (plik:linia świeży, 2026-06-30)
| # | plik:linia | co | źródło/objaw | patched? | open? | klasa | sev |
|---|---|---|---|---|---|---|---|
| 1 | `common.py:2188-2194` | `DRIVE_SPEED_MULT_BY_TIER` 0.78/0.82/0.82 <1.0 uzbrojone za hot-flagą | źródło | częściowo (flaga OFF + nota; wartości zostają) | **TAK** | K+N | P3 |
| 2 | `common.py:2197-2209` | `speed_mult_for_tier` bramka — szczelna (1.0 gdy OFF) | źródło (OK) | — | nie | — | — |
| 3 | `feasibility_v2.py:811-812,818,953` · `plan_recheck.py:667,673` · `route_simulator_v2.py:253,408` | konsumenci mnożnika (wszyscy za bramką) | źródło (OK) | — | nie | — | — |
| 4 | `tools/drive_speed_overshoot_verdict.py:131-136` | N/A-gdy-flaga-OFF (fix 20dec97) | objaw→naprawiony | TAK (`20dec97`) | NIE | E (był) | — |
| 5 | `tools/...verdict.py:104-108,180-181` | flip_end bound ON=[flip,flip_end) | objaw→naprawiony | TAK | NIE | — | — |
| 6 | `tools/...verdict.py:31,36` | zaszyte FLIP/FLIP_END defaults = okno 26.06 (mylący CLEAN po nowym re-flipie z bare-args) | objaw | nie | TAK | N | P3 |
| 7 | `dispatch_state/drive_speed_overshoot_verdict.txt` (29.06 07:14) | zamrożony snapshot, brak recurring schedulera (at-187 SPENT) ani TTL | objaw | nie | TAK (A4-H) | H | P3 |
| 8 | A4_instrument_registry.md row 7 | źródło werdyktu mis-podane jako `drive_min_calibration_log_v2.jsonl`/`drive_min_enriched.jsonl`; narzędzie czyta `ziomek_pred_calibration.jsonl` | objaw (artefakt audytu) | nie | TAK | E | P3 |

> Inst.#8: `drive_min_calibration_log_v2.jsonl`/`_enriched` karmią INNY przyrząd (`ENABLE_DRIVE_MIN_CALIBRATION_V2_SHADOW=true`, para C3 z A3 §4) — A4 skonflował dwa drive-przyrządy. Wniosek o świeżości danych w A4 i tak prawdziwy (`ziomek_pred_calibration.jsonl` świeży 16:54), ale debug po złym pliku.

---

## 5. TABELA POKRYCIA
| Element | Zbadane? | Jak |
|---|---|---|
| `drive_speed_overshoot_verdict.py` compute() | TAK | import+2× run, M1≡M2 cross-check |
| `_flag_on` gate (N/A path) | TAK | code-read `:131-136` + run pod realnym OFF |
| `--flip-end` / FLIP_END_DEFAULT | TAK | kontrfaktyczny forced-ON: 903→10, granica 17:40:00Z dokładna |
| Stara logika `.bak-pre-5-flipend` | TAK | replay inline → CLEAN na 903 (893 flag-OFF) |
| commit `20dec97` | TAK | `git show` stat+diff vs wdrożony plik |
| `DRIVE_SPEED_MULT_BY_TIER` + bramka | TAK | grep+read common.py:2180-2209 |
| Konsumenci mnożnika (feas/plan_recheck/route_sim) | TAK | grep całego silnika (poza tools/tests/.bak) |
| Flaga efektywna (3-warstwy) | CZĘŚCIOWO | flags.json=False (hot-reload, nie env-frozen; brak w drop-inach plan-recheck/panel-watcher z A3 §1 — spójne z hot-reload). NIE dumpowałem `systemctl show` per-serwis (DoD: bez restartu; A3 już potwierdził brak tej flagi w drop-inach) |
| TZ bias (L-klasa) | TAK | delivered=Warsaw-naive vs pred=UTC; baseline bias −4.7 min mały+sensowny → TZ poprawny (błąd 2h dałby ±120 min) |
| Para `DRIVE_MIN_CALIBRATION_V2` (main OFF/shadow ON) | NIE | poza zakresem C07 (osobny przyrząd; tylko odnotowane przy A4-mis-attribution #8) |
| Ground-truth GPS dla delivered_at | NIE | DoD/oracle-caveat: proxy-certified; werdykt N/A → niewiążące. GPS-GT producent = `gps_delivery_truth.jsonl` (poza C07) |

## 6. LUKI POKRYCIA (jawnie)
- **Per-serwis `systemctl show -p Environment`** dla tej flagi NIE wykonany (zakaz restartu/ingerencji; A3 §1 już zmierzył drop-iny — flaga nieobecna = czysto flags.json hot-reload, zgodne z `_flag_on` czytającym flags.json). Ryzyko dywergencji cross-proces = niskie (flaga OFF wszędzie).
- **Nie odpaliłem `main()`** (pisze do dispatch_state) → nie zweryfikowałem ścieżki zapisu OUT ani `--notify` (poza DoD; zbędne — `compute()` to cała logika werdyktu).
- **Counterfactual flag-ON** to in-memory monkeypatch `_flag_on` (mój proces), NIE realny flip — zgodne z DoD. Pokazuje logikę kohorty, nie żywy efekt korekty (korekta nigdy nie była mierzona LIVE > 15 min).

---

## 7. DEDUP / framing do rootów
- Inst.#1+#6 (mina re-flipu + stale flip-defaults) → root **K/N: kalibracja sprzeczna z walidacją 29.06, uzbrojona za hot-flagą bez twardego guarda**. NIE zwija się do K1 (brak-jednego-źródła) — to odrębna mina. Powiązanie tematyczne z `ziomek-calibration-2026-06-29` (load>clock, poślizg-odbioru) i `PLAN_RECHECK_TIER_DWELL` (właściwy lewar).
- Inst.#4+#5 → root **E-naprawiony** (11 „naprawionych 29.06" werdyktów z A4 §8) — **TEN konkretny (drive_speed) = VALIDATED oracle, nie tylko deklaracja.** Zdejmuje 1/11 z listy „PLAUSIBLE do oracle".
- Inst.#7 → root **H stale-.txt-bez-TTL** (A4 §8, wspólny z bug4_reseq/objm_peak verdict-txt).
- Inst.#8 → root **E registry-accuracy** (artefakt audytu A4, nie silnik).
