#!/usr/bin/env python3
"""D3-cleanup helper: atomowo usuwa klucz ENABLE_ETA_QUANTILE_R6_BAGCAP z flags.json.
Exit 0 = usunięty; 2 = klucz nieobecny (idempotentnie OK); 3 = klucz ma wartość true
(ktoś zrobił rollback flipa — NIE usuwać, ABORT aplikacji cleanupu)."""
import json
import os
import sys
import tempfile

P = "/root/.openclaw/workspace/scripts/flags.json"
KEY = "ENABLE_ETA_QUANTILE_R6_BAGCAP"

d = json.load(open(P))
if KEY not in d:
    print(f"{KEY}: brak klucza (idempotentnie OK)")
    sys.exit(2)
if d[KEY] is not False:
    print(f"ABORT: {KEY}={d[KEY]!r} — rollback flipa wykryty, NIE usuwam")
    sys.exit(3)
bak = P + ".bak-pre-d3-cleanup-2026-07-20"
with open(bak, "w") as f:
    json.dump(d, f, indent=2, ensure_ascii=False)
del d[KEY]
fd, t = tempfile.mkstemp(dir=os.path.dirname(P))
with os.fdopen(fd, "w") as f:
    f.write(json.dumps(d, indent=2, ensure_ascii=False))
    f.flush()
    os.fsync(f.fileno())
os.replace(t, P)
print(f"{KEY}: usunięty (backup: {bak})")
