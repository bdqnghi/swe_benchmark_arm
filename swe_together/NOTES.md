# SWE-Together on arm64

Native `linux/arm64` images for the 109 canonical tasks of SWE-Together
([Togetherbench/SWE-Together](https://github.com/Togetherbench/SWE-Together) @ `891d19e`,
`canonical_full109.json`). The official task images (`ghcr.io/togetherbench/multi-user-turn-codebench/<id>:<tag>`)
are `linux/amd64` only.

* Task images: `bdqnghi/swe-together:<task_id>` (one repository, tag = task id).
* Reconstructed private bases: `bdqnghi/swe-together-base:{hyperswitch-dev,reigh-dev,comfyui-dev,sd-scripts-dev}`.
* Per-task status (built / validated / pushed / reward / official reward / deviations): [`status.json`](status.json).

## Results (2026-09-26)

**109/109 built natively, gold-validated and pushed** (`bdqnghi/swe-together:<task_id>`, arm64, every tag checked present
for arm64 through the Docker Hub API), plus the four bases. ~97 GB compressed on Hub.

* 42 tasks: reward 1.0 with the reference patch.
* 51 tasks: gold reward below 1.0, **identical on the official amd64 image** (same procedure under qemu).
* 16 tasks: no reference patch upstream; unpatched-repo reward identical on the official image.
* 0 tasks differ from the official image. No task needed replacing.

Rewards below 1.0 (arm64 = official amd64; * = in the 20-task pilot subset `tib/configs/subsets/swe-together.json`):

| Task | Mode | Reward |
|---|---|---|
| banodoco-video-perf-optimize | baseline | 0.0 |
| cli-fix-2026-0 | gold | 0.93 |
| cli-task-4a9dde * | gold | 0.8 |
| cli-task-b12319 * | gold | 0.525 |
| cli-task-c425e4 * | gold | 0.7 |
| cli-task-e5813e | gold | 0.8 |
| comfyui-gemma3-sliding-window | gold | 0.55 |
| comfyui-jina-clip-v2-full | gold | 0.428 |
| comfyui-lumina-axes-lens | gold | 0.49 |
| comfyui-lumina2-lora-prefix | gold | 0.76 |
| comfyui-newbie-lumina-refactor | gold | 0.21 |
| comfyui-triton-windows-amd-fix | gold | 0.75 |
| dataclaw-test-coverage-fix | baseline | 0.0 |
| dataclaw-windows-path-fix * | gold | 0.712 |
| desloppify-treesitter-plugins | baseline | 0.0 |
| desloppify-zone-classification * | baseline | 0.0 |
| gemini-voyager-task-64c72f | gold | 0.0 |
| gemini-voyager-task-aa88f5 * | gold | 0.8 |
| hyperswitch-8008 | gold | 0.75 |
| hyperswitch-8084 | gold | 0.55 |
| hyperswitch-8338 | gold | 0.53 |
| hyperswitch-8377 | gold | 0.36 |
| hyperswitch-8389 * | gold | 0.64 |
| hyperswitch-9063 | gold | 0.33 |
| hyperswitch-9430 | gold | 0.0 |
| hyperswitch-9437 | gold | 0.21 |
| hyperswitch-9465 | gold | 0.2 |
| lock-code-manager-fix-7c955a * | gold | 0.55 |
| mlx-lm-mambacache | baseline | 0.0 |
| no-magic-task-ce67d5 | gold | 0.2 |
| nunchaku-quantize-bugfix | gold | 0.682 |
| nunchaku-svdq-reconstruction | gold | 0.85 |
| openclaw-security-review-flow | baseline | 0.0 |
| pi-mono-auto-0467b78e | gold | 0.048 |
| pi-mono-auto-0750e8eb | baseline | 0.0 |
| pi-mono-auto-0fa6d7bd | gold | 0.32 |
| pi-mono-auto-41636ae5 | gold | 0.52 |
| pi-mono-auto-4439324b | gold | 0.0 |
| pi-mono-auto-6f8288bb | gold | 0.0 |
| pi-mono-auto-93c17d3b | gold | 0.865 |
| pi-mono-auto-95de1efe | gold | 0.0 |
| pi-mono-auto-a4fca584 | gold | 0.0 |
| pi-mono-auto-cad40af4 | gold | 0.4 |
| pi-mono-auto-cbb62cbe | gold | 0.85 |
| pi-mono-auto-d3b2130d * | gold | 0.1 |
| pi-mono-auto-d8c68afc | gold | 0.84 |
| pi-mono-auto-e86a388c | gold | 0.084 |
| pi-mono-auto-ec7037ba | gold | 0.5 |
| pi-mono-auto-fcb17499 | gold | 0.0 |
| pi-mono-foreign-toolcall-fix | gold | 0.7 |
| pi-mono-keybinding-scope | gold | 0.0 |
| pi-mono-tool-execution-write-error | gold | 0.36 |
| qwen3-moe-gguf-dequant | gold | 0.668 |
| reigh-medialightbox-refactor | baseline | 0.0 |
| reigh-preset-data-flow | baseline | 0.15 |
| reigh-radix-props-cleanup | baseline | 0.0 |
| reigh-stroke-overlay-refactor | baseline | 0.05 |
| reigh-taskspane-lightbox-bug | baseline | 0.1 |
| reigh-timeline-mode-cleanup | baseline | 0.0 |
| reigh-timeline-multiselect | baseline | 0.0 |
| rudel-task-491983 | gold | 0.8 |
| rudel-task-8e0bd6 | gold | 0.65 |
| sd-scripts-reg-image-dedup * | gold | 0.85 |
| sd-scripts-sdxl-multires-dedup | gold | 0.4 |
| triton-msvc-c4267-warnings | gold | 0.52 |
| unsloth-idefics3-finetune | baseline | 0.0 |
| vibecomfy-mcp-pr-integration * | baseline | 0.162 |


## Files

| File | Purpose |
|---|---|
| `build_swe_together.py` | Build -> gold-validate -> push -> remove local tag, per task, in parallel; resumable via `status.json` (flock-shared, several builder processes may run). Failing tasks are re-run on the official amd64 image under qemu; an identical reward is recorded as a dataset property (`matches_official`). |
| `arm64_rules.py` | The per-task arm64 Dockerfile substitutions (global + per task) and the FROM rewrite to the reconstructed bases. |
| `pins.py` | Drift control: pins bun, global npm packages, NodeSource nodejs and pip distributions to what the official image resolved. |
| `fingerprint.py` | Probes an image (under qemu for the official amd64 ones): tool versions, every Python environment's distributions, global npm packages, dpkg. Output `fingerprints/<task>.json`; used by `pins.py`. |
| `validate.py` | Gold validation of one image (see below). `validate.py <task> [--image IMG --platform linux/amd64]`. |
| `bases/*.Dockerfile`, `build_bases.py` | Reconstruction of the four private bases. |
| `make_dataset.py`, `swe-together-arm64.patch` | Task tree pointed at the arm64 images. |
| `reconcile.py` | Marks a validated task pushed when Docker Hub holds its digest (early runs checked with `docker manifest inspect`, which hit the registry's anonymous pull rate limit and reported good pushes as failed). |
| `fingerprints/` | What each official image resolved (input of `pins.py`). |
| `removals.tsv` | Log of local images from earlier ARM work removed for disk space. |

## How to run SWE-Together on arm64

```
git clone https://github.com/Togetherbench/SWE-Together && cd SWE-Together && git checkout 891d19e
git apply ../swe_benchmark_arm/swe_together/swe-together-arm64.patch
uv sync
.venv/bin/python src/run_eval.py --env-type docker --model <model> --tag <tag> --workers 4   # or launch.py ... with SWT_SANDBOX=docker
```

The patch sets every task's `[environment] docker_image` to `bdqnghi/swe-together:<task_id>` and replaces
`environment/Dockerfile` by the arm64 Dockerfile that built the image (plus `arm64-pip-constraints.txt` where
used), so a forced rebuild (`--force-build`) reproduces it. Harbor's docker environment uses the prebuilt
`docker_image` unless `force_build` is set.

Out of scope here: the correctness judge (`eval/`) runs on E2B by default (`JUDGE_SANDBOX=e2b|enroot`;
`SWT_SANDBOX=docker` keeps the judge on E2B). E2B builds its own templates from the task Dockerfiles on x86
cloud machines, so the judge is unaffected by these images; an arm64 judge would need the enroot backend
(`JUDGE_SANDBOX=enroot`) pointed at these images, which was not tried.

## Gold validation

`validate.py` does what the harness does after a trial, with the reference patch in place of the agent's:

1. Start the image with the task's `cpus`/`memory` limits (`docker run ... sleep infinity`, as Harbor's compose file).
2. Apply `reference_patch.json`'s `patch` as root exactly as the judge sandbox does
   (`eval/correctness/sandbox.py`): candidates from `src/patch_normalize.apply_candidates`, repo = the diff banner's
   repo or the shallowest `.git` under the well-known roots, `git apply --check` then `-C2`, `chmod -R a+rwX`.
3. Copy `tests/` to `/tests` and run `bash -c "/tests/test.sh > /logs/verifier/test-stdout.txt 2>&1"`
   (Harbor `verifier.py`), then read `/logs/verifier/reward.txt` (else `reward.json`).

test.sh runs as **root**, as on the harness's default sandbox (E2B: `commands.run(..., user="root")`) and on
enroot. Harbor's docker backend instead execs as the image's `USER` (often `agent`). A first pass run that way
lowered cli-fix-2026-0 from 0.93 to 0.63 (`go: could not create module cache: stat /root/go/pkg/mod: permission
denied`, the image sets `GOMODCACHE=/root/go/pkg/mod` but runs as `agent`); the task image recipe is upstream's, so
expect the same under `--env-type docker` with the official images.

Two early tasks (amytis-task-e3714e, cli-task-0ec2e9) were
validated in the first pass as the image USER, and scored 1.0 that way too.

Pass criterion: reward 1.0, or, when the gold reward is below 1.0, the **same reward on the official amd64 image**
run through the identical procedure under qemu (`status.json`: `official_reward`, `matches_official`). Many
SWE-Together reference patches are reconstructions from session logs (`_fidelity: directional`) and do not
reach 1.0 even upstream.

16 of the 109 tasks have no reference patch (`reference_patch.json` `_status: no_canonical`, "tool_use inputs are
bare strings; no diffable content survives"). For those, test.sh is run on the unpatched repository
(`mode: baseline`) and the reward must equal the official image's.

## Drift control

The task Dockerfiles install several things unpinned; a rebuild months after the official one resolves newer
versions. Example: bun 1.4.2 (current) writes JUnit `classname`s differently from bun 1.3.14 (official), which
dropped `agent-swarm-task-4a881b`'s gold reward from 1.0 to 0.375. `fingerprint.py` records what every official
image resolved and `pins.py` rewrites the Dockerfile accordingly:

* bun installer -> `bash -s "bun-v<official>"`;
* `npm install -g <pkg>` -> `<pkg>@<official>` for every package in the official global npm tree;
* NodeSource `nodejs` -> `nodejs=<official dpkg version>` (e.g. `20.20.2-1nodesource1`);
* pip -> `arm64-pip-constraints.txt` with the official image's distributions (all Python environments, build
  backends, editable/VCS/local installs and apt-owned packages excluded, `+cpu` local labels dropped), passed as
  build `ARG PIP_CONSTRAINT`, so it binds every `pip install` of the build but is not set at runtime.

Go toolchains, Rust toolchains, `go.sum`, lockfile-based `bun install`/`npm ci`/`pnpm install --frozen-lockfile`
and the git base commits are already pinned by the upstream Dockerfiles.

## Reconstructed bases

26 tasks start `FROM ghcr.io/togetherbench/togetherbench/{hyperswitch,reigh,comfyui,sd-scripts}-dev:latest`,
which are not pullable (ghcr answers `DENIED`) and have no published Dockerfile. Their layers are the bottom of
each task image's BuildKit history; `bases/<name>.Dockerfile` is that history turned back into a Dockerfile
(`tools/reconstruct.py` style, written out by hand because the steps are few):

| Base | Official base (from history) | Contents |
|---|---|---|
| hyperswitch-dev | `rust:1.82.0-slim-bookworm` | apt toolchain, juspay/hyperswitch clone, `cargo fetch`, claude 2.1.108 |
| reigh-dev | `ubuntu:24.04` | NodeSource 20 (pinned to 20.20.2-1nodesource1), banodoco/reigh-app clone, `npm ci` |
| comfyui-dev | `ubuntu:24.04` | venv, ComfyUI clone, CPU torch 2.6.0 stack, ruff/pylint |
| sd-scripts-dev | `python:3.12.8-slim-bookworm` | venv, kohya-ss/sd-scripts clone, CPU torch 2.6.0 stack |

Each clone is checked out at the default branch's last commit before the official base was built (the history
timestamps, 2026-06-07); tasks then fetch and check out their own `BASE_COMMIT`. pip installs are constrained to
the representative official task image's distributions. The task Dockerfiles' `FROM` is rewritten to
`bdqnghi/swe-together-base:<name>`.

