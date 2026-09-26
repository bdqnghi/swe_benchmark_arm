#!/usr/bin/env python3
"""Print per-family counts and a per-task table from status.json (and rf_base_status.json).

Usage: summary.py [--markdown]   (the markdown table is what NOTES.md's status section is generated from)
"""
import json, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import build as B  # noqa: E402

st = json.load(open(ROOT / "status.json"))
bst = json.load(open(ROOT / "rf_base_status.json")) if (ROOT / "rf_base_status.json").exists() else {}
tasks = sorted(p.name for p in B.TASKS.iterdir() if (p / "environment" / "Dockerfile").exists())
c = Counter()
rows = []
for t in tasks:
    v = st.get(t, {})
    fam = B.family(t)
    c[fam, "total"] += 1
    for k in ("built", "validated", "pushed"):
        c[fam, k] += bool(v.get(k))
    val = v.get("validation") or {}
    detail = ""
    if fam == "rf" and val:
        detail = f"tests_reward={val.get('tests_reward')} relevant={val.get('relevant')} p2f={val.get('full_p2f')} m2f={val.get('full_m2f')}"
    elif val:
        detail = f"reward={val.get('reward')}"
    base = bst.get(B.rf_base_name(t), {}) if fam == "rf" else {}
    rows.append((t, fam, bool(v.get("built")), v.get("validated"), bool(v.get("pushed")),
                 (base.get("pushed") if fam == "rf" else ""), detail, v.get("error") or ""))
for fam in ("swebenchpro", "deepswe", "rf"):
    print(f"{fam:12s} total={c[fam, 'total']} built={c[fam, 'built']} validated={c[fam, 'validated']} pushed={c[fam, 'pushed']}")
if "--markdown" in sys.argv:
    print("\n| task | built | validated | pushed | detail | error |\n|---|---|---|---|---|---|")
    for t, fam, b, v, p, bp, d, e in rows:
        print(f"| `{t}` | {'yes' if b else 'no'} | {'yes' if v else ('FAIL' if v is False else '-')} | "
              f"{'yes' if p else 'no'} | {d} | {e} |")
