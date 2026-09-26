#!/usr/bin/env python3
"""Mark validated tasks as pushed when Docker Hub has the tag with the digest of the local validated image
(earlier runs checked with `docker manifest inspect`, which hits the registry pull rate limit and reported
successful pushes as failed). Removes the local tag afterwards unless --keep."""
import json, subprocess, sys, urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_swe_together as B

keep = "--keep" in sys.argv
st = B.Status(B.HERE / "status.json")
for task, v in sorted(st.data.items()):
    if not v.get("validated") or v.get("pushed"):
        continue
    ref = f"bdqnghi/swe-together:{task}"
    try:
        with urllib.request.urlopen(f"https://hub.docker.com/v2/repositories/bdqnghi/swe-together/tags/{task}",
                                    timeout=30) as r:
            hub = json.load(r).get("digest")
    except Exception:  # noqa: BLE001
        hub = None
    local = subprocess.run(["docker", "image", "inspect", "-f", "{{json .RepoDigests}}", ref], capture_output=True,
                           text=True).stdout.strip()
    ok = bool(hub) and hub in local
    print(task, "hub", hub, "local", local or "-", "->", "pushed" if ok else "NOT confirmed")
    if ok:
        st.update(task, pushed=True, repo_digests=local)
        if not keep and task not in B.PROTECTED:
            subprocess.run(["docker", "rmi", ref], capture_output=True)
