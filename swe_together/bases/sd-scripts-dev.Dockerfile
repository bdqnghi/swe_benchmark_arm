# arm64 reconstruction of ghcr.io/togetherbench/togetherbench/sd-scripts-dev:latest (private, no Dockerfile published)
# from the BuildKit history of ghcr.io/togetherbench/multi-user-turn-codebench/sd-scripts-fp8-lumina:a6e2437846b5.
# Base: python 3.12.8 slim on Debian bookworm = python:3.12.8-slim-bookworm.
# The clone is checked out at the default branch's last commit before the base was built (2026-06-07T07:20:49Z).
# arm64: download.pytorch.org/whl/cpu has no aarch64 torchvision 0.21.0+cpu; its aarch64 0.21.0 wheel is the CPU build.
FROM python:3.12.8-slim-bookworm
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends     git curl ca-certificates build-essential tmux     libgl1 libglib2.0-0     && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /workspace /logs/verifier
WORKDIR /workspace
RUN git clone https://github.com/kohya-ss/sd-scripts.git /workspace/sd-scripts && \
    cd /workspace/sd-scripts && git checkout -q $(git rev-list -1 --before="2026-06-07T07:20:49Z" HEAD)
RUN python3 -m venv /workspace/venv
ENV PATH=/workspace/venv/bin:/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
RUN pip install --no-cache-dir     --extra-index-url https://download.pytorch.org/whl/cpu     torch==2.6.0+cpu torchvision==0.21.0     accelerate==1.6.0     transformers==4.54.1     "diffusers[torch]==0.32.1"     ftfy==6.3.1     opencv-python==4.10.0.84     einops==0.7.0     safetensors==0.4.5     toml==0.10.2     voluptuous==0.15.2     huggingface-hub==0.34.3     imagesize==1.4.1     numpy==1.26.4     rich==14.1.0     sentencepiece==0.2.1     tensorboard==2.19.0     pytest==8.3.4     pytest-timeout==2.3.1     tqdm
RUN cd /workspace/sd-scripts && pip install --no-cache-dir -e . 2>/dev/null || true
ENV PYTHONPATH=/workspace/sd-scripts:
RUN printf '%s\n' 'export PATH=/workspace/venv/bin:$PATH'     'export PYTHONPATH=/workspace/sd-scripts${PYTHONPATH:+:$PYTHONPATH}'     > /etc/profile.d/sd_scripts_venv.sh &&     chmod 0644 /etc/profile.d/sd_scripts_venv.sh
RUN git config --global user.email "agent@test.com" &&     git config --global user.name "Test Agent"
RUN curl -fsSL https://claude.ai/install.sh | bash -s -- 2.1.108
ENV PATH=/root/.local/bin:/workspace/venv/bin:/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
WORKDIR /workspace/sd-scripts
