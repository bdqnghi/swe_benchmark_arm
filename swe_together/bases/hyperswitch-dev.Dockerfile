# arm64 reconstruction of ghcr.io/togetherbench/togetherbench/hyperswitch-dev:latest (private, no Dockerfile
# published) from the BuildKit history of ghcr.io/togetherbench/multi-user-turn-codebench/hyperswitch-8084:4d27613167a4.
# Base rootfs + rust install (74.8MB + 734MB installing gcc/libc6-dev/wget, RUST_VERSION=1.82.0) = rust:1.82.0-slim-bookworm.
# The clone is checked out at the default branch's last commit before the base was built (2026-06-07T07:15:20Z).
FROM rust:1.82.0-slim-bookworm
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends     git curl ca-certificates build-essential tmux pkg-config libssl-dev     && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /workspace /logs/verifier
WORKDIR /workspace
RUN git clone https://github.com/juspay/hyperswitch.git /workspace/hyperswitch && \
    cd /workspace/hyperswitch && git checkout -q $(git rev-list -1 --before="2026-06-07T07:15:20Z" HEAD)
RUN cd /workspace/hyperswitch && cargo fetch 2>/dev/null || true
RUN printf '%s\n' 'export PATH=/usr/local/cargo/bin:$PATH'     'export CARGO_HOME=${CARGO_HOME:-/usr/local/cargo}'     'export RUSTUP_HOME=${RUSTUP_HOME:-/usr/local/rustup}'     > /etc/profile.d/cargo.sh && chmod 0644 /etc/profile.d/cargo.sh
RUN for bin in cargo rustc rustup rustdoc rustfmt; do         if [ -x /usr/local/cargo/bin/$bin ]; then             ln -sf /usr/local/cargo/bin/$bin /usr/local/bin/$bin;         fi;     done
RUN cd /workspace/hyperswitch &&     timeout 1800 cargo check --workspace --bins 2>&1 | tail -2 || true
RUN git config --global user.email "agent@test.com" &&     git config --global user.name "Test Agent"
RUN curl -fsSL https://claude.ai/install.sh | bash -s -- 2.1.108
ENV PATH=/root/.local/bin:/usr/local/cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
WORKDIR /workspace/hyperswitch
