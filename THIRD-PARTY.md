# Dependency notices

The Cargo lockfile identifies the exact Rust dependencies. Tokio, tokio-macros,
mio, socket2, bytes, pin-project-lite, libc, errno, signal-hook-registry and
procedural macro dependencies retain their upstream MIT and/or Apache-2.0
licenses. Dependency license texts are included under `payload/licenses/` in
release bundles; the build requires the corresponding Cargo source packages.

Bootlin GCC and its sysroot are build tools, downloaded separately with a
checksum. Bootlin publishes their sources and licensing information at
https://toolchains.bootlin.com/downloads/releases/. Compiler runtime code is
covered by its applicable runtime exception. Camera shared libraries are not
included in project distributions.

The patch utility uses system Python, fakeroot, mtd-utils and liblzo2. These
retain their upstream licenses and are installed through the host/container
package manager. Factory firmware remains a user-supplied local input.

Dropbear 2026.94 is downloaded from upstream GitHub with archive SHA-256
`e3dbb92dfe44a439ff6d371eaf0120410e23e0d0aae0c6c365c3489a54b55165`.
Its multicall binary includes the SSH server, key generator, LibTomCrypt, and
LibTomMath. Their MIT-style and other permissive notices are reproduced in
`payload/licenses/dropbear-2026.94/LICENSE`. The pinned source archive and build
configuration are identified by `scripts/build_dropbear.py`,
`config/dropbear-options.h`, and the payload manifest. The source archive is
downloaded from the upstream GitHub repository at commit
`28216cd9af822732a1549b78621c2ea9a76a0fe5` (tag `DROPBEAR_2026.94`).
The commit archive is verified against its pinned SHA-256 before extraction.

Serde, serde_derive, serde_core, serde_json, itoa, memchr, and zmij retain their
upstream MIT and/or Apache-2.0 licenses. Cargo.lock pins their resolved versions;
the build includes their license files in `payload/licenses/`.

The MQTT client uses rumqttc and its locked dependencies, including flume,
futures utilities, and Tokio utilities. Their upstream license texts are
included under `payload/licenses/` by the build. TLS and WebSocket dependencies
are disabled.

Configuration replacement uses tempfile with default features disabled, plus
its locked Rustix and utility dependencies. Their license files are included in
`payload/licenses/` alongside the other Rust dependencies.
