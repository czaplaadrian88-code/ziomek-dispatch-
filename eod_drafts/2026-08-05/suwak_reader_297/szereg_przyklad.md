# SZEREG CZASOWY — przyklad rekordow (05.08, bieg reczny + bieg przez harness)

Pliki `.jsonl` w `eod_drafts/` sa wykluczone przez `.gitignore:47` — ponizej ich TRESC,
zeby format wyjscia byl w repo. Zywe pliki biegow zostaly w worktree.

## bieg reczny (--out-dir <worktree>)

`/root/worktrees/dispatch_v2/active/20260805-suwak-reader-297-cto/eod_drafts/2026-08-05/suwak_reader_297/suwak_autonomii.jsonl` — 1 linia(e)

```json
{
 "schema": "suwak_autonomii.series.v1",
 "day": "2026-08-05",
 "generated_utc": "2026-08-05T08:06:28Z",
 "read_started_utc": "2026-08-05T08:06:26Z",
 "mode": "READ_ONLY",
 "status": "OK",
 "degraded": [],
 "snapshot": "/root/worktrees/dispatch_v2/active/20260805-suwak-reader-297-cto/eod_drafts/2026-08-05/suwak_reader_297/2026-08-05/suwak_autonomii.json",
 "inputs": [
  {
   "path": "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl",
   "present": true,
   "size_bytes": 36922104,
   "mtime_utc": "2026-08-05T08:05:35Z",
   "age_hours": 0.0,
   "records": 382,
   "bad_lines": 0,
   "sha256": "5e9b77c3e81c78ebad3fdc3c665236b0be75f23e78a764ee406cc14d1420e15c",
   "day_first": "2026-08-03",
   "day_last": "2026-08-05",
   "skipped": null
  },
  {
   "path": "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1",
   "present": true,
   "size_bytes": 138994886,
   "mtime_utc": "2026-08-03T00:00:04Z",
   "age_hours": 56.1,
   "records": 1228,
   "bad_lines": 0,
   "sha256": "25bd08ac8c519601be1ec20f082b1464895fb201d791a909ef55cdd886e68d11",
   "day_first": "2026-07-28",
   "day_last": "2026-08-02",
   "skipped": null
  },
  {
   "path": "/root/.openclaw/workspace/dispatch_state/outcomes_clean_shadow.jsonl",
   "present": true,
   "size_bytes": 3612418,
   "mtime_utc": "2026-08-05T04:40:01Z",
   "age_hours": 3.4,
   "records": 20462,
   "bad_lines": 0,
   "sha256": "1f5cb84edac27d2bbf40e037c16f23c1b99c522512fdd45b15a82fc84db64c55",
   "day_first": "2026-05-17",
   "day_last": "2026-08-05",
   "skipped": null
  }
 ],
 "liczba1": {
  "metric": "would_auto_assign_d",
  "pct": 19.729206963249517,
  "true": 306,
  "n": 1551,
  "baseline_pct": 1.0315925209542232,
  "dprime_pct": 17.60154738878143,
  "auto_route_auto_pct": 10.058027079303676,
  "pool_ge3": {
   "n": 1074,
   "d_pct": 25.41899441340782
  },
  "pool_le2": {
   "n": 477,
   "d_pct": 6.918238993710692
  },
  "window": {
   "day_first": "2026-07-28",
   "day_last": "2026-08-05",
   "days_distinct": 9
  },
  "intake": {
   "records_read": 1610,
   "duplicates_dropped": 0,
   "excluded_lifecycle": 59,
   "excluded_missing_gate_fields": 0,
   "decisions": 1551,
   "bad_lines": 0,
   "files_read": 2
  },
  "top_blockers": {
   "pos_not_informed": 703,
   "late_pickup_extension": 294,
   "late_pickup_redirect": 276,
   "score_distrust_ceiling": 246,
   "scarcity_pool": 172
  },
  "reason": null
 },
 "liczba2": {
  "metric": "agree_top1_by_pool",
  "global": {
   "agree_pct": 57.05698367705992,
   "n": 20462
  },
  "pool_ge3": {
   "agree_pct": 66.908037653874,
   "n": 1381
  },
  "pool_le2": {
   "agree_pct": 28.639618138424822,
   "n": 419
  },
  "pool_unknown": {
   "agree_pct": 56.96602722109099,
   "n": 18662
  },
  "scarcity": {
   "baseline_disagree_rate_pct": 33.091962346125996,
   "le2_disagree_rate_pct": 71.36038186157518,
   "le2_disagree_observed": 299,
   "le2_disagree_expected_at_baseline": 138.7,
   "le2_disagree_excess_scarcity": 160.3,
   "excess_share_of_le2_disagreements_pct": 53.62698253168297,
   "excess_share_of_measured_disagreements_pct": 21.20961346160477,
   "measured_disagreements": 756
  },
  "pool_coverage_pct": 8.796794057276903,
  "window": {
   "day_first": "2026-05-17",
   "day_last": "2026-08-05",
   "days_distinct": 81
  },
  "pool_known_window": {
   "day_first": "2026-07-23",
   "day_last": "2026-08-04",
   "days_distinct": 13,
   "records": 1800,
   "share_of_corpus_pct": 8.796794057276903
  },
  "overlap": {
   "agree_pct": 58.77022653721683,
   "n": 1545,
   "label": "okno wspólne z shadow_decisions 2026-07-28..2026-08-05"
  },
  "reason": null
 },
 "join": {
  "n_joined": 1526,
  "matrix": {
   "auto=T,agree=T": 223,
   "auto=T,agree=F": 80,
   "auto=F,agree=T": 685,
   "auto=F,agree=F": 538
  },
  "agree_given_auto_ready_pct": 73.5973597359736,
  "agree_given_not_auto_ready_pct": 56.00981193785773,
  "reason": null
 }
}
```

