# LocalDing

A local-first, cloud-free firmware patch for compatible IP/Wi-Fi doorbells,
featuring native MQTT and Home Assistant discovery.

Tested on:

* Reolink Video Doorbell WiFi-W (`DB_566128M5MP_W_W`)

This adds a few features to the firmware:

* Removal of cloud services
  * Prohibition on sending network traffic to non-private addresses
* MQTT client which publishes
  * Motion and object detection
  * Doorbell press events
* Web-based config for MQTT, SSH authorized_keys, and NTP server.
* Public-key-only SSH access as `root` on port `2222`.

## Firmware input

The supported factory release is `7106_2609241897`, SHA-256
`51b2698da3b0e1ceff3573bb565895f862ab1a1f64b12d786c2f372fa7e67e2e`. Obtain it
separately. This repository, CI, release bundle and container contain no
factory image, extracted vendor libraries or firmware download mechanism. Only
this verified hardware/release is accepted.

## How to patch the factory image

Install Docker and place the supported factory `.pak` file in a local `input`
directory.

Run these commands from the directory containing `input`:

```sh
mkdir -p output
docker pull ghcr.io/kruton/local-ding:latest
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$PWD/input:/input:ro" \
  -v "$PWD/output:/output" \
  ghcr.io/kruton/local-ding:latest \
  patch --factory /input/factory.pak \
  --build-version 7106_2611060006 --output-dir /output
```

Replace `factory.pak` with the actual filename and choose a build version newer
than the camera's installed version. For a specific release, use its published
version tag in place of `latest`. Add `--ntp-server 192.168.1.1` to the patch
command to seed a manual NTP server; otherwise NTP uses DHCP option 42.

The utility writes a `.pak` and its matching `.json` manifest to `output`.

Upload the generated `.pak` through the camera's Firmware Update page. The
container does not flash the camera. After reboot, open
`https://CAMERA/local-settings/` to configure MQTT and add an SSH public key.
Existing persistent authorized keys remain available.

The output directory must exist and be writable. No privileged container is
required. The local-only overlay disables cloud services, public DNS and public
IPv4 destinations while retaining private routed networks.

### Verify generated images (optional)

If you want to verify the patch has been applied to a image, use the output
`.pak` and manifest `.json`.  Verify the result using the exact generated
filenames:

```sh
docker run --rm --user "$(id -u):$(id -g)" \
  -v "$PWD/output:/output:ro" \
  ghcr.io/kruton/local-ding:latest \
  verify /output/DB_566128M5MP_W_W.7106_2611060006.Reolink-Video-Doorbell-WiFi-W.OV05A10.5MP.WIFI8812.REOLINK.pak \
  --manifest /output/DB_566128M5MP_W_W.7106_2611060006.Reolink-Video-Doorbell-WiFi-W.OV05A10.5MP.WIFI8812.REOLINK.json
```

## Configuration

Open `https://CAMERA/local-settings/` with the camera administrator login.
MQTT configuration and public keys are owner-only persistent files.

### SSH access

Dropbear provides public-key-only SSH as `root` on port `2222`. Add your public
key in the settings page, then connect:

```sh
ssh -p 2222 -i ~/.ssh/id_camera root@CAMERA
```

Existing authorized keys and the per-camera host key persist under
`/mnt/para/dropbear/`. Password login, forwarding, and SFTP are disabled.
Terminal sessions and remote commands are supported.

NTP settings are stored in `/mnt/para/local-ding/time.json`, with runtime state
under `/mnt/tmp/local-ding-time/`. The executable provides the
`local-ding-mqtt`, `local-ding-settings`, and `local-ding-router` service aliases.

### NTP configuration

Automatic time synchronization requests DHCP option 42. Manual mode accepts
private IPv4 servers and persists across reboots. `--ntp-server ADDRESS` seeds
Manual mode during packaging. Persistent settings override image defaults.

## Build

The firmware executables are `local-ding` and `dropbearmulti`; the host utility
is `local-ding-fw`. `dropbearmulti` contains the SSH server and Ed25519 key
generator, invoked through the `dropbear` and `dropbearkey` symlinks. It is
stripped and dynamically linked against glibc. Both executables are built by
`scripts/build.py` and included in the Linux bundle and Docker image.

Linux x86-64 build dependencies: uv, Python 3.10+, build-essential, curl, bzip2
and Rust nightly-2026-11-01 with the `rust-src` and `rustfmt` components.

```sh
uv sync --locked
rustup toolchain install nightly-2026-11-01 --profile minimal --component rustfmt --component rust-src
sh scripts/toolchain.sh
uv run --frozen python scripts/build.py --toolchain .cache/toolchain/armv7-eabihf--glibc--stable-2020.02-2
uv run --frozen python scripts/bundle.py
```

