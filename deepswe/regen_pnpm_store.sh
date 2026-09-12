#!/bin/sh
# drizzle-orm-window-function-builders: its pnpm lockfile pins drizzle-kit-0.25.0-b1faa33.tgz, which npm has removed.
# The official (amd64) task image still holds that package in pnpm's content-addressable store; the arm64 Dockerfile
# ADDs this tarball so `pnpm install --frozen-lockfile --prefer-offline` resolves it while native modules rebuild for
# aarch64. Run from the deep-swe checkout root.
set -eu
IMG=public.ecr.aws/d3j8x8q7/swe-bench-202605:kh70cshdjenz3z5gq2wrqzhjmn82xbb0-v1.1
OUT=tasks/drizzle-orm-window-function-builders/environment/pnpm-store.tar
docker pull --platform linux/amd64 "$IMG"
docker run --rm --platform linux/amd64 --entrypoint tar "$IMG" cf - -C / root/.local/share/pnpm/store > "$OUT"
ls -la "$OUT"
