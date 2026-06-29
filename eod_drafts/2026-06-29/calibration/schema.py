import json, sys

def describe(label, line):
    print(f"\n===== {label} =====")
    try:
        d = json.loads(line)
    except Exception as e:
        print("  PARSE ERROR:", e, " raw[:200]=", line[:200])
        return
    if not isinstance(d, dict):
        print("  top-level type:", type(d).__name__, str(d)[:300])
        return
    for k, v in d.items():
        t = type(v).__name__
        s = ""
        if isinstance(v, (list, dict)):
            s = f" len={len(v)}"
            if isinstance(v, list) and v:
                el = v[0]
                if isinstance(el, dict):
                    s += " elem_keys=" + ",".join(list(el.keys())[:25])
                else:
                    s += " elem=" + str(el)[:80]
            elif isinstance(v, dict):
                s += " keys=" + ",".join(list(v.keys())[:25])
        else:
            s = " = " + str(v)[:120]
        print(f"  {k} ({t}){s}")

if __name__ == "__main__":
    label = sys.argv[1]
    line = sys.stdin.readline()
    describe(label, line)