The compiler uses its public glibc 2.30 sysroot. `LOCAL_DING_TOOLCHAIN` also
selects the toolchain. Source downloads use Cargo.lock and pinned toolchain and
Dropbear SHA-256 checksums. The Linux release archive includes both ARM executables and
the Python utility.  Patching dependencies: `uv`, Python 3.10+, `fakeroot mtd-utils
liblzo2-2`.

The ARM build rebuilds `std` and `panic_abort` with
`-Zbuild-std=std,panic_abort -Zbuild-std-features=optimize_for_size`.
The standard library uses size-oriented algorithms with backtraces and panic
unwinding disabled. Panics print a diagnostic and abort. Full LTO, one codegen
unit, `opt-level = "z"`, and symbol stripping apply to the release build.
Both production and settings-test executables use these build settings;
host unit tests use the toolchain's standard test runtime.

```sh
./local-ding-fw patch --factory /input/factory.pak \
  --build-version 7106_2611060005 --output-dir /output
./local-ding-fw verify /output/DB_566128M5MP_W_W.7106_2611060005.Reolink-Video-Doorbell-WiFi-W.OV05A10.5MP.WIFI8812.REOLINK.pak \
  --manifest /output/DB_566128M5MP_W_W.7106_2611060005.Reolink-Video-Doorbell-WiFi-W.OV05A10.5MP.WIFI8812.REOLINK.json
```

The output version must be newer than the camera's installed version. The input
is never overwritten. Keep the generated filename intact. The manifest records
checksums and filesystem capacity. No hardware flashing is automatic.

### Dropbear build

Dropbear [2026.94](https://github.com/mkj/dropbear/releases/tag/DROPBEAR_2026.94)
is built from the checksum-verified upstream GitHub archive, pinned to release
commit `28216cd9af822732a1549b78621c2ea9a76a0fe5`. To build it independently or use a
locally supplied source archive:

```sh
uv run --frozen python scripts/build_dropbear.py --toolchain PATH \
  --source-archive /path/to/dropbear-2026.94.tar.gz
```

The source archive is checksum-verified, including when supplied locally.
`payload/dropbear.json` records its source, build options hash, binary size, and
checksum. The firmware manifest records the installed Dropbear checksum and
filesystem capacity.

## Build the container locally

Build the payload with the commands above, or extract the Linux release bundle,
then build the image:

```sh
docker build -t local-ding .
```

Use `local-ding` in place of `ghcr.io/kruton/local-ding:latest` in the patching
and verification commands.

## Tests

`uv run --frozen python -m unittest discover -s tests` runs host tests without
vendor images.  `uv run --frozen python scripts/build.py --settings-test
--toolchain PATH` builds the isolated settings test executable. Integration
tests accept a locally supplied factory image; neither firmware nor generated
images belong in Git or release assets.

Set `LOCAL_DING_SYSROOT` to the public toolchain sysroot for ARM integration
tests. `LOCAL_DING_FACTORY` optionally supplies a local factory image for the
firmware packaging test; CI does not download a factory image.

## Python development

```sh
uv sync --locked
uv run --frozen ruff check --fix
uv run --frozen ruff format
uv run --frozen ruff format --check
uv run --frozen ruff check
```

## Asset formatting

```sh
uv sync --locked
npm ci
uv run --frozen python scripts/format_assets.py
uv run --frozen python scripts/format_assets.py --check
```

## Rust data handling

JSON parsing and serialization use Serde and serde_json. Text fields use
`String`, payloads use `Vec<u8>`, and filesystem calls validate paths with
`CString`. HTTP sizes and ROUT frame lengths are checked at protocol boundaries.
Run `cargo test --locked` for JSON and configuration-validation unit tests.

MQTT uses `rumqttc` over plain TCP with TLS and WebSocket features disabled.
The event loop handles protocol I/O and keepalive continuously. QoS 1 publishes
complete after the matching broker acknowledgment; the bridge retains pending
doorbell events across connection failures and republishes availability after
reconnecting.

Persistent MQTT configuration is `/mnt/para/mqtt/events.json`; NTP configuration
is `/mnt/para/local-ding/time.json`. Both use typed Serde JSON. NTP stores `mode`
as `"auto"` or `"manual"` and `servers` as an array of IPv4 strings. The
`local-ding-time-config` helper decodes and validates NTP settings for the boot
service. Files are written atomically with tempfile, mode 0600, file sync, and
parent-directory sync. SSH authorized keys retain the standard OpenSSH format.
