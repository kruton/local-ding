#!/bin/sh
set -eu
base=${1:-.cache/toolchain}
name=armv7-eabihf--glibc--stable-2020.02-2
mkdir -p "$base"
archive="$base/$name.tar.bz2"
[ -f "$archive" ] || curl --silent --show-error --fail --location --retry 3 "${BOOTLIN_TOOLCHAIN_URL:-https://toolchains.bootlin.com/downloads/releases/toolchains/armv7-eabihf/tarballs/$name.tar.bz2}" -o "$archive"
printf '%s  %s\n' 72993db0eb4b1d0f9896200eb6645e02affe039009d41749418082004a554fbc "$archive" | sha256sum -c -
[ -x "$base/$name/bin/arm-linux-gcc" ] || tar -xjf "$archive" -C "$base"
printf '%s\n' "$base/$name"
