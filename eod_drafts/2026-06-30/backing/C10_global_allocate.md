# C10 — RUNTIME-ORACLE: global_allocate / reassign_global_select (lane C)

**Agent:** C10-global-allocate · **Lane:** C (runtime-oracle, C9/C11) · **Data:** 2026-06-30 ~17:23 UTC · **READ-ONLY** (zero edycji/restartów/flipów; analiza do scratchpad, NIC do dispatch_state).
**Claim do weryfikacji:** rejestr A4 #4d + #10 = „global_allocate VALIDATED; reassign de-pile 5/5 dużych pile-onów rozbitych, over-hide=0".
**Metoda:** odczyt ŚWIEŻYCH jsonl które żywe kolektory już produkują + rekompute prawdy DRUGĄ, niezależną metodą (regroup raw `new_cid`/`proposed_cid` vs stemplowany `g_maxpile`; ground-truth `deliv_spread_km` z feasibility_v2; ręczny invariant survivors+hidden==cand). Skrypt: `scratchpad/c10_oracle.py` (2 odpalenia, identyczne = deterministyczne).

---

## TL;DR — werdykt TRÓJDZIELNY (nie jeden „VALIDATED")

Instrument NIE jest jednolicie „VALIDATED". Rozpada się na 3 niezależne osie prawdy:

