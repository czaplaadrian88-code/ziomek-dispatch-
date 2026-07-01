# FAZA E refuter — equal-treatment-vs-discriminate-position (lens B)
VERDICT: PLAUSIBLE | is_source=TRUE | is_open=TRUE (LATENT/documented, NOT active P1 harm)

## Metoda 2 (niezależna od grepu linii audytu): live jsonl + efektywny stan flag + osiągalność

### Stan flag (flags.json /root/.openclaw/workspace/scripts/flags.json, live)
- ENABLE_EQUAL_TREATMENT_BUCKET=True ; ENABLE_NO_GPS_EQUAL_TREATMENT=True ; ENABLE_PRE_SHIFT_EQUAL_NO_PENALTY=True ; ENABLE_PRE_SHIFT_DEPARTURE_CLAMP=True
- ENABLE_AUTO_ASSIGN=False  => executor out-of-engine INERT
- twin-parity L1 TERAZ = ZERO (oba bucket-twin flagi aligned ON)

### HARM = REFUTED (in-engine FAR-veto benign/beneficial)
- shadow_decisions.jsonl 27-30.06 (956 dec): v325_pre_shift_far_veto_kept = 18 rekordów, WSZYSTKIE verdict=PROPOSE (0 KOORD/stranded).
  - 12× far-pre-shift w /alternatives = poprawnie zdemotowany pod lepszego REALNEGO kuriera (beneficial: unika 40-60min czekania)
  - 6× far-pre-shift w /best (pool_feas 1-2) = jedyna opcja, MIMO -1000 proponowany (brak harmu, zlecenie obsłużone)
- Docstring autora dispatch_pipeline.py:2419: "Zdjęcie FAR-veta posłałoby klienta na 40-60 min czekania → harm (replay 29.06 to wykrył)" => consolidation_target opcja A (usuń -1000) jest NET-SZKODLIWA; opcja B (udokumentuj wyjątek) JUŻ zrobiona.
- penalty_suppressed: 66 linii/104 wystąpień (-1.27..-51.16) ZEROWANE live => §7-T4 "pre-shift -20 kara wciąż w kodzie" jest RUNTIME-SUPPRESSED/stale; jedyna rezydualna dyskryminacja = świadomy load-aware -1000.

### OUT-OF-ENGINE bramki = INERT
- tools/reassignment_forward_shadow.py:19,23 — READ-ONLY, OSOBNY PROCES, flag ENABLE_REASSIGNMENT_FORWARD_SHADOW default OFF, "ZERO ryzyka dla żywego dispatchu", jedyny zapis = jsonl.
- auto_assign_gate.py:160 G7 ("pozycja musi być informed, nigdy blind/center") liczona na każdej decyzji (l.11) ALE executor (auto_assign_executor.py) gated ENABLE_AUTO_ASSIGN=False => would_auto=0/d, zero live assignment.
- _demote_blind_empty: equal ON => _is_demotable_blind_empty zwraca False dla no_gps I pre_shift (dispatch_pipeline.py:2467); 0 demote-eventów no_gps/pre_shift w ledger. Demotowane tylko 'none' (poza grafikiem) = reguła kardynalna.

### OPENNESS przeżywa (LATENT, udokumentowane)
- §4 "No-GPS = ZAWSZE równo / NIGDY gorszy score/feasibility/ranking/TRASA" vs FAR-veto -1000 = realne napięcie TEKSTOWE, ale §7-T4 (KANON:151) jawnie listuje to jako "NAPIĘCIE STRUKTURALNE (pilnować)" + §4 klauzula wyjątku + docstring 2419 => konflikt ZNANY/UDOKUMENTOWANY, nie cichy.
- L1 (KANON:166) dual-flag mina = realna kruchość (1 flip od dryfu), ale TERAZ aligned => twin-divergence=0.
- Konsolidacja (jedna oś-pozycji _selection_bucket + ortogonalna oś-obciążenia + jedna bramka równości) genuinie NIE zrobiona = realny coherence-debt source-level.

## Wniosek
Root poprawnie wskazuje realną, source-level lukę koherencji/konsolidacji (NIE render-patch) => is_source=true, is_open=true jako LATENT debt. ALE PRZESZACOWUJE: aktywny harm REFUTED, out-of-engine inert (flagi OFF/shadow), jedna z 2 proponowanych napraw (usuń veto) jest net-szkodliwa, a "self-contradiction" jest już udokumentowana w §7-T4 (nie cicha) => D-class lying/silent-inversion przeszacowane. Niska pilność, NIE aktywne P1.
