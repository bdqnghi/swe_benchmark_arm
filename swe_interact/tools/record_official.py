#!/usr/bin/env python3
"""Record an official amd64 (qemu) validation result as the expected outcome of an upstream quirk.

Usage: record_official.py <task> <validate.py log of the official run> "<note>"
Writes rules/<task>/official_amd64_validation.json; validate.py then accepts an arm64 result equal to it.
"""
import json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import build as B  # noqa: E402

task, logp, note = sys.argv[1:4]
s = open(logp).read()
j = json.loads(s[s.index("{"):])
keep = ("reward", "tests_reward", "relevant", "full_p2f", "full_m2f", "filtered_summary", "relevant_passed_after")
out = {k: v for k, v in j.items() if k in keep}
out.update(image=B.official_from(task) if B.family(task) == "rf" else "", platform="linux/amd64 (qemu)", note=note)
p = ROOT / "rules" / task / "official_amd64_validation.json"
p.parent.mkdir(parents=True, exist_ok=True)
json.dump(out, open(p, "w"), indent=1)
print(p, out)
