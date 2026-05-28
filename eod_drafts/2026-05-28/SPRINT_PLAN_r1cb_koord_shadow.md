# Sprint Plan — R1 progressive + V319H guard + difficult-case KOORD redirect (SHADOW)

**Data:** 2026-05-28 wieczór (~20:55 Warsaw)
**Trigger:** Adrian zgłosił 2 tragedygeometryczne propozycje dziś:
- #476749 Kebab Król → Mieszka I 8B (Adrian Cit, sequence Kaczor→Mieszka→Antoniuk = "Z")
- #476777 Rukola Sienkiewicza → Kraszewskiego 45b (Paweł SC, kontynuacja z Pizza Dealer→Sikorskiego, cosine -0.991)

**Diagnoza:** sprinty BUG A-E z 26-28.05 są shadow lub nie odpalają na te case'y. Replay 7d (1170 decyzji) pokazał że R1+CB (bez B) łapie oba + 17 historycznych improvements, przy 2 maybe-regresjach (10% noise).

**Decyzja Adriana 2026-05-28 ~20:50:**
1. Implementuj R1+CB shadow dziś
2. KOORD redirect dla difficult cases (gdy max score < -30) + zapisywanie do dedykowanego logu na uczenie

**Owner:** SELF + częściowo AIDER (per ZIOMEK_AI_ROUTING)

---

## 0. Replay summary (7d, 21-28.05, 1170 decyzji)

| Metryka | Wartość |
|---|---|
| Real changes z R1+CB (no FIX-B) | 43 |
| **IMPROVEMENT (cos<-0.3 w OLD)** | **19 (44%)** |
| NEUTRAL_OTHER (delta R1=0,CB=0 = replay V3.16 artefakty) | 10 |
| NEUTRAL_CLOSE (delta=0, scores close) | 11 |
| REGRESSION_FAR | 3 (2 R1-driven, 1 artefakt) |
| Realne R1+CB-driven = 19 wins vs 2 maybe-regresje | **ratio 9.5:1** |

### 2 maybe-regresje (do KOORD-redirect mitigation)

| # | Order | OLD cos | OLD pos | NEW pos | NEW drive |
|---|---|---|---|---|---|
| 1 | #476327 Sweet Fit→Brukowa | -0.855 | pre_shift Tomasz Ch | pre_shift Gabriel | 26.2 min |
| 2 | #476328 Pablos kebab→PRODUKCYJNA 92 | -0.996 | pre_shift Tomasz Ch | pre_shift Jakub OL | 25.6 min |

**Wzorzec:** OLD ma realnie złą geometrię (cos<-0.85), ALE pool alternatyw też zły (wszyscy pre_shift). R1+CB karze poprawnie OLD ale forsuje dalekiego kuriera. **Powinno iść do KOORD-redirect.**

---

## 1. 3 zmiany w Sprint

### FIX-R1 — progresywny clip R1 corridor vs cosine

```python
# dispatch_pipeline.py — helper
def _compute_r1_progressive_delta(cosine, existing_bonus):
    """Zwraca delta dla bonus_r1_corridor by zastosować progresywną karę.
    
    Mocniejsza kara TYLKO gdy cosine < -0.3 (drops nie-aligned).
    NIGDY nie lightening — zwraca delta ≤ 0.
    """
    if cosine is None: return 0.0
    if not isinstance(cosine, (int, float)): return 0.0
    if cosine < -0.7:    new_val = -100.0
    elif cosine < -0.5:  new_val = -60.0
    elif cosine < -0.3:  new_val = -45.0
    else: return 0.0  # cos ≥ -0.3 → zachowaj istniejące
    return min(new_val - (existing_bonus or 0.0), 0.0)
```

Empiryczne dane (7d, n=51 cases z cos<-0.3):
- cos<-0.7 (n=14): istniejące clip -40, nowe -100 → delta -60
- cos -0.7..-0.5 (n=7): istniejące -40, nowe -60 → delta -20
- cos -0.5..-0.3 (n=15): istniejące -35, nowe -45 → delta -10
- cos -0.3..0 (n=15): zachowane

### FIX-CB — V319H continuation bonus guard

```python
def _compute_v319h_guard_delta(cosine, continuation_bonus):
    """Zwraca delta dla v319h_bug2_continuation_bonus by zerować gdy drops nie-aligned.
    
    v319h_bug2_continuation_bonus = +30 za "kontynuację fali" maskuje
    karę kierunku gdy cosine < -0.3. Guard: gdy drops się rozjeżdżają,
    continuation_bonus nie ma uzasadnienia → zeruj.
    """
    if cosine is None: return 0.0
    if not isinstance(cosine, (int, float)): return 0.0
    if not isinstance(continuation_bonus, (int, float)): return 0.0
    if continuation_bonus <= 0: return 0.0
    if cosine < -0.3: return -continuation_bonus
    return 0.0
```

