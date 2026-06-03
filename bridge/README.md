# ArtLynk Bridge — goggle → HDMI, a clean Buildroot project

A tiny embedded‑Linux appliance: a **Radxa Zero** that pulls the BetaFPV P1 HD goggle's USB video
tap and paints it on **HDMI**, with hardware H.265 decode. No desktop, no distro — just U‑Boot, a
Linux kernel, a BusyBox rootfs, and one small service. This folder is a self‑contained **Buildroot
`br2-external` tree**, which is *the* standard way to build a custom product on top of Buildroot
without forking Buildroot itself.

```
[ goggle ]  --USB-C (CDC-ECM)-->  [ Radxa Zero bridge ]  --micro-HDMI-->  [ monitor ]
 venc8 tap                         decode H.265 + KMS
 tcp://10.55.0.1:9000              (this Buildroot image)
```

## Why a Radxa Zero (and not the Pi Zero 2 W)
Our stream is **1080p60 H.265**. The Radxa Zero's Amlogic **S905Y2** has a hardware HEVC decoder;
the Pi Zero 2 W has none (H.264 only) and can't software‑decode this in real time. HDMI on the Radxa
comes out the **micro‑HDMI** connector (its USB‑C ports are USB‑only — no DisplayPort Alt Mode, so a
USB‑C→HDMI dongle won't work). You'll need a **micro‑HDMI → HDMI** cable.

Wiring: USB‑C #1 = power · USB‑C #2 = USB host → goggle · micro‑HDMI → monitor.

## What `br2-external` is (the 1‑minute version)
Buildroot is a big Kconfig+Make system that downloads and cross‑compiles U‑Boot, the kernel, and a
root filesystem, then packs an image. You **don't fork it** — you point it at an *external tree*
(this folder) that adds your board config, your overlay files, and your custom packages. You build
with `make BR2_EXTERNAL=<this folder> …`. Everything product‑specific lives here; Buildroot stays
pristine.

## Layout (what each piece is)
```
bridge/
├── external.desc                     # names this external tree -> BR2_EXTERNAL_ARTLYNK_BRIDGE_PATH
├── external.mk                       # pulls in our package .mk files
├── Config.in                         # adds our packages to Buildroot's menuconfig
├── configs/
│   └── artlynk_bridge_defconfig      # THE config: board, kernel, packages, rootfs (start here)
├── board/radxa-zero/
│   ├── linux.fragment                # extra kernel options (CDC-ECM host, DRM, HEVC decoder)
│   ├── genimage.cfg                  # how to lay out the SD-card image (bootloader + partitions)
│   ├── post-image.sh                 # runs genimage after the build to produce sdcard.img
│   └── rootfs-overlay/               # files copied verbatim onto the rootfs
│       └── etc/
│           ├── artlynk/bridge.conf   # runtime config (goggle IP/port, hw vs sw decode)
│           └── init.d/
│               ├── S40usbnet         # bring up the USB-ECM link to the goggle
│               └── S90artlynk        # launch the viewer service
└── package/artlynk-view/
    ├── Config.in / artlynk-view.mk   # a normal Buildroot package…
    └── src/artlynk-view              # …that installs our viewer loop (GStreamer → KMS)
```
This is exactly the "what lives in git" picture from our earlier chat: a **defconfig**, a **board
dir** (kernel fragment + image layout + overlay), and a **custom package**. The heavy lifting
(compiling hundreds of components) is Buildroot's; we just supply the product glue.

## Build it (Docker — works on macOS)
Buildroot needs Linux, so we build in a container. The helper handles everything (clones Buildroot,
configures, builds) and persists the toolchain/downloads between runs:
```sh
bridge/docker/build.sh              # configure + build  ->  bridge/docker/images/sdcard.img
bridge/docker/build.sh menuconfig   # tweak the config interactively
bridge/docker/build.sh savedefconfig# write trimmed config back to configs/artlynk_bridge_defconfig
bridge/docker/build.sh shell        # poke around in the build container
bridge/docker/build.sh clean        # wipe the build output (keeps buildroot + download cache)
bridge/docker/build.sh reset        # delete the whole build volume (full rebuild next time)
```
- **First build is long** (it builds a cross‑toolchain + everything from source) — later builds are
  incremental. The heavy build lives in a Docker **named volume** (`artlynk-br`) — a native ext4 in
  the Docker VM, *not* a macOS bind mount. (Bind mounts corrupt tar extraction and are slow; the
  named volume avoids both.) Only the finished `sdcard.img` is copied out to `bridge/docker/images/`.
- Flash the result to a microSD (`dd`/balenaEtcher) — or, for the no‑HDMI UVC test mode, just plug
  the Radxa into your Mac (see below).

## Flash & first boot (M1) — expect to iterate
First boot on a fresh board is **real bring‑up**: flash, watch the serial console, fix one thing,
rebuild, repeat. That loop *is* the embedded experience.

`sdcard.img` is a complete disk image, so flashing = writing it to the microSD (same idea as a Pi
OS image — no Radxa‑specific tool needed):
- **balenaEtcher** — "Flash from file" → `sdcard.img` → pick the card → Flash. (Easiest, verified.)
- **Raspberry Pi Imager** — Choose OS → "Use custom" → `sdcard.img` → pick the card → Write.
- **`dd`** (careful with the device name!):
  ```sh
  sudo dd if=bridge/docker/images/sdcard.img of=/dev/diskN bs=4M conv=fsync
  ```
**Verify boot without HDMI** via the **serial console** (you already have 3.3 V USB‑UART adapters):
Radxa Zero UART is on the GPIO header — connect GND/TX/RX, open it at **115200**, and watch U‑Boot →
kernel → the `S40usbnet`/`S90artlynk` messages. That confirms **M1** with no monitor and no webcam.

Two known iteration points (flagged in the files):
- **Amlogic FIP bootloader.** `post-image.sh` packs it via LibreELEC's `amlogic-boot-fip`. Its packer
  is **x86_64**, so on your Apple‑Silicon Mac it needs x86 emulation in the container (Docker
  Desktop's "Use Rosetta" — yes, the same Rosetta from your crash log). If it won't pack on the Mac,
  run that one step on an x86 box/CI, or drop a known‑good Radxa/Armbian `u-boot.bin.sd.bin` into the
  images dir and the script skips it. (Building the kernel/rootfs in Docker is native arm64 and fine.)
- **Boot device/offset.** `boot.cmd` (mmc index, rootfs `mmcblk0p2`) and the bootloader `offset=512`
  in `genimage.cfg` are the first things to check against the serial log if it doesn't boot.

## Boot & run flow (on the device)
1. U‑Boot → Linux kernel → BusyBox `init` runs the `/etc/init.d/S*` scripts in order.
2. **S40usbnet** finds the goggle's CDC‑ECM interface (the goggle is the USB *device*, we're the
   *host*) and gives ourselves `10.55.0.2/24`.
3. **S90artlynk** starts `artlynk-view`, which waits for `tcp://10.55.0.1:9000`, then runs a
   GStreamer pipeline straight to **KMS/DRM** (the HDMI framebuffer) — no X/Wayland. It reconnects
   forever, so it "just works" when the goggle powers on.

## Milestones (how we'll actually get there)
- **M1 — it boots:** Buildroot image boots on the Radxa, HDMI shows the console / a test pattern.
- **M2 — it sees the goggle:** USB‑ECM link up, `artlynk-view` pulls the tap and displays it with
  **software** decode (`DECODE=sw` in `bridge.conf`) — proves the whole pipeline end‑to‑end.
- **M3 — hardware decode:** switch to `DECODE=hw` (V4L2 stateless HEVC on the Amlogic VPU) for
  low‑latency 1080p60.

## The two genuinely fiddly parts (flagged honestly)
1. **Amlogic bootloader.** Amlogic SoCs need a signed firmware chain (FIP: BL2/BL31/BL33 + the
   `aml_encrypt_g12a` packing) written to a fixed SD offset. `genimage.cfg`/`post-image.sh` leave a
   clearly‑marked TODO for dropping in the Amlogic blobs (CoreELEC/LibreELEC and Buildroot's
   `board/amlogic/` are good references). This is the usual "vendor‑boot is messy" tax.
2. **HEVC on a mainline kernel.** The Amlogic `meson-vdec` V4L2 decoder is solid for H.264/VP9;
   **HEVC on the S905Y2 is the thing to verify** (`linux.fragment` enables it). If mainline isn't
   ready, the fallback is a CoreELEC‑style Amlogic kernel. M2's software path lets us make progress
   either way.

Start at `configs/artlynk_bridge_defconfig` and `package/artlynk-view/src/artlynk-view` — those two
files are the soul of the project.
