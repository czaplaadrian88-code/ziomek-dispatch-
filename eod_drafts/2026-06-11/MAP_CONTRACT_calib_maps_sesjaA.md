# KONTRAKT FORMATU MAP KALIBRACYJNYCH — sesja A (silnik) ↔ sesja B (generatory)

**Autor:** SESJA A (SP-B2-PEAKWIN, 2026-06-11). Konsument: `dispatch_v2/calib_maps.py`
(LIVE w pipeline/serializerze, fail-soft dopóki plików brak — generujcie śmiało,
konsumpcja włączy się sama od następnego ticka po pojawieniu się pliku).

## Sloty czasowe (wspólne)

`calib_maps.time_slot_warsaw(now)` — **importujcie tę funkcję w generatorach**
(czysty kod, zero I/O), żeby bucketować identycznie jak konsument:

| slot | okno Warsaw |
|---|---|
| `peak_lunch` | 11:00–13:59 |
| `high_risk` | 14:00–16:59 (strefa śmierci, raport §3.1) |
| `peak_dinner` | 17:00–19:59 |
| `off` | reszta |
| `all` | specjalny klucz w MAPACH = fallback bez podziału na sloty |

Lookup konsumenta: najpierw slot bieżący, potem `all`, potem None.

## 1. `dispatch_state/eta_quantile_map.json` (SP-B2-ETAQ)

```json
{
  "version": 1,
  "generated_at": "2026-06-12T04:30:00+00:00",
  "buckets": [
    {"slot": "peak_lunch", "pred_lo": 20.0, "pred_hi": 30.0,
     "p50": 14.0, "p80": 19.0, "n": 312}
  ]
}
```

- `pred_lo <= travel_min < pred_hi` (lewostronnie domknięte).
- Konsument bierze `p50` jako `travel_min_cal` (p80 dostępne na żądanie).
- Koszyki o n poniżej waszego progu (sugestia ≥30) po prostu NIE emitujcie —
  konsument zwróci None i pole będzie puste (to OK).
- Zapis atomowy (temp+fsync+rename) — konsument cache'uje po mtime.

## 2. `dispatch_state/restaurant_prep_bias.json` (SP-B2-PREPBIAS)

```json
{
  "version": 1,
  "generated_at": "2026-06-12T04:15:00+00:00",
  "global": {
    "peak_lunch": {"bias_med": 5.0, "n": 900, "std": 8.0},
    "all":        {"bias_med": 4.0, "n": 2400, "std": 9.1}
  },
  "restaurants": {
    "pizzeria 105": {
      "peak_lunch": {"bias_med": 12.0, "n": 45, "std": 11.0}
    }
  }
}
```

- Klucz restauracji: **`nazwa.strip().lower()`** (konwencja `new_rest_norm`
  z dispatch_pipeline — dokładnie tak normalizuje konsument).
- `bias_med` w MINUTACH (dodatni = restauracja później niż deklaruje).
- Fallback konsumenta: komórka restauracji → `global[slot]` → `global[all]` → None.
- Min n=30 per komórka egzekwujcie generator-side (roadmapa SP-B2-PREPBIAS).

## Co już konsumuje (LIVE od restartu dispatch-shadow, flagi ON)

- `travel_min_cal` per kandydat w shadow_decisions (LOCATION A+B),
  flaga `ENABLE_ETA_QUANTILE_SHADOW`.
- `prep_bias_min` + `effective_ready_shadow` (ISO) top-level w shadow_decisions,
  flaga `ENABLE_PREP_BIAS_SHADOW`.
- `auto_route_context.auto_route_time_bucket` (peak_lunch/high_risk/peak_dinner/off).

Std per restauracja z prep_bias = wejście do SP-B2-SYNCWORKA (sesja A).
