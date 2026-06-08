#!/usr/bin/env python3
"""Purge geo-poison: usuń z geocode_cache.json klucze BEZ ulicy (sam numer domu,
np. „3, białystok"). To zatrute wpisy z buga `m`-eating-M-streets (2026-06-08):
„Magazynowa 3"/„Malachitowa 3" kolidowały w jednym kluczu. Po deployu fixu regexu
+ restart usługi te wpisy re-geokodują się poprawnie (Google primary), pod własnym
kluczem z nazwą ulicy.

URUCHAMIAĆ DOPIERO PO restart dispatch-shadow + dispatch-panel-watcher (inaczej
stary kod re-zatruje). Domyślnie DRY-RUN; `--apply` zapisuje (z backupem).
"""
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

CACHE = Path("/root/.openclaw/workspace/dispatch_state/geocode_cache.json")


def is_streetless(key: str) -> bool:
    core = re.sub(r",?\s*bia[łl]ystok\s*$", "", key.strip().lower()).strip(" ,")
    return bool(re.fullmatch(r"\d+[a-z]?", core))


def main(apply: bool) -> int:
    data = json.loads(CACHE.read_text())
    bad = {k: v for k, v in data.items() if is_streetless(k)}
    print(f"cache entries total: {len(data)}")
    print(f"streetless (poisoned) keys: {len(bad)}")
    for k, v in list(bad.items())[:20]:
        print(f"  PURGE {k!r:18} <- {v.get('original','')!r} ({v.get('source','')})")
    if len(bad) > 20:
        print(f"  … +{len(bad) - 20} more")
    if not apply:
        print("\nDRY-RUN — nic nie zapisano. Dodaj --apply aby usunąć.")
        return 0
    if not bad:
        print("nic do usunięcia.")
        return 0
    backup = CACHE.with_suffix(f".json.bak-pre-streetless-purge-{int(time.time())}")
    backup.write_text(CACHE.read_text())
    print(f"\nbackup: {backup}")
    cleaned = {k: v for k, v in data.items() if k not in bad}
    fd, tmp = tempfile.mkstemp(dir=str(CACHE.parent), prefix=f".{CACHE.name}.tmp-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, CACHE)
    print(f"removed {len(bad)} keys; cache now {len(cleaned)} entries.")
    return 0


if __name__ == "__main__":
    sys.exit(main(apply="--apply" in sys.argv))
