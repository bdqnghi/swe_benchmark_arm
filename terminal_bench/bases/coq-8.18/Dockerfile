# arm64 stand-in for coqorg/coq:8.18 (which is published for amd64 only). Mirrors that image's layout: user `coq`
# (uid 1000, passwordless sudo), opam switch `4.13.1+flambda` in /home/coq/.opam with coq 8.18.0 installed.
FROM debian:bookworm
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        opam build-essential m4 git curl ca-certificates rsync unzip bubblewrap pkg-config libgmp-dev sudo \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 -s /bin/bash coq && echo 'coq ALL=(ALL) NOPASSWD:ALL' >> /etc/sudoers
USER coq
WORKDIR /home/coq
RUN opam init --bare --disable-sandboxing -y -a \
    && opam switch create 4.13.1+flambda ocaml-variants.4.13.1+options ocaml-option-flambda -y \
    && opam install -y coq.8.18.0 \
    && opam clean -a -c -s --logs \
    && rm -rf /home/coq/.opam/repo /home/coq/.opam/download-cache
ENV PATH=/home/coq/.opam/4.13.1+flambda/bin:/home/coq/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
RUN coqc --version