### NOVEL — Difficult-case KOORD redirect

```python
# Po obliczeniu wszystkich scores R1+CB applied:
# Gdy NAJLEPSZY z poolu ma score < DIFFICULT_CASE_SCORE_FLOOR (= -30 startowo),
# → verdict=KOORD + log do difficult_case_log.jsonl

if (ENABLE_DIFFICULT_CASE_KOORD_REDIRECT and
    feasible_count >= 1 and
    max_score_post_fixes < DIFFICULT_CASE_SCORE_FLOOR):
    verdict = "KOORD"
    reason = f"difficult_geometry_max_score_{max_score_post_fixes:.1f}"
    decision["difficult_case_redirect"] = {
        "max_score": max_score_post_fixes,
        "floor": DIFFICULT_CASE_SCORE_FLOOR,
        "best_candidate_id": best.get('courier_id'),
        "best_cosine": best.get('r1_avg_pairwise_cosine'),
        "best_max_bag_min": best.get('max_bag_time_min'),
        "n_candidates_above_floor": 0,
    }
    # Log do dedykowanego pliku dla learning
    _append_difficult_case_log({
        "ts": ts, "order_id": order_id, "restaurant": restaurant,
        "delivery_address": delivery_address,
        "candidates": [serialized_summary for c in cands],
        "fix_deltas": {"r1": ..., "cb": ...},
        "operator_decision": None,  # filled async by reconciliation
    })
```

Path: `/root/.openclaw/workspace/scripts/logs/difficult_case_log.jsonl`

---

## 2. Konfiguracja flag (common.py)

```python
# === R1 progressive clip + V319H guard + difficult case KOORD redirect (2026-05-28) ===
# Shadow-first defaults OFF; flip via flags.json hot-reload po 2-3d verify.
ENABLE_R1_PROGRESSIVE_CLIP = _os.environ.get(
    "ENABLE_R1_PROGRESSIVE_CLIP", "0") == "1"
ENABLE_V319H_CONTINUATION_GUARD = _os.environ.get(
    "ENABLE_V319H_CONTINUATION_GUARD", "0") == "1"
ENABLE_DIFFICULT_CASE_KOORD_REDIRECT = _os.environ.get(
    "ENABLE_DIFFICULT_CASE_KOORD_REDIRECT", "0") == "1"

# R1 progressive thresholds — empirycznie kalibrowane z 7d replay
R1_PROGRESSIVE_CRITICAL_COS = -0.7    # cos < -0.7 → -100
R1_PROGRESSIVE_HEAVY_COS    = -0.5    # cos < -0.5 → -60
R1_PROGRESSIVE_MEDIUM_COS   = -0.3    # cos < -0.3 → -45
R1_PROGRESSIVE_CRITICAL_VAL = float(_os.environ.get(
    "R1_PROGRESSIVE_CRITICAL_VAL", "-100.0"))
R1_PROGRESSIVE_HEAVY_VAL    = float(_os.environ.get(
    "R1_PROGRESSIVE_HEAVY_VAL", "-60.0"))
R1_PROGRESSIVE_MEDIUM_VAL   = float(_os.environ.get(
    "R1_PROGRESSIVE_MEDIUM_VAL", "-45.0"))

V319H_GUARD_COSINE_THRESHOLD = float(_os.environ.get(
    "V319H_GUARD_COSINE_THRESHOLD", "-0.3"))

DIFFICULT_CASE_SCORE_FLOOR = float(_os.environ.get(
    "DIFFICULT_CASE_SCORE_FLOOR", "-30.0"))
```

---

## 3. Plan implementacji (4 etapy)

### Etap 1 (DZIŚ, ~45 min)
- [x] Sprint plan (ten plik)
- [ ] common.py: 5 flag + 7 stałych (kroku 0)
- [ ] dispatch_pipeline.py: 3 helpery + shadow application + KOORD redirect (Aider deepseek-coder, >50 LOC)
- [ ] shadow_dispatcher.py: 3 nowe pola w serializerach LOC A+B
- [ ] tests: 12 testów
- [ ] py_compile + import check
- [ ] backupy .bak-pre-r1cb-shadow-2026-05-28
- [ ] commit + tag `r1-cb-koord-shadow-impl-2026-05-28`
- [ ] restart dispatch-shadow (NIE telegram)
- [ ] 5 min smoke + tail shadow_decisions.jsonl

