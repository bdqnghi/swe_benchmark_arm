#!/usr/bin/env python3
"""Summarise each filesystem layer of an image whose BuildKit history has been scrubbed ("created by buildkit").

The swe-atlas RF images carry no instruction history, so tools/reconstruct.py cannot be used. This lists, per
layer: size, file count, the heaviest path prefixes, dpkg packages added (from var/lib/dpkg/status), Python
distributions added (*.dist-info), executables added under */bin, and whiteouts. That is enough to write the
recipe by hand (rf/recipes/<repo>.Dockerfile).

Usage: layers.py <image> [--depth 3] [--top 12] [--json out.json]
"""
import argparse, io, json, os, re, subprocess, sys, tarfile, tempfile
from collections import Counter
from pathlib import Path


def dpkg_pkgs(text):
    pk = {}
    for block in text.split("\n\n"):
        m = re.search(r"^Package: (\S+)", block, re.M)
        v = re.search(r"^Version: (\S+)", block, re.M)
        s = re.search(r"^Status: .* installed$", block, re.M)
        if m and v and s:
            pk[m.group(1)] = v.group(1)
    return pk


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--depth", type=int, default=3)
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--json")
    ap.add_argument("--keep-tar")
    a = ap.parse_args()
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as td:
        tarp = a.keep_tar or os.path.join(td, "img.tar")
        if not os.path.exists(tarp):
            subprocess.run(["docker", "save", "-o", tarp, a.image], check=True)
        outer = tarfile.open(tarp)
        manifest = json.load(outer.extractfile("manifest.json"))[0]
        cfg = json.load(outer.extractfile(manifest["Config"]))
        prev_dpkg, report = {}, []
        for i, lp in enumerate(manifest["Layers"]):
            f = outer.extractfile(lp)
            try:
                inner = tarfile.open(fileobj=f, mode="r|*")
            except tarfile.ReadError:
                continue
            sizes, counts, wh, bins, dists = Counter(), Counter(), [], [], []
            total = n = 0
            dpkg_text = None
            for m in inner:
                name = m.name.lstrip("./")
                base = os.path.basename(name)
                if base.startswith(".wh."):
                    wh.append(name)
                    continue
                key = "/".join(name.split("/")[: a.depth])
                sizes[key] += m.size
                counts[key] += 1
                total += m.size
                n += 1
                if name == "var/lib/dpkg/status" and m.isfile():
                    dpkg_text = inner.extractfile(m).read().decode(errors="replace")
                if re.search(r"(^|/)bin/[^/]+$", name) and (m.isfile() or m.issym()) and not name.startswith(("usr/bin", "bin/", "usr/sbin", "sbin/")):
                    bins.append(name)
                if name.endswith(".dist-info") and m.isdir():
                    dists.append(name)
            added = removed = {}
            if dpkg_text is not None:
                cur = dpkg_pkgs(dpkg_text)
                added = {k: v for k, v in cur.items() if prev_dpkg.get(k) != v}
                removed = sorted(set(prev_dpkg) - set(cur))
                prev_dpkg = cur
            rep = {"layer": i, "size_mb": round(total / 2**20, 1), "files": n,
                   "top": [(k, round(v / 2**20, 1), counts[k]) for k, v in sizes.most_common(a.top)],
                   "dpkg_added": added, "dpkg_removed": removed, "bins": bins[:60], "dists": dists[:400],
                   "whiteouts": wh[:40]}
            report.append(rep)
            print(f"=== layer {i}: {rep['size_mb']} MB, {n} files")
            for k, mb, c in rep["top"]:
                print(f"    {mb:9.1f} MB {c:7d}  {k}")
            if added:
                print(f"    dpkg +{len(added)}: {' '.join(sorted(added))[:1500]}")
            if removed:
                print(f"    dpkg -{len(removed)}: {' '.join(removed)[:500]}")
            if bins:
                print(f"    bins: {' '.join(bins[:40])}")
            if dists:
                print(f"    dists({len(dists)}): {' '.join(os.path.basename(d) for d in dists[:80])}")
            if wh:
                print(f"    whiteouts: {' '.join(wh[:20])}")
        print("config:", json.dumps(cfg.get("config"), indent=None))
        if a.json:
            json.dump({"config": cfg.get("config"), "layers": report, "dpkg": prev_dpkg}, open(a.json, "w"), indent=1)


if __name__ == "__main__":
    main()
