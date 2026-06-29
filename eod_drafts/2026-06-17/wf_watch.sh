D="/root/.claude/projects/-root/6ddc95a2-f7c5-49cf-8826-85e752da3613/subagents/workflows/wf_4b0b7718-c88"
J="$D/journal.jsonl"
PY=/root/.openclaw/venvs/dispatch/bin/python
prev=""; stable=0
for i in $(seq 1 80); do
  sr=$($PY -c "
import json
s=r=0
for l in open('$J'):
    l=l.strip()
    if not l: continue
    try: d=json.loads(l)
    except: continue
    t=d.get('type')
    if t=='started': s+=1
    elif t=='result': r+=1
print(s,r)
" 2>/dev/null)
  s=${sr% *}; r=${sr#* }
  sig="$s-$r"
  if [ "${s:-0}" -ge 11 ] && [ "${s:-0}" -eq "${r:-0}" ]; then
    if [ "$sig" == "$prev" ]; then stable=$((stable+1)); else stable=0; fi
    if [ "$stable" -ge 4 ]; then echo "WORKFLOW_DONE agents_started=$s results=$r"; exit 0; fi
  else
    stable=0
  fi
  prev="$sig"
  sleep 20
done
echo "WATCHER_TIMEOUT started=$s results=$r"
