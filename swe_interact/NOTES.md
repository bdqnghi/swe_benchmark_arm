# SWE-Interact on arm64

Native `linux/arm64` images for [SWE-Interact](https://github.com/scaleapi/SWE-Interact) (commit `b32f98c`,
75 tasks, each in a multi-turn and a single-turn variant), built and validated on the GB10 host
(aarch64, 20 cores). Status per task: `status.json` (task images) and `rf_base_status.json` (reconstructed RF
bases); a summary table is at the end of this file.

## Images

| Image | What |
|---|---|
| `bdqnghi/swe-interact:<task>` | task environment (the multi-turn `environment/Dockerfile`, which is the single-turn one plus `/usr/local/bin/repo_exec_server.py`; both variants use it) |
| `bdqnghi/swe-interact-base:rf_<org>_<repo>_<id>` | arm64 reconstruction of the amd64-only `ghcr.io/scaleapi/swe-atlas:swe_atlas_RF_<org>_<repo>_<id>_1.0` base of each `rf_*` task |
| `bdqnghi/sweap-images:<tag>` | existing arm64 SWE-bench Pro images (this repository's `swebench_pro/`), the base of the `swebenchpro_*` tasks |

`<task>` is the task directory name, except that names longer than Docker's 128-character tag limit (the longest is
132 characters) drop the `instance_` infix (`build.py:tag_for()`). `status.json` records the exact image name of every task.

## Running with Harbor (local Docker, no Modal)

```
cd <SWE-Interact checkout> && git checkout b32f98c
git apply <this repo>/swe_interact/swe-interact-arm64.patch      # or use swe_interact/tasks_arm64 from make_tree.py
export LITELLM_API_KEY=... LITELLM_BASE_URL=... SIM_USER_MODEL=...   # simulated user (multi-turn)
harbor run -p "$PWD/data/multiturn" -e docker --no-force-build -a <agent> -m <model> -n 4 -o "$PWD/results" \
    --env-file harbor/.env
```

* The patch sets `[environment] docker_image = "bdqnghi/swe-interact:<tag>"` in every `task.toml` (both variants) and
  rewrites each `environment/Dockerfile` to its arm64 recipe (so `--force-build` also builds natively, except the
  generated inputs noted below). The verifier runs in the agent container (shared mode) for all 75 tasks, so no
  `[verifier.environment]` entry is needed. Use absolute `-p`/`-o` paths, as for Terminal-Bench.
* The upstream run configs pass `--force-build`; with the patch, `--no-force-build` pulls the prebuilt arm64 image.
* Multi-turn tasks start a `user-server` sidecar built from `environment/user-server` (`python:3.12-slim`, multi-arch)
  on every trial. It calls the simulated-user LLM; it needs no change for arm64.
* **Harbor 0.23.0 bug on local Docker, multi-turn only**: the final step's verifier writes
  `/logs/verifier/user-server/` as root in the bind-mounted log directory, and Harbor then fails to move it into
  `steps/<name>/verifier/` (`PermissionError: .exported`), marking the trial as an exception. Single-step trials
  chown the logs on `stop()`, multi-step trials do not between steps. `harbor-0.23.0-multistep-log-ownership.patch`
  (apply in the Harbor `site-packages` directory with `patch -p1`) calls `prepare_logs_for_host()` before each step's
  outputs are archived. Verified: a 5-step trial with the `nop` agent completes without exception after the patch
  and fails with it before. Modal is unaffected.
* Docker Hub pull quota: every multi-turn trial resolves `python:3.12-slim` for the sidecar build; on a shared host
  the account's hourly quota runs out (429). Running Harbor with `DOCKER_CONFIG` pointing at an empty config
  (anonymous pulls, per-IP quota) avoided it here.

## How each family was built

### swebenchpro_* (25)

`FROM jefzda/sweap-images:<tag>` -> `FROM bdqnghi/sweap-images:<tag>` (same tag; all 25 exist in this repository's
SWE-bench Pro set), rest of the Dockerfile unchanged (git reset to the base commit, uv, `repo_exec_server.py`).

### deepswe_* (25)

`task.toml` names a prebuilt amd64 image (`public.ecr.aws/d3j8x8q7/swe-bench-202605:<ext_id>`), but the published run
configs use `--force-build`, so what Harbor actually runs is the Dockerfile: `FROM public.ecr.aws/x8v8d7g8/mars-base:latest`
(multi-arch) + clone at the base commit + dependency install + `repo_exec_server.py`. Its BuildKit history
(checked for `httpx-streaming-json-iteration`) is exactly that Dockerfile, so the prebuilt image adds nothing beyond
the dependency versions resolved at its build time. These Dockerfiles are the older SWE-Interact snapshot of the
DeepSWE tasks, not the v1.1 ones this repository's `deepswe/` targets (no CTRF reporters, no git time-travel), so the
arm64 builds use SWE-Interact's own Dockerfiles; fixes are carried over only where needed (below).

