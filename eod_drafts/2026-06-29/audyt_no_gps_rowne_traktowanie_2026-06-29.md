# AUDYT: równe traktowanie kurierów bez GPS — wszystkie warstwy (2026-06-29)

Geneza: Adrian — duch w konsoli proponuje przerzut z Piotra Zawadzkiego (470) na Bartka Ołdzieja (123),
mimo że Piotr ma ułożoną trasę i po zabraniu 2 zleceń będzie pusty = pusty swap.
Szersze: „naprawiane 3 razy, a dalej kurierzy bez GPS są gorzej liczeni."

## Timeline 3 napraw (flags.json backupy + git)
- 22.06  `ENABLE_NO_GPS_EQUAL_TREATMENT=ON`  (commit 9db4570/7d9ca0f) — no_gps wyłączony z demote
- 24.06  `ENABLE_EQUAL_TREATMENT_BUCKET=ON`   (commit 4a556e4/d7d7655) — no_gps+pre_shift po score w bucketach selekcji
- ~08.06 `ENABLE_COURIER_LAST_KNOWN_POS=ON` + F4 interp/pickup-proxy — no_gps dostaje ostatnią znaną pozycję
- 25.06  retire B3 no_gps uncertainty penalty (7516049) — wyparte przez powyższe
- 28.06  commit 42ca8ae — KOLEJNA „stale inline _bucket" → _selection_bucket (PLN/best_effort arm)
- shadow_decisions.jsonl = 06-27..06-29 → CAŁY log jest PO obu flipach (stan żywy po naprawach)

## MAPA mechanizmów różnicujących pozycję (LIVE = wpływa na realny przydział)

### A. Surowy score (scoring/pipeline)
1. F1.7 no_gps neutralizacja — dispatch_pipeline.py:5719-5734.
   no_gps → km_to_pickup = ŚREDNIA floty (fallback 5.0), travel_min = max(15, prep).
   = NEUTRALNE. ✅ równo (no_gps nie jest karany w surowym score).
2. v325_pre_shift_soft_penalty — feasibility_v2.py:760-765 (stała C.V325_PRE_SHIFT_SOFT_PENALTY).
   pre_shift z pickupem 0<too_early<=HARD_REJECT_MIN → SOFT kara w score (zaobserwowane -15,77).
   ⚠ NIE objęte flagami równego traktowania (te ruszają bucket/demote, nie surowy score).
   To kara „przed zmianą", nie „bez GPS" — osobna decyzja czy znosić.
3. scoring.py — BRAK bezpośredniego terminu na pos_source (potwierdzone grep). DIST_DECAY po km (a km neutralne dla no_gps). ✅

### B. Selekcja / ranking / bucket
4. _selection_bucket — dispatch_pipeline.py:2411. FLAG-AWARE: informed=0; gdy EQUAL_TREATMENT_BUCKET ON → no_gps/pre_shift=0; 'none'=2.
   Wpięte w żywe klucze: _late_pickup_score_first_key (546), _best_effort_sort_key (583),
   objm_lexr6 bucket_fn (1378), resweep (1086/1119). ✅ równo (obie flagi ON).
5. _is_demotable_blind_empty / _demote_blind_empty — 2426/2459. no_gps wyłączony (NO_GPS flag),
   pre_shift wyłączony (BUCKET flag), 'none' demotowane. ✅ równo.
6. ⚠ _best_effort_fastest_pickup_key — dispatch_pipeline.py:613-617. HARDCODED bucket informed0/blind2/pre_shift2,
   NIE woła _selection_bucket, NIE czyta flagi. Docstring: „SHADOW/LOG-ONLY do walidacji" → NIE żywe DZIŚ,
   ale to bliźniak-mina (gdyby go awansowano = wraca dyskryminacja). Klasa jak 42ca8ae.

### C. Czas dojazdu (ETA)
7. drive_min_calibration OFFSET_TABLE — no_gps +6,5 min, pre_shift +15,3 min.
   Gated ENABLE_DRIVE_MIN_CALIBRATION_V2 = MAIN OFF (tylko shadow). Komentarz: „NIE FLIPOWAC MAIN=true" (premisa=artefakt).
   = NIE żywe. ✅ (ale gdyby ktoś flipnął MAIN → bezpośrednia kara czasu no_gps/pre_shift).

### D. Feasibility (HARD bramki)
8. pickup_too_far — feasibility_v2.py:652. Geometria; dla no_gps km neutralne (fleet_avg) → zwykle OK; ryzyko gdy synthetic daleko.
9. v325_NO_ACTIVE_SHIFT — :722. shift_end=None → HARD REJECT. To grafik, nie GPS, ALE FAIL12_STOREPOS edge (:713-719)
   blokuje nawet pos_source=gps-ze-store gdy brak shift_end (Z-06). Kill-switch ENABLE_FAIL12_STOREPOS_STRICT.
10. v325_PRE_SHIFT_TOO_EARLY — :751-756. HARD REJECT gdy pickup > HARD_REJECT_MIN przed startem zmiany.
11. ENABLE_PRE_SHIFT_DEPARTURE_CLAMP — :794 (ON). Clamp wyjazdu dla pre_shift/no_gps.

### E. Autonomia (auto-assign)
12. auto_assign_gate G7 pos_not_informed — auto_assign_gate.py:137-139. BLOKUJE no_gps/pre_shift/none/pin przed AUTO.
    `pos_from_store` też blokuje (Z-06). ENABLE_AUTO_ASSIGN=False → DZIŚ tylko telemetria.
    ⚠ LATENTNE: po włączeniu autonomii = wprost sprzeczne z „równo jak z GPS".
