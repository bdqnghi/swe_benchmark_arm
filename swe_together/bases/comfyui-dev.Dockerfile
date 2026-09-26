# arm64 reconstruction of ghcr.io/togetherbench/togetherbench/comfyui-dev:latest (private, no Dockerfile published)
# from the BuildKit history of ghcr.io/togetherbench/multi-user-turn-codebench/comfyui-gemma3-sliding-window:e1932271906b.
# The clone is checked out at the default branch's last commit before the base was built (2026-06-07T07:28:55Z).
# arm64: download.pytorch.org/whl/cpu has no aarch64 torchvision 0.21.0+cpu; its aarch64 0.21.0 wheel is the CPU build.
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
RUN apt-get update && apt-get install -y --no-install-recommends     git curl ca-certificates     python3 python3-pip python3-venv     build-essential tmux     && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /workspace /logs/verifier
WORKDIR /workspace
RUN git clone https://github.com/comfyanonymous/ComfyUI.git /workspace/ComfyUI && \
    cd /workspace/ComfyUI && git checkout -q $(git rev-list -1 --before="2026-06-07T07:28:55Z" HEAD)
RUN python3 -m venv /workspace/venv
ENV PATH=/workspace/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ENV VIRTUAL_ENV=/workspace/venv
RUN pip install --no-cache-dir     --extra-index-url https://download.pytorch.org/whl/cpu     torch==2.6.0+cpu     torchvision==0.21.0     transformers==4.47.1     safetensors==0.5.2     aiohttp==3.11.11     einops==0.8.0     pyyaml==6.0.2     Pillow==11.1.0     scipy==1.15.1     tqdm==4.67.1     psutil==6.1.1     tokenizers==0.21.0     sentencepiece==0.2.0     spandrel==0.4.1     pytest==8.3.4     pytest-timeout==2.3.1     opencv-python-headless==4.10.0.84     && pip cache purge 2>/dev/null;     pip uninstall -y     nvidia-cublas-cu12 nvidia-cuda-cupti-cu12 nvidia-cuda-nvrtc-cu12     nvidia-cuda-runtime-cu12 nvidia-cudnn-cu12 nvidia-cufft-cu12 nvidia-curand-cu12     nvidia-cusolver-cu12 nvidia-cusparse-cu12 nvidia-nccl-cu12 nvidia-nvjitlink-cu12     nvidia-nvtx-cu12 triton 2>/dev/null; true
RUN ln -sf "$(which python3)" /usr/local/bin/python &&     pip install --no-cache-dir ruff==0.9.5 pylint==3.3.3
RUN printf '%s\n' 'export PATH=/workspace/venv/bin:$PATH'     'export PYTHONPATH=/workspace/ComfyUI${PYTHONPATH:+:$PYTHONPATH}'     > /etc/profile.d/comfyui_venv.sh &&     chmod 0644 /etc/profile.d/comfyui_venv.sh &&     ln -sf /workspace/venv/bin/python3 /workspace/venv/bin/python 2>/dev/null || true
RUN sed -i 's/if args\.cpu:/if args.cpu or not torch.cuda.is_available():/'     /workspace/ComfyUI/comfy/model_management.py 2>/dev/null || true
ENV PYTHONPATH=/workspace/ComfyUI
RUN git config --global user.email "agent@test.com" &&     git config --global user.name "Test Agent"
RUN curl -fsSL https://claude.ai/install.sh | bash -s -- 2.1.108
ENV PATH=/root/.local/bin:/workspace/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
WORKDIR /workspace/ComfyUI