### rf_* (25, SWE Atlas refactoring)

The `ghcr.io/scaleapi/swe-atlas` images are amd64-only and their history is scrubbed (every layer is "created by
buildkit"), so `tools/reconstruct.py` does not apply. `rf/build_rf_base.py` reconstructs them from the filesystem:

1. **base**: exact `diff_id` prefix match against candidate public images derived from the image's ENV and
   `/etc/os-release` (`rf/find_base.py`; e.g. trufflehog -> `golang:1.22.12-bookworm`, scapy ->
   `python:3.8.20-slim-bullseye`); the arm64 build uses the same multi-arch tag (pulled through `mirror.gcr.io`,
   identical digests).
2. **packages**: packages in the final dpkg (or Alpine apk) database but not in the base are installed natively;
   names without an arm64 candidate (e.g. `libquadmath0`, `*-x86-64-*`) are skipped and printed in the build log.
3. **transplant**: every file added after the base layers is copied from the official image, except dpkg-owned files
   (reinstalled natively), ELF objects and `ar` archives outside Go module / test-fixture trees (architecture
   specific; listed in `report.json`), Python site-packages (reinstalled, step 4), rustup toolchains and cargo
   binaries (step 5), caches/logs/apt state and uv-managed interpreters. This carries the repository (squashed
   single-commit `.git`), Go module caches, configuration, entrypoints and data unchanged.
4. **python**: each site-packages directory is reinstalled with its own interpreter at the exact official versions
   (`--no-deps`); editable installs are reinstalled from the transplanted source; the verifier venv `/opt/venv`
   (uv Python 3.12 with `openai`) is recreated with uv. Versions without aarch64 wheels are built from source (build
   tools added only in that case).
5. **native tools**: Go binaries are reinstalled from their embedded build info (`go install <pkg>@<version>`, e.g.
   `gotestsum v1.12.0`), or rebuilt from the workspace when they were built from the task repository itself
   (`k6`); rustup toolchains are reinstalled at the same versions and `cargo install`ed tools from `.crates2.json`.
6. **config**: ENV, WORKDIR, USER, LABELs, ENTRYPOINT/CMD copied from the official image config.

The task image is then the task's own Dockerfile on top (`repo_exec_server.py` + the verifier's `openai`/`python-dotenv`).
Compiled build trees inside the workspace (suricata, MariaDB, netdata) are not transplanted; the RF validator
(`master_validator_script.sh`) configures and builds C/C++ projects itself before running tests.

## Validation

`validate.py <task>` reproduces Harbor's oracle + verifier for the single-turn variant without Harbor or an LLM:
start the image, copy `solution/` to `/solution` and `tests/` to `/tests`, run `solve.sh`, then `tests/test.sh`, and
read `/logs/verifier/reward.txt`. The single-turn and multi-turn variants ship byte-identical `tests/` and `solution/`
directories, and the multi-turn final step's `canonical_test.sh` is `tests/test.sh` plus a diff-metrics block; the
multi-turn final step additionally checks the agent's commit structure (implementation commit, test commit,
`/tmp/base_commit.txt`), which the reference solution does not produce, so the oracle cannot pass the multi-turn
variant by construction.

* **swebenchpro_\***: reward 1 (the official SWE-bench Pro `run_script.sh` + `parser.py`; all FAIL_TO_PASS and
  PASS_TO_PASS tests pass with the gold patch).