## arm64 deviations

Every change is recorded in `arm64_rules.py` / `pins.py`, listed per task in `status.json` (`deviations`), and
visible in `swe-together-arm64.patch`.

* **Go toolchain tarballs** (26 tasks): `go<ver>.linux-amd64.tar.gz` -> `go<ver>.linux-arm64.tar.gz` (same version);
  cli-task-70c88c also verifies the tarball's sha256, replaced by the arm64 tarball's checksum from go.dev.
* **PyTorch CPU wheels**: download.pytorch.org/whl/cpu has no aarch64 `torchvision==0.21.0+cpu` (3 comfyui tasks and the
  comfyui/sd-scripts bases), `torch==2.5.1+cpu` or `torchvision==0.20.1+cpu` (unsloth-idefics3-finetune); the
  aarch64 wheels of the same versions without the local label are the CPU builds. `torch==2.6.0+cpu` exists for
  aarch64 and is unchanged.
* **Private bases** (26 tasks): `FROM` rewritten to the reconstructed `bdqnghi/swe-together-base:*` (see above).
* **Drift pins** (not arm64-specific, applied so the rebuild matches the official image): NodeSource nodejs (28 tasks),
  global npm packages such as pnpm (26), bun (15), pip constraints (24 tasks + comfyui/sd-scripts bases).

