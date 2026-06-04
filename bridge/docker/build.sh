#!/usr/bin/env bash
# Build the ArtLynk bridge SD-card image in Docker (works on macOS).
#
# The heavy Buildroot build runs inside a Docker *named volume* (a native ext4 in the Docker VM) —
# NOT a host bind mount. macOS bind mounts (gRPC-FUSE/virtiofs) corrupt tar extraction ("Directory
# renamed before its status could be extracted") and are very slow. The finished image is copied out
# to bridge/docker/images/. The bridge source is bind-mounted (small, read-only-ish).
#
#   bridge/docker/build.sh                 # configure (if needed) + build -> images/sdcard.img
#   bridge/docker/build.sh menuconfig      # interactive Buildroot config
#   bridge/docker/build.sh savedefconfig   # write trimmed config back to configs/
#   bridge/docker/build.sh shell           # drop into the build container (volume mounted)
#   bridge/docker/build.sh clean           # remove build output (keeps buildroot + dl cache)
#   bridge/docker/build.sh reset           # delete the whole build volume (full rebuild next time)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
BRIDGE="$(cd "$HERE/.." && pwd)"
IMG=artlynk-bridge-builder
VOL="${ARTLYNK_VOL:-artlynk-br}"               # named volume: buildroot + output + dl
BR_VERSION="${BR_VERSION:-2026.02}"            # Buildroot release/branch (LTS; gst 1.24 -> uvcsink)
OUT="$HERE/images"                             # finished sdcard.img lands here on the host
mkdir -p "$OUT"

echo "[*] building the builder image..."
docker build -t "$IMG" --build-arg UID="$(id -u)" --build-arg GID="$(id -g)" "$HERE"
docker volume create "$VOL" >/dev/null

# run a snippet inside /work/buildroot, with the volume + bridge source + output dir mounted
run() {
  docker run --rm -it \
    -v "$VOL:/work" \
    -v "$BRIDGE:/src/bridge" \
    -v "$OUT:/out" \
    "$IMG" bash -lc "
      set -e
      cd /work
      # (re)clone when the requested Buildroot version changes (keeps the /work/dl download cache).
      if [ ! -d buildroot/.git ] || [ \"\$(cat .br_version 2>/dev/null)\" != '$BR_VERSION' ]; then
        echo '[*] (re)cloning Buildroot $BR_VERSION (first run or version change)...'
        rm -rf buildroot output
        git clone --depth 1 --branch '$BR_VERSION' https://gitlab.com/buildroot.org/buildroot.git buildroot
        echo '$BR_VERSION' > .br_version
      fi
      cd buildroot
      $1
    "
}

BR='make BR2_EXTERNAL=/src/bridge O=/work/output BR2_DL_DIR=/work/dl'

case "${1:-build}" in
  build)
    # always (re)apply the defconfig so edits to configs/artlynk_bridge_defconfig take effect
    run "$BR artlynk_bridge_defconfig
         $BR
         if cp -f /work/output/images/sdcard.img /out/ 2>/dev/null; then
             # also expose the individual boot artifacts for over-SSH deploys (deploy-ssh.sh)
             cp -f /work/output/images/Image /work/output/images/boot.scr \
                   /work/output/images/meson-g12a-radxa-zero.dtb /out/ 2>/dev/null || true
             echo '[*] copied -> bridge/docker/images/ (sdcard.img + Image + dtb + boot.scr)'
         else echo '[!] build finished but no sdcard.img (check the log above)'; fi"
    ;;
  defconfig)     run "$BR artlynk_bridge_defconfig" ;;
  menuconfig)    run "[ -f /work/output/.config ] || $BR artlynk_bridge_defconfig; $BR menuconfig" ;;
  savedefconfig) run "$BR savedefconfig BR2_DEFCONFIG=/src/bridge/configs/artlynk_bridge_defconfig" ;;
  shell)         run "bash" ;;
  clean)         run "rm -rf /work/output; echo 'cleaned /work/output (kept buildroot + dl cache)'" ;;
  reset)         docker volume rm "$VOL" >/dev/null && echo "removed volume $VOL (full rebuild next time)" ;;
  *) echo "usage: $0 [build|menuconfig|savedefconfig|defconfig|shell|clean|reset]"; exit 1 ;;
esac