## bieg przez shadow_review_daily --only suwak_autonomii

`/root/worktrees/dispatch_v2/active/20260805-suwak-reader-297-cto/eod_drafts/2026-08-05/suwak_reader_297/harness_e2e/suwak_autonomii.jsonl` — 1 linia(e)

```json
{
 "schema": "suwak_autonomii.series.v1",
 "day": "2026-08-05",
 "generated_utc": "2026-08-05T08:13:36Z",
 "read_started_utc": "2026-08-05T08:13:35Z",
 "mode": "READ_ONLY",
 "status": "OK",
 "degraded": [],
 "snapshot": "/root/worktrees/dispatch_v2/active/20260805-suwak-reader-297-cto/eod_drafts/2026-08-05/suwak_reader_297/harness_e2e/2026-08-05/suwak_autonomii.json",
 "inputs": [
  {
   "path": "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl",
   "present": true,
   "size_bytes": 36943685,
   "mtime_utc": "2026-08-05T08:10:36Z",
   "age_hours": 0.0,
   "records": 384,
   "bad_lines": 0,
   "sha256": "9d9b7e305a10bd12f2822d2055e989ab3c5eb6a029b4dae3ca910d9b24739087",
   "day_first": "2026-08-03",
   "day_last": "2026-08-05",
   "skipped": null
  },
  {
   "path": "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1",
   "present": true,
   "size_bytes": 138994886,
   "mtime_utc": "2026-08-03T00:00:04Z",
   "age_hours": 56.2,
   "records": 1228,
   "bad_lines": 0,
   "sha256": "25bd08ac8c519601be1ec20f082b1464895fb201d791a909ef55cdd886e68d11",
   "day_first": "2026-07-28",
   "day_last": "2026-08-02",
   "skipped": null
  },
  {
   "path": "/root/.openclaw/workspace/dispatch_state/outcomes_clean_shadow.jsonl",
   "present": true,
   "size_bytes": 3612418,
   "mtime_utc": "2026-08-05T04:40:01Z",
   "age_hours": 3.6,
   "records": 20462,
   "bad_lines": 0,
   "sha256": "1f5cb84edac27d2bbf40e037c16f23c1b99c522512fdd45b15a82fc84db64c55",
   "day_first": "2026-05-17",
   "day_last": "2026-08-05",
   "skipped": null
  }
 ],
 "liczba1": {
  "metric": "would_auto_assign_d",
  "pct": 19.716494845360824,
  "true": 306,
  "n": 1552,
  "baseline_pct": 1.0309278350515463,
  "dprime_pct": 17.59020618556701,
  "auto_route_auto_pct": 10.051546391752577,
  "pool_ge3": {
   "n": 1074,
   "d_pct": 25.41899441340782
  },
  "pool_le2": {
   "n": 478,
   "d_pct": 6.903765690376569
  },
  "window": {
   "day_first": "2026-07-28",
   "day_last": "2026-08-05",
   "days_distinct": 9
  },
  "intake": {
   "records_read": 1612,
   "duplicates_dropped": 0,
   "excluded_lifecycle": 60,
   "excluded_missing_gate_fields": 0,
   "decisions": 1552,
   "bad_lines": 0,
   "files_read": 2
  },
  "top_blockers": {
   "pos_not_informed": 703,
   "late_pickup_extension": 294,
   "late_pickup_redirect": 276,
   "score_distrust_ceiling": 246,
   "scarcity_pool": 172
  },
  "reason": null
 },
 "liczba2": {
  "metric": "agree_top1_by_pool",
  "global": {
   "agree_pct": 57.05698367705992,
   "n": 20462
  },
  "pool_ge3": {
   "agree_pct": 66.908037653874,
   "n": 1381
  },
  "pool_le2": {
   "agree_pct": 28.639618138424822,
   "n": 419
  },
  "pool_unknown": {
   "agree_pct": 56.96602722109099,
   "n": 18662
  },
  "scarcity": {
   "baseline_disagree_rate_pct": 33.091962346125996,
   "le2_disagree_rate_pct": 71.36038186157518,
   "le2_disagree_observed": 299,
   "le2_disagree_expected_at_baseline": 138.7,
   "le2_disagree_excess_scarcity": 160.3,
   "excess_share_of_le2_disagreements_pct": 53.62698253168297,
   "excess_share_of_measured_disagreements_pct": 21.20961346160477,
   "measured_disagreements": 756
  },
  "pool_coverage_pct": 8.796794057276903,
  "window": {
   "day_first": "2026-05-17",
   "day_last": "2026-08-05",
   "days_distinct": 81
  },
  "pool_known_window": {
   "day_first": "2026-07-23",
   "day_last": "2026-08-04",
   "days_distinct": 13,
   "records": 1800,
   "share_of_corpus_pct": 8.796794057276903
  },
  "overlap": {
   "agree_pct": 58.77022653721683,
   "n": 1545,
   "label": "okno wspólne z shadow_decisions 2026-07-28..2026-08-05"
  },
  "reason": null
 },
 "join": {
  "n_joined": 1526,
  "matrix": {
   "auto=T,agree=T": 223,
   "auto=T,agree=F": 80,
   "auto=F,agree=T": 685,
   "auto=F,agree=F": 538
  },
  "agree_given_auto_ready_pct": 73.5973597359736,
  "agree_given_not_auto_ready_pct": 56.00981193785773,
  "reason": null
 }
}
```