Nothing is emulated inside any image; no test or verifier file was changed.

## Local image removals

Only images with a verified remote copy and no container (running or stopped) using them were removed; the 20
pilot-subset task images and the four bases are kept locally for the Stage 1 pilot.

* Images this job created or pulled: each `bdqnghi/swe-together:<task>` tag after its push was confirmed on Docker Hub
  (except the pilot subset), and every official `ghcr.io/togetherbench/multi-user-turn-codebench/<task>:<tag>`
  image pulled for fingerprinting/official comparison (a public ghcr copy by construction), after use.
* Images from earlier ARM work (Docker Hub digest checked equal to the local RepoDigest before removal):

| Removed (UTC) | Image | Size | Remote check |
|---|---|---|---|
| 2026-09-26T14:27:35Z | `bdqnghi/terminal-bench-v4:takens-embedding-lean-verifier` | 10.40 GB | hub digest sha256:1b3591c905e6ca81034d1ec4916ae0bb51d60a672a561244435882bbeeec64d8 matches local |
| 2026-09-26T14:27:39Z | `bdqnghi/terminal-bench-v4:formal-crypto-verifier` | 6.64 GB | hub digest sha256:ca0d81473a57d4e0e1500ded18150d7af1ac6765b7ab25d6dc6fcc2b2a932df2 matches local |
| 2026-09-26T14:27:43Z | `bdqnghi/terminal-bench-v4:formal-crypto` | 6.38 GB | hub digest sha256:8506ced396fa9d05dc5f5cfcdbd61a03a13c21f1fbe63988c4268c01c3545b79 matches local |
| 2026-09-26T14:27:44Z | `bdqnghi/terminal-bench-v4:layout-config-recreation-verifier` | 4.88 GB | hub digest sha256:96159b1b12f1c81be8530fcbdb6aa5835cdab5b13453c691c9b9d0d607d69ebd matches local |
| 2026-09-26T14:27:45Z | `bdqnghi/terminal-bench-v4:layout-config-recreation` | 4.81 GB | hub digest sha256:4b6623bb81fe75196e41036cd54461f291c52a9ffd57f6d217b306957ffa2c54 matches local |
| 2026-09-26T14:27:47Z | `bdqnghi/terminal-bench-v4:cumulative-layout-shift-verifier` | 3.73 GB | hub digest sha256:0d8275a9d5ef30abe67447483d497eb9d3a96f3aceda21aa32860836f9f93ffa matches local |
| 2026-09-26T14:27:48Z | `bdqnghi/terminal-bench-v4:freecad-spring-clip-verifier` | 3.45 GB | hub digest sha256:2c09ce355175ef5a0b3de9c31cf9b95ce9d5a8b12864bd90039ea46b206284ab matches local |
| 2026-09-26T14:27:51Z | `bdqnghi/terminal-bench-v4:freecad-platform-drawing-verifier` | 3.45 GB | hub digest sha256:af179b0d27cc661ba857743bc4a28fadda565397c9e1bed3d9c271e07cb0f979 matches local |
| 2026-09-26T14:27:54Z | `bdqnghi/terminal-bench-v4:freecad-impeller-verifier` | 3.45 GB | hub digest sha256:99ce71622e1cdb62e6b44da92d69be74ed4130cafb1ab4cd8cf185e5cab9981f matches local |
| 2026-09-26T14:27:55Z | `bdqnghi/terminal-bench-v4:freecad-spring-clip` | 3.41 GB | hub digest sha256:954a3aa1495996314a2925d4c478fa56318e86e452418221a7a66d9b53ba51e4 matches local |
| 2026-09-26T14:27:58Z | `bdqnghi/terminal-bench-v4:freecad-platform-drawing` | 3.41 GB | hub digest sha256:d6b63c2cf172ad8448191d00b5639ee415c9dde732246fc4bcae12a646df4fae matches local |
| 2026-09-26T14:28:02Z | `bdqnghi/terminal-bench-v4:freecad-impeller` | 3.41 GB | hub digest sha256:954a3aa1495996314a2925d4c478fa56318e86e452418221a7a66d9b53ba51e4 matches local |
| 2026-09-26T14:28:03Z | `bdqnghi/terminal-bench-v4:nextjs-performance-verifier` | 3.21 GB | hub digest sha256:5cdc0a0a322f935461102c20c2659859ea791ef588b8fffbfa89a292920cc1fe matches local |

