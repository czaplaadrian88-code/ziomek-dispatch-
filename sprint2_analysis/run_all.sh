#!/usr/bin/env bash
# Sprint 2 analysis run-wrapper. Pure offline, no service touch.
set -euo pipefail

cd "$(dirname "$0")"
mkdir -p logs data

DATE="$(date +%Y-%m-%d)"
echo "=== Sprint 2 analysis run @ $(date +'%H:%M Warsaw') ==="

echo "[0/5] Sanity checks..."
python3 sanity_checks.py | tee logs/sprint2_sanity.txt

echo
echo "[1/5] Data inventory..."
python3 data_inventory.py | tee logs/sprint2_data_inventory.txt

echo
echo "[2/5] TAK=0 mystery..."
python3 tak_mystery.py | tee logs/sprint2_tak_mystery.txt

echo
echo "[3/5] Override patterns..."
python3 override_patterns.py | tee logs/sprint2_override_patterns.txt

echo
echo "[4/5] Propose flow uptime..."
python3 propose_uptime_analysis.py | tee logs/sprint2_propose_uptime.txt

echo
echo "[5/5] Building report..."
python3 report_builder.py --date "$DATE"

echo
echo "Done. Report: data/sprint2_root_cause_${DATE}.md"
