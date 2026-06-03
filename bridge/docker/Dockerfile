# Buildroot build environment for the ArtLynk bridge. Buildroot needs Linux; this lets you build
# from macOS/Windows via Docker. Runs as a non-root user matching your host UID so the output files
# aren't root-owned.
FROM debian:bookworm

# Buildroot's host-side build dependencies (it builds its own cross-toolchain + host tools).
RUN apt-get update && apt-get install -y --no-install-recommends \
        bash build-essential git wget curl ca-certificates \
        cpio unzip rsync bc bzip2 xz-utils file \
        python3 sed gawk diffutils findutils \
        libncurses-dev libssl-dev \
        qemu-user libc6-amd64-cross \
    && rm -rf /var/lib/apt/lists/*
# qemu-user provides qemu-x86_64; libc6-amd64-cross provides /usr/x86_64-linux-gnu/ guest libs —
# together they let the Amlogic FIP packer's x86_64 aml_encrypt tool run on this arm64 build host.

ARG UID=1000
ARG GID=1000
RUN groupadd -g ${GID} build 2>/dev/null || true \
 && useradd -m -u ${UID} -g ${GID} -s /bin/bash build 2>/dev/null || true
USER build
WORKDIR /work
