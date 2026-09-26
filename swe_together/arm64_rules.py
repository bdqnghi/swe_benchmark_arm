"""Per-task arm64 changes to the SWE-Together task Dockerfiles (tasks/<id>/environment/Dockerfile).

Every change is a literal or regex substitution applied to the upstream Dockerfile text before
`docker build --platform linux/arm64`. GLOBAL rules apply to every task; TASK rules only to the
named task. Each rule states why it is needed. The resulting Dockerfiles are what
`swe-together-arm64.patch` records.
"""

# (regex, replacement, reason)
GLOBAL = [
    (r"\.linux-amd64\.tar\.gz", ".linux-arm64.tar.gz",
     "Go toolchain tarball: same version, arm64 build (go.dev/dl and dl.google.com)"),
    # sha256 of go1.25.9.linux-amd64.tar.gz -> of go1.25.9.linux-arm64.tar.gz (go.dev/dl/?mode=json&include=all)
    (r"00859d7bd6defe8bf84d9db9e57b9a4467b2887c18cd93ae7460e713db774bc1",
     "ec342e7389b7f489564ed5463c63b16cf8040023dabc7861256677165a8c0e2b",
     "Go tarball sha256: go1.25.9 linux-arm64 checksum"),
    (r"torchvision==0\.21\.0\+cpu", "torchvision==0.21.0",
     "download.pytorch.org/whl/cpu has no aarch64 torchvision 0.21.0+cpu; its aarch64 0.21.0 wheel is the CPU build"),
]

# task_id -> list of (regex, replacement, reason)
TASK = {
    "unsloth-idefics3-finetune": [
        (r"torch==2\.5\.1\+cpu", "torch==2.5.1",
         "download.pytorch.org/whl/cpu has no aarch64 2.5.1+cpu wheel; its aarch64 2.5.1 wheel is the CPU build"),
        (r"torchvision==0\.20\.1\+cpu", "torchvision==0.20.1", "same for torchvision 0.20.1"),
    ],
}

# task_id -> package names left out of the pip constraints (official version has no aarch64 build, ...)
SKIP_CONSTRAINTS = {
}

# task_id -> {filename: content} extra files written into the build context
TASK_FILES = {
}

# task_id -> base image override for tasks whose FROM names a private ghcr base
# (filled in when the base images are reconstructed; see build_bases.py)
BASE_FROM = {
    "ghcr.io/togetherbench/togetherbench/hyperswitch-dev:latest": "bdqnghi/swe-together-base:hyperswitch-dev",
    "ghcr.io/togetherbench/togetherbench/reigh-dev:latest": "bdqnghi/swe-together-base:reigh-dev",
    "ghcr.io/togetherbench/togetherbench/comfyui-dev:latest": "bdqnghi/swe-together-base:comfyui-dev",
    "ghcr.io/togetherbench/togetherbench/sd-scripts-dev:latest": "bdqnghi/swe-together-base:sd-scripts-dev",
}


def patch_dockerfile(task: str, text: str) -> tuple[str, list[str]]:
    """Return (patched text, list of applied rule reasons)."""
    import re
    applied = []
    for pat, rep, why in GLOBAL + TASK.get(task, []):
        new, n = re.subn(pat, rep, text, flags=re.M)
        if n:
            applied.append(f"{why} (x{n})")
            text = new
    for old, new in BASE_FROM.items():
        if old in text:
            text = text.replace(old, new)
            applied.append(f"FROM {old} -> {new} (arm64 reconstruction of the private base)")
    return text, applied