13. auto_assign_gate G11 score_distrust_ceiling (>90) — score-based, NIE pos-based. (poza tematem GPS)

### F. Przerzuty / duch w konsoli  ← DOMINUJĄCY ŻYWY PROBLEM
14. reassignment_forward_shadow.py:
    - _SYNTH_POS = {none, pin, pre_shift, ""} (l.64) — własna definicja, pre_shift = „fikcja".
    - ramię ratunku: a_late = (a_cand is None) (l.239) — gdy holder wypadnie z hipotetycznej puli →
      AUTOMATYCZNIE „spóźni się" → fabrykuje „ratunek" → quality_reassign=True.
    - NIE zsynchronizowane z równym traktowaniem silnika. Kurier bez GPS / pre_shift / już jadący
      wypada z puli → fałszywy duch „wyrwij mu zlecenia".
15. feed.py (konsola) — pokazuje quality_reassign=True jako „🔁 Propozycje PRZERZUTU" (TTL 7 min).
    BRAK filtra pewnej pozycji (Telegram ma _pos_trusted, konsola nie) → duchy bez realnego GPS holdera widoczne.

## POMIAR WPŁYWU (żywe logi)
- TOR ① (przerzuty): 108 quality_reassign w oknie 23-29.06; **64 (59%) to ratunek na a_in_pool=False**
  (holder wyrzucony z puli, nie zmierzone spóźnienie). Holderzy: 409×18, 370×15, **470(Piotr)×12**, 492×9, 509×5, 471×4.
  → To bezpośrednia przyczyna tego co Adrian widzi. EWIDENTNIE ŻYWE I BŁĘDNE.
- TOR główny (selekcja): 481 PROPOSE (06-27..29, PO flipach). best=synthetic-pos 82, best=informed 399.
  23 decyzje gdzie synthetic-pos alt miał WYŻSZY surowy SCORE niż wybrany informed best.
  ⚠ NIEKONKLUZYWNE: selektor = objm_lexr6 (R6-first), NIE score-first → niższy score może być lepszym R6.
  Synthetic pozycja MOŻE dawać gorszą predykcję R6/trasy (kara POŚREDNIA) → wymaga objm-aware replay,
  bo istniejący nogps_preshift_bucket_replay.py mierzy LEGACY klucz (bucket-penalized), nie żywy objm.

## WNIOSKI
1. 3 naprawy (22.06 no_gps-equal, 24.06 bucket-equal, last-known-pos) ZADZIAŁAŁY w GŁÓWNEJ selekcji:
   bucket+demote zneutralizowane, surowy score no_gps neutralny (F1.7). W żywym przypadku Piotra
   przy PRZYDZIALE różnica 90,88 vs 98,38 = wyłącznie TIER prędkości (gold vs std), NIE GPS.
2. Powtarzalność („dalej źle") = NIEKOMPLETNA mapa: logika bucketu/pozycji jest ZDUPLIKOWANA w wielu
   kluczach/narzędziach. Każda naprawa unifikuje jedną kopię na _selection_bucket (ostatnia 28.06 42ca8ae),
   ale POMINIĘTE/NOWE kopie (shadow key #6, forward-shadow #14, auto-gate #12) trzymają dyskryminację żywą.
3. DOMINUJĄCY, ŻYWY, WIDOCZNY problem = TOR ① (przerzuty/duch, #14-15): 59% propozycji jakości to fałszywe
   ratunki ripujące zlecenia kurierom bez GPS/pre_shift. To NIGDY nie zostało zrównane.
4. LATENTNE (nie gryzie dziś, ugryzie przy zmianie flag): #12 auto-gate (po włączeniu autonomii),
   #6 shadow key (po awansie), #7 drive-calib (po MAIN flip).
5. OSOBNA decyzja (nie „bez GPS", a „przed zmianą"): #2 v325_pre_shift_soft_penalty, #10 too_early,
   #11 clamp — czy pre_shift ma być w 100% równy aktywnym (ryzyko: przydział komuś kto jeszcze nie pracuje).

## REKOMENDOWANY ZAKRES (do decyzji Adriana, każdy = protokół)
- P0: TOR ① — zrównać reassignment_forward_shadow z równym traktowaniem (holder w grafiku bez GPS ≠ auto-late;
  ratunek nie odpala na samym a_cand=None) + filtr pewnej pozycji w feed.py konsoli. Kasuje 59% fałszywych duchów.
- P1: unifikacja WSZYSTKICH inline-bucketów na _selection_bucket (#6 shadow key) + auto-gate G7 (#12) —
  żeby 4. naprawa była OSTATNIA (jedno źródło prawdy, checker w teście że nikt nie hardkoduje bucketu).
- P2 (pomiar-first): objm-aware replay → czy synthetic pozycja daje POŚREDNIĄ karę R6; dopiero potem decyzja.
- DECYZJA: pre_shift (#2/#10/#11) — równać czy zostawić (świadoma kara „przed zmianą").

## Czego NIE ruszać bez świadomej decyzji
- drive_min_calibration MAIN (artefakt, udowodnione 05.06) — zostaje OFF.
- v325_NO_ACTIVE_SHIFT (#9) — to bezpiecznik grafiku (#471036), nie dyskryminacja GPS.
