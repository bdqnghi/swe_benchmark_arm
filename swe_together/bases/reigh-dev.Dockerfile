# arm64 reconstruction of ghcr.io/togetherbench/togetherbench/reigh-dev:latest (private, no Dockerfile published)
# from the BuildKit history of ghcr.io/togetherbench/multi-user-turn-codebench/reigh-preset-data-flow:7c7d9842c79d.
# The clone is checked out at the default branch's last commit before the base was built (2026-06-07T07:18:54Z).
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends     git curl ca-certificates build-essential tmux unzip     && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash -     && apt-get install -y nodejs     && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /workspace /logs/verifier
WORKDIR /workspace
RUN git clone https://github.com/banodoco/reigh-app.git /workspace/repo && \
    cd /workspace/repo && git checkout -q $(git rev-list -1 --before="2026-06-07T07:18:54Z" HEAD)
RUN cd /workspace/repo && npm ci --ignore-scripts 2>/dev/null || true
RUN git config --global user.email "agent@test.com" &&     git config --global user.name "Test Agent"
RUN curl -fsSL https://claude.ai/install.sh | bash -s -- 2.1.108
ENV PATH=/root/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
WORKDIR /workspace/repo