### Etap 2 (29-30.05, 24-48h verify)
- Codzienne rano replay z fresh shadow data
- Porównanie symulacji vs rzeczywiste delty w `shadow_decisions.jsonl`
- Liczba difficult_case_log entries — pierwsza analiza
- Verify że nasze 2 case'y (Mieszka I, Sikorskiego) w shadow_decisions mają nowe pola

### Etap 3 (31.05, flip)
- ACK Adrian → hot-reload `ENABLE_R1_PROGRESSIVE_CLIP=true`, `ENABLE_V319H_CONTINUATION_GUARD=true`, `ENABLE_DIFFICULT_CASE_KOORD_REDIRECT=true`
- Monitor 24h: difficult_case_log rate, operator override rate
- Hot-reload rollback gotów (5s)

### Etap 4 (07.06, A/B + decyzja)
- Porównanie tydzień 21-27.05 (przed) vs 01-07.06 (po)
- Metryki:
  - Operator override rate (Bartek/Adrian INNY)
  - KOORD verdict rate
  - difficult_case_log entries — co operatorzy z nimi robili
- Adrian Q&A
- Decyzja: design FIX-B Wariant 1 osobny sprint (cosine-gate) lub odłożyć

---

## 4. Rollback (hot-reload, ~5s)

```bash
# Soft (per-flag):
python3 -c "import json,os,tempfile; p='/root/.openclaw/workspace/scripts/flags.json'; d=json.load(open(p)); d['ENABLE_R1_PROGRESSIVE_CLIP']=False; d['ENABLE_V319H_CONTINUATION_GUARD']=False; d['ENABLE_DIFFICULT_CASE_KOORD_REDIRECT']=False; fd,t=tempfile.mkstemp(dir=os.path.dirname(p)); open(fd,'w').write(json.dumps(d,indent=2,ensure_ascii=False)); os.replace(t,p)"

# Hard (kod revert):
cd /root/.openclaw/workspace/scripts/dispatch_v2
git revert <commit> --no-edit
sudo systemctl restart dispatch-shadow
# dispatch-telegram WYMAGA Adrian ACK
```

---

## 5. Difficult case log — schema

```json
{
  "ts": "2026-05-28T18:26:34Z",
  "order_id": "476777",
  "restaurant": "Rukola Sienkiewicza",
  "delivery_address": "Kraszewskiego 45b/112",
  "verdict_legacy": "PROPOSE",
  "verdict_redirected": "KOORD",
  "fix_deltas_applied": {
    "r1_progressive": -60.0,
    "v319h_guard": 0.0
  },
  "best_pre_fixes": {
    "courier_id": "376", "name": "Paweł SC",
    "score_orig": 10.5, "score_post_fixes": -49.5,
    "cosine": -0.991, "max_bag_min": 25.31,
    "r5_detour_km": 1.21
  },
  "candidates_above_floor_count": 0,
  "score_floor": -30.0,
  "operator_decision": null,  // filled later by reconciliation
  "telemetry": {
    "pool_total": 5, "pool_feasible": 3,
    "auto_route": "ACK"
  }
}
```

**Cel:** korpus do uczenia ML / FIX-B kalibracji. Każdy case z `difficult_case_log` to historia: "oto co system uznał za trudne, oto co operator zrobił". Materiał do Faza 6 (klastry osiedlowe) + LGBM.

---

## 6. Kryteria akceptacji Etapu 1

- [ ] 12/12 nowych testów PASS
- [ ] Smoke 5 min: brak ERROR/WARN w journalctl dispatch-shadow
- [ ] Pierwsza propozycja post-restart ma w shadow_decisions.jsonl:
  - `bonus_r1_progressive_shadow_delta` (key obecny, nawet 0)
  - `bonus_v319h_guard_shadow_delta` (key obecny)
  - `difficult_case_redirect` (key obecny lub null)
- [ ] Zero zmian w produkcyjnym verdict/best (flagi OFF)

---

## 7. NIGDY (per dispatch_v2 CLAUDE.md)

- NIE restart dispatch-telegram bez explicit ACK Adriana
- NIE `jq`, `sed` read-only, NIE heredoc z cudzysłowami
- NIE modify wave_scoring.py
- Atomic writes (temp+fsync+rename) dla flags.json
- Per-step ACK: po commit/tag → STOP for ACK przed restart

**Owner sprintu:** Claude (Z2/Z3 quality > speed, per Adrian "krok po kroku, pewność rezultatów" 2026-05-28).