| Oś | Werdykt | Dowód 2. metodą |
|---|---|---|
| **De-pile COUNT** (maxpile before→after) | ✅ **validated** | regroup raw `new_cid`/`proposed_cid` per tick (1859 ticków) = **0 mismatch** vs stempel; invarianty survivors+hidden==cand, maxpile≤survivors OK; **7/7** dużych pile-onów (≥3) zredukowanych |
| **Jakość geometryczna worka** („feasibility-validated, 3-4 OK") | 🔴 **void** | ground-truth `deliv_spread_km`: **710/2019 = 35,2%** alokacji multi-drop ma spread**>8 km** (R1!), 267 z nich r6**>40** (ponad twardy cap), 175 spread>12 km. De-pile dziedziczy ślepotę geometryczną |
| **LIVE re-proponowanie** (engine action) | ⚪ **untested** | kod `:419-421` = warning no-op; `PENDING_RESWEEP_LIVE=false`. Tylko shadow + display-overlay (`ENABLE_GLOBAL_ALLOC_WRITE=true`) |

**Sedno (C11):** instrument certyfikuje LICZBĘ rozbitych pile-onów (uczciwie), ale jest GEOMETRYCZNIE ŚLEPY na to, CO produkuje. Werdykt review `reassign_global_select_review.py:100-103` deklaruje „worek kept = rozmiar zwalidowany feasibility (3-4 OK)" — to NIEPRAWDA pod scarcity. „VALIDATED" w rejestrze = count-validated, quality-void. Flip LIVE puszczony na ten werdykt aktywnie przepchnąłby **279** propozycji spread>8 (would_repropose=true) do Telegrama.

---

## 1. STAN NA ŻYWO (freshness + flagi)

| Artefakt | mtime (UTC) | Status |
|---|---|---|
| `dispatch_state/pending_global_resweep.jsonl` (3044 wierszy) | 17:22:36 FRESH | kolektor NEW-order global_allocate, tick 1 min |
| `dispatch_state/reassign_global_select.jsonl` (53 wiersze) | 12:06:43 semi-fresh | event-driven: loguje TYLKO gdy candidates≥2 (passthrough <2 = early-return bez append, `reassignment_global_select.py:328-340`) — semi-fresh BY-DESIGN, nie martwy |
| `dispatch_state/global_alloc.json` | 17:22:38 FRESH | overlay konsoli (NEW) — display-live |
| `dispatch_state/reassign_global_alloc.json` | 17:21:14 FRESH | overlay konsoli (PRZERZUT) — display-live |
| `dispatch_state/reassign_global_select_review_verdict.txt` | **29.06 18:30 STALE** | werdykt one-shot (at-197), NIE odświeżany; brak recurring timera |

**Flagi efektywne (flags.json):** `ENABLE_PENDING_RESWEEP=True` (shadow) · `PENDING_RESWEEP_LIVE=False` · `ENABLE_GLOBAL_ALLOC_WRITE=True` (display overlay) · `ENABLE_REASSIGN_GLOBAL_SELECT=True` (shadow+overlay) · `PENDING_RESWEEP_MARGIN=15.0`. (`PANEL_FLAG_REASSIGN_GLOBAL_SELECT_OVERLAY`, `ENABLE_REASSIGN_QUALITY_GATE`, `REASSIGN_GLOBAL_SELECT_CAND_TTL_SEC` — NIEOBECNE w flags.json → defaulty z kodu/env; klasa D-dryf, oś A3/A5.)

---

## 2. OŚ 1 — DE-PILE COUNT = ✅ VALIDATED (2. metoda potwierdza, instrument NIE kłamie o liczbie)

**Claim:** de-pile rozbija pile-on (jeden kurier do N zleceń → różni kurierzy).
**2. metoda (niezależna od stempla):** dla każdego ticku `pending_global_resweep.jsonl` przegrupowałem SUROWE `new_cid` (po alokacji) i `proposed_cid` (przed) z per-order wierszy i policzyłem max-pile sam, po czym porównałem do stemplowanego `g_maxpile_before/after`.

```
ticks checked: 1859
g_maxpile_BEFORE mismatch (mine vs stamped): 0
g_maxpile_AFTER  mismatch (mine vs stamped): 0
g_maxpile_before dist: {1:1377, 2:329, 3:100, 4:46, 5:5, 6:1, 7:1}   ← realne pile do 7!
g_maxpile_after  dist: {1:1549, 2:267, 3:36, 4:7}
NEW-path: ticks g_maxpile_before>=3 = 153; reduced (after<before) = 137 (89,5%)
```

**Reassign path (`reassign_global_select.jsonl`):** invarianty trzymają twardo:
- `survivors_out + hidden_out == candidates_in` → **OK** (0 naruszeń, 53 ticki).
- `maxpile_after <= survivors_out` → **OK**.
- maxpile_before dist {1:20, 2:26, 3:6, 4:1}; **ticki maxpile_before≥3 = 7; zredukowane = 7/7** (review mówił 5/5 bo --since-scoped okno 8h; pełny plik = 7/7).

**Werdykt osi 1:** ✅ **validated** — proxy-certyfikowany (samospójność instrumentu). Liczenie de-pile jest uczciwe; `global_allocate` (jedno źródło, `pending_global_resweep.py:145`, importowane przez `reassignment_global_select.py:54`) realnie redukuje max-pile. To NIE jest lying-instrument na osi liczby.

---

## 3. OŚ 2 — JAKOŚĆ GEOMETRYCZNA = 🔴 VOID (de-pile dziedziczy ślepotę; seed 152/426 POTWIERDZONY i szerszy)

**Claim review (`reassign_global_select_review.py:100-103`):** „Worki kept = rozmiar zwalidowany feasibility (3-4 na jednego = OK gdy dobry worek)".
**2. metoda = ground-truth geometria:** `new_deliv_spread_km` w jsonl = max-pairwise dystans dostaw w worku, liczony przez `feasibility_v2` (haversine coordów dostaw — geometria, NIE button-truth). Policzyłem ile alokacji `global_allocate` tworzy worki R1-łamiące.

```
allocated rows (new_cid set, no_courier=false): 3044
  z czego new_deliv_spread_km NON-NULL (worek multi-drop): 2019
  z tego spread > 8.0 km (NARUSZENIE R1): 710 = 35,2%
  spread buckets: {0-2:73, 2-5:514, 5-8:722, 8-10:292, 10-12:243, >12:175}
  spread>8 AND r6>40 (ponad twardy cap 35/40): 267/710 ; median r6 (spread>8) = 35,3
  spread>8 AND would_repropose=true (system BY ZADZIAŁAŁ): 279
  distinct order_ids z alokacją spread>8: 277
```

> Seed audytu mówił „152/426 spread>8 PO global_allocate" (okno poranne 30.06). Mój pełny-plik recompute = **35,2% (710/2019)** — TEN SAM stosunek, większa próba. **POTWIERDZONE.**

**🔫 Smoking gun (de-pile COUNT-sukces ⊕ geometryczna klęska w jednym wierszu):**
- `oid=484250` `new_cid=515` **spread=18,0 km r6=73,4 min** reason=`rozjazd_kierunkow` pool_feasible=0 **`g_maxpile_before=4 → g_maxpile_after=2`**. De-pile „rozbił" 4-pile do 2 (sukces licznikowy!), ale przeniósł zlecenie na worek 18 km spread / 73 min R6. Powód literalnie `rozjazd_kierunkow` — instrument myśli, że NAPRAWIA rozjazd, a przenosi na GORSZY geometrycznie cel.
- `oid=484020` `new_cid=447` (Dawid — case Adriana) **spread=24,3 km r6=53,3** reason=`lepszy_kurier` pool_feasible=0.
- `oid=483508` `new_cid=413` spread=17,9 r6=62,7 `g_maxpile 3→2`.

**Mechanizm (dlaczego):** `global_allocate` woła `DP.assess_order` (`pending_global_resweep.py:139`), którego klucz selekcji `objm_lexr6.lex_qual` (świeży grep `:29`) jest **czysto czasowy** (post_shift, r6_breach, late_pickup, new_pickup_late — ZERO osi rozjazdu). `deliv_spread_km` jest serializowany na kandydacie ale ŻADEN klucz selekcji go nie czyta. Pod scarcity (pool_feasible=0 w **20,2%** wierszy, 43-45% na peaku per seed) ścieżka best-effort sentinel zwraca kuriera o najbliższym ODBIORZE niezależnie od rozjazdu DOSTAW → 447/centrum wygrywa mimo dostaw zachód+wschód. De-pile (reassign + NEW) używa TEGO SAMEGO `global_allocate` → dziedziczy ślepotę 1:1.

**Werdykt osi 2:** 🔴 **void** — ground-truth. Certyfikacja „feasibility-validated worek" jest FAŁSZYWA pod scarcity. Instrument mierzy LICZBĘ, deklaruje JAKOŚĆ. To dokładnie wzorzec C11 „artefakt mierzy proxy" — review pyta „czy max-pile spadł?", nigdy „czy worek po de-pile jest geometrycznie zdrowy?".

---

## 4. OŚ 3 — LIVE = ⚪ UNTESTED (ścieżka engine niewpięta; tylko display)

**Kod (świeży, `pending_global_resweep.py`):**
```
419   live_acted = 0
420   if C.flag(FLAG_LIVE, False):
421       _log.warning("PENDING_RESWEEP_LIVE=ON ale ścieżka live niewpięta — shadow-only (patrz docstring)")
```
`PENDING_RESWEEP_LIVE=false` → nawet gdyby ON, to **warning no-op** (re-proponowanie / edit msg TG / update pending_proposals NIE zaimplementowane; docstring `:19-22`, `:416-418`). 

**Co JEST live:** `ENABLE_GLOBAL_ALLOC_WRITE=true` → resweep nadpisuje `global_alloc.json` (`:401-414`, serializacja przez `shadow_dispatcher._serialize_result`) → `feed.py` overlay pokazuje podział na tablicy konsoli. Analogicznie reassign → `reassign_global_alloc.json` (`reassignment_global_select.py:152-165`). **Oba DISPLAY-LIVE, ENGINE-SHADOW.** Silnik nadal proponuje per-order greedy do Telegrama; overlay to tylko warstwa 10 (render).

**Werdykt osi 3:** ⚪ **untested** — ground-truth (code read). Pomiar shadow + overlay konsoli = validated jako MIERNIK; akcja silnika = niezaimplementowana = nie ma czego walidować. Zgodne z seed: „Faza C global-alloc = display-only".

---

## 5. OVER-HIDE GUARD — werdykt „=0" stale + window-scoped; guard koarsy (E/H)

**Claim review:** `overhide_suspect_ticks: 0`.
**2. metoda (pełny plik, `reassign_global_select_review.py:71` recepta):** ticki candidates_in≥2 ∧ survivors_out==0:
```
OVER-HIDE suspect: 7 ticków
  29.06 11:55:47 cand3 surv0 hid3 dropped=[quality_failed_vs_global, stays_with_holder, stays_with_holder]
  29.06 11:58:47 cand3 surv0 hid3 ...
  29.06 18:31:31 / 18:34:33 cand2 surv0 ...
  30.06 10:22:46 / 11:05:43 / 11:17:53 cand2 surv0 dropped=[stays_with_holder, stays_with_holder]
```
**Reconcyliacja (czemu review widział 0):** at-197 (jednorazowy, 18:30) odpalony z `--since` post-fix (bug singleton-hide naprawiony 29.06 rano, docstring) → odciął 11:55/11:58; ticki 18:31/18:34 jeszcze nie istniały o 18:30; ticki 30.06 powstały PO werdykcie. Więc „=0" było PRAWDĄ dla swojego okna — **NIE kłamstwo**, ale **stale** (brak recurring review → 6 ticków po-werdyktowych NIGDY nieaudytowanych).

**Czy 7 over-hide to bug?** NIE — wszystkie dropped-reasons = `stays_with_holder` (global best = obecny holder → słusznie nie przerzucaj) lub `quality_failed_vs_global` (gate jakości odrzucił → słusznie ukryj). To POPRAWNE chowanie. ALE guard `survivors==0` **konflatuje** „słusznie ukryto wszystkie" z „buggy ukryto genuine" — daje FAŁSZYWE uspokojenie (nie odróżnia benign od bug). Lepszy guard: `survivors==0 ∧ ∃ dropped.why ∉ {stays_with_holder, quality_failed_vs_global}`.

**Werdykt:** guard nie dał false-negative na REALNYM bugu (7 = benign, zweryfikowane po dropped-reasons), ale jest koarsy + werdykt stale + 30.06 niezaudytowane → klasa E (stale verdict bez TTL) + H (one-shot zamiast recurring).

---

## 6. SENTINEL — `no_courier=0/3044`: de-pile NIGDY nie mówi KOORD (M, dedup K5)

```
no_courier=true rows (KOORD): 0 / 3044
```
`global_allocate` gałąź `no_courier` (`:200-210`) wymaga `best is None`, ale best-effort sentinel ZAWSZE zwraca kuriera → pod pool_feasible=0 i tak alokuje (stąd r6=53-73 worki). Per change-protocol „always-propose/sentinel best-effort = OK nie bug" — INTENCJONALNE. Ale konsekwencja dla de-pile: **nigdy nie eskaluje do KOORD pod scarcity** — zawsze upycha na kogoś, choćby na 73-min/24-km worek. To MOST K5 (sentinel jako dane → geometria-ślepy pile-on). Nie bug instrumentu, lecz właściwość którą flip LIVE musi uwzględnić.

---

## 7. CO TEN INSTRUMENT FLIPUJE (gates-which-flip)

1. `reassign_global_select_review` → bramkuje zaufanie do overlay przerzutu + utrzymanie `ENABLE_REASSIGN_GLOBAL_SELECT=on`. **Oś COUNT validated → overlay liczbowo OK.**
2. `pending_global_resweep` → bramkuje decyzję Adriana A (re-ranker) vs B (fix u źródła) + ewentualny flip `PENDING_RESWEEP_LIVE`. **⛔ Werdykt osi 2 (void) MUSI zablokować flip LIVE:** puszczenie de-pile na żywo bez członu geometrii w `lex_qual` (P0-A) przepchnęłoby 279 propozycji spread>8 (do r6=73, spread=24) do Telegrama. Seed/MEMORY: P0-A+P0-B MUSZĄ iść RAZEM, sam de-pile = no-op/szkoda.
3. `ENABLE_GLOBAL_ALLOC_WRITE` overlay → display-live już teraz; pokazuje koordynatorowi te same geometrycznie-ślepe alokacje (overlay nie filtruje spread).

---

## 8. TABELA POKRYCIA

| Element | Zbadane? | Metoda | Werdykt |
|---|---|---|---|
| `pending_global_resweep.jsonl` (3044 w.) | ✅ pełny plik | regroup raw + ground-truth spread | count validated / geometry void |
| `reassign_global_select.jsonl` (53 w.) | ✅ pełny plik | invarianty + maxpile dist | count validated; 7/7 big reduced |
| g_maxpile honesty (NEW path) | ✅ 1859 ticków | 2. metoda regroup new_cid/proposed_cid | 0 mismatch — NIE kłamie |
| geometria spread>8 PO global_allocate | ✅ 2019 multi-drop | ground-truth deliv_spread_km | 35,2% R1-violation (void cert) |
| r6>cap pod scarcity | ✅ | join spread>8 × r6 | 267/710 r6>40 |
| LIVE path wpięcie | ✅ code read :419-421 | ground-truth (kod) | untested (no-op) |
| over-hide guard | ✅ pełny plik + reconcile at-197 | recepta :71 + dropped-reasons | stale+koarsy; 7 benign |
| overlay display-live | ✅ mtime + serializer | ls + code :401-414 | display-live confirmed |
| no_courier sentinel | ✅ | count | 0/3044 (M, intended) |
| `global_allocate` engine (assess_order) wnętrze | ⚠️ NIE re-trace’owane | poza lane C (silnik=lane B); cytuję objm_lexr6:29 lex_qual ze świeżego grep | dedup→P0-A |
| brute-force permutacji OSRM | ⚠️ NIE — nieadekwatne | spread to geometria coordów, nie kolejność trasy; OSRM-permut nieistotny dla tej osi | n/d |
| reassign_global_select_review.py RUN | ⛔ NIE uruchomiony | pisze do dispatch_state (OUT_VERDICT) — poza DoD | recompute zamiast |

**Luki jawne:**
- NIE re-trace’owałem wnętrza `assess_order`/`lex_qual` (to lane B silnika); cytuję `objm_lexr6.py:29` jako źródło ślepoty ze świeżego grep, ale dowód geometryczny mam z OUTPUTU instrumentu (spread), nie z kroku po kroku silnika.
- NIE odpaliłem `reassign_global_select_review.py` (pisze do dispatch_state) — rekomputowałem jego logikę ręcznie.
- `reassign_global_select.jsonl` ma 53 wiersze (event-driven) — mała próba dla osi reassign; oś COUNT/invarianty pewne, ale dystrybucja dużych pile-onów (7) to wąskie okno 29-30.06.
- delivered_at/picked_up_at NIE użyte (spread=geometria coordów = ground-truth niezależny od button-truth — caveat fundamentu nie dotyczy tej osi).

---

## 9. DEDUP / ZWIJANIE DO ROOTÓW (framing Fazy A6 + unified audit)

- **Geometryczna ślepota de-pile** → zwija do **P0-A** (`lex_qual` bez osi geometrii) + **K1** (brak jednego źródła) + **K5** (sentinele/best-effort jako dane → geometria-ślepy pile-on pod scarcity). NIE nowy root — POTWIERDZENIE runtime-oracle istniejących P0-A/K5.
- **De-pile bez global de-konflikcji dla NOWEGO zlecenia** → **P0-B** / **K6** (greedy bez global de-pile). LIVE niewpięty = ta asymetria.
- **Review certyfikuje count nie quality / verdict stale / over-hide koarsy** → klasa **E** (lying/stale instruments) — ta sama rodzina co 11 kłamstw przyrządów z audytu 28.06.
- **LIVE display-only vs engine-shadow** → klasa **H** (luka cyklu: pomiar bez akcji) + **F** (overlay=display, nie decyzja silnika).
- **no_courier=0 sentinel** → klasa **M**, dedup K5.

**Konkluzja:** „VALIDATED" w rejestrze A4 jest PRAWDZIWE tylko dla osi COUNT. Jako pełen werdykt instrumentu = **MISLEADING**: count-validated, **geometry-void**, **live-untested**. Każdy flip oparty na tym „VALIDATED" musi przejść człon geometrii w `lex_qual` (P0-A) ZANIM de-pile pójdzie LIVE.
