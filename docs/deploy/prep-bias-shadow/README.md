# C2 prep-bias — live-shadow monitor (ACK Adrian 21.06: p70/median + live-shadow, STOP przed flipem)

Snapshoty jednostek systemd (żywe w `/etc/systemd/system/`). Skrypt kanon: `tools/prep_bias_shadow_monitor.py`.

**Po co:** pojedynczy replay 21.06 dał NO-GO dla p80, ~rzut-monetą median/p70 na małym czystym
podzbiorze (matched_only proposed==final, n≈200). Monitor re-mierzy codziennie (05:00 UTC, po
`dispatch-faza7-kpi` 04:00 odświeżającym 14d backfill) → akumuluje czy median/p70 precyzja stabilnie
>0.5 i jak opt/pess w czasie. Dopisuje rekord do `dispatch_state/prep_bias_shadow_metrics.jsonl`.

**Bezpieczeństwo:** READ-ONLY na logach; flaga decyzyjna `ENABLE_PREP_BIAS_TABLE=OFF` (korekta NIE
wpływa na R6); hot-path `feasibility_v2` nietknięty; zero restartu dispatch-services.
opt/pess NIEREDUKOWALNE offline → rozstrzyga dopiero realny flip (ACK-gated, NIE tu).

**Przegląd po ~1-2 tyg:** `tail dispatch_state/prep_bias_shadow_metrics.jsonl` → jeśli median/p70
precyzja stabilnie >0.5 i opt/pess midpoint >baseline 68.5% → kandydat do live-flip (osobny ACK).