* **deepswe_\***: reward 1 (hidden `test.patch`, baseline and new test suites both pass with the reference solution).
* **rf_\***: the RF verifier's reward is `tests_reward AND rubric.must_have_pass`; the rubric half is LLM-graded
  (`evaluate_rubrics.py`, `EVAL_MODEL`, default Anthropic Opus 4.5) and was **not** run. Validation here is the
  deterministic half: `master_validator_script.sh --mode agent` runs the full test suite on the base commit and on
  base + gold patch + hidden test patch, and `tests_reward = 1.0` means no pass->fail or missing->fail transition among
  the task's relevant tests. A vacuous pass is rejected: at least one relevant test must have passed after the
  mutation. What cannot be validated without the LLM: whether the gold refactoring satisfies the rubric (it is the
  reference, so upstream presumably scored it as passing) and whether the rubric grader behaves the same (it runs
  in the image's `/opt/venv` with `openai`, architecture-independent).

Harbor itself was exercised on the arm64 tree: `harbor run -a oracle -e docker --no-force-build` on the single-turn
variant of `swebenchpro_instance_flipt-io__flipt-e5fe37...` and `deepswe_helm-array-merge-strategies` gives reward 1.0
for both; a multi-turn trial with the `nop` agent runs all five steps (reward 0 as expected, with the Harbor patch).

## Per-task fixes and deviations

* `deepswe_drizzle-orm-window-function-builders`: the lockfile pins `drizzle-kit-0.25.0-b1faa33.tgz`, which npm has
  removed. `rules/<task>/prepare.sh` copies pnpm's content-addressable store out of the official prebuilt image
  (`docker create` + `docker cp`, no emulation) and the Dockerfile ADDs it before `pnpm install --frozen-lockfile
  --prefer-offline` (same fix as `deepswe/regen_pnpm_store.sh`). `--force-build` from the patched tree needs
  `prepare.sh` to be run first (the store tarball is not in the patch).
* `deepswe_fastapi-deprecation-response-headers`: starlette 1.x (resolved today) deprecates httpx in its TestClient
  and the tests error at collection. `tools/pin_python.py` pins every Python distribution to the version installed in
  the official prebuilt image (`rules/<task>/files/constraints.txt`, applied via `PIP_CONSTRAINT`/`UV_CONSTRAINT`).
* `swebenchpro_instance_qutebrowser__qutebrowser-96b99780...` (two fixes, `rules/<task>/Dockerfile.append`): the arm64
  SWE-bench Pro base uses Ubuntu focal's PyQt5 5.14.1 / Qt 5.12.8 (no aarch64 PyQt5 wheels exist on PyPI; the official
  image has PyPI PyQt5 5.15.2 / Qt 5.15.2).
  1. focal's sip 4 is the top-level `sip` module, while qutebrowser at this commit imports `from PyQt5 import sip`;
     the same extension is exposed as `PyQt5.sip`.
  2. Qt 5.12's `offscreen` platform plugin switches to an X11/GLX backend whenever `DISPLAY` is set (the task's
     `run_script.sh` starts Xvfb and exports `DISPLAY=:99`); creating QtWebEngine's global GL share context there
     aborts the process ("Could not initialize GLX"), so pytest dies at the first test that needs a `QApplication`
     and the verifier sees 124 of 178 required tests. The same abort happens on the plain `bdqnghi/sweap-images` base;
     the official amd64 image passes the verifier under qemu (reward 1). The plugin is rebuilt from the Qt 5.12.8
     sources (`qt/qtbase` tag v5.12.8, C++ unmodified, `offscreen.pro` without the X11/GLX branch, i.e. what Qt builds
     without xlib); the build packages are removed again.
* `rf_task-694b4b99829f00e24fd118a1` (scapy), upstream quirk: the swe-atlas image puts the rubric venv `/opt/venv`
  (uv CPython 3.12) first on `PATH`, so the RF validator runs scapy's `UTscapy` under Python 3.12, where the vendored
  `six` at this commit fails (`No module named 'scapy.modules.six.moves'`). No relevant test runs, and `tests_reward`
  is 1 vacuously, on the official amd64 image under qemu exactly as on arm64. `validate.py` normally rejects a vacuous
  pass; it accepts it when the result equals the official image's, recorded in
  `rules/<task>/official_amd64_validation.json`.
* Upstream task Dockerfiles on bullseye-based swe-atlas images (scapy) no longer build as published: the verifier
  layer's `apt-get update; apt-get install python3-pip` hits the removed bullseye pools (404). The arm64 bases carry
  the archive/snapshot sources, so the task Dockerfile builds unchanged on top of them.
* RF bases on Debian bullseye: bullseye left `deb.debian.org` at the end of August 2026 (security pool already 404,
  not yet on `archive.debian.org`): main/updates come from `archive.debian.org`, security from
  `snapshot.debian.org/archive/debian-security/20260801T000000Z`.

## Operations

* `build.py [--family F] [--task T ...] [--jobs N]`: prepare, build, validate, push, then remove the local tag;
  resumable from `status.json` (a rerun skips pushed+validated tasks, retries failed builds; `--force` rebuilds).
  Network/DNS failures and Docker Hub 429s are retried; builds pull anonymously (`work/anon-docker-config`) because
  the account's pull quota is shared with other jobs on the host.
* `rf/run_rf.py [--task T ...]`: base reconstruction + push, then `build.py` for the task.
* `make_tree.py`: writes `tasks_arm64/{multiturn,singleturn}` and `swe-interact-arm64.patch`.
* `validate.py <task> [--image REF --platform linux/amd64]`: validation alone; with an official image under qemu to
  check whether a failure is upstream.
* A build does not start while the disk has less than 110 GB free.
* Local image removal: only images this job built or pulled, only after their push/remote copy is verified
  (`RepoDigests` entry from the push, or a registry lookup) and when no container uses them. Every removal is
  logged in `cleanup_log.jsonl` (image, size, how the remote copy was verified).
