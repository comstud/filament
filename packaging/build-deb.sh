#!/bin/sh
# Build python3-filament .deb packages from a clean archive of git HEAD, in a
# temp directory so the working tree is untouched. (Commit your changes
# first -- this archives HEAD, not the working tree.)
#
# Needs: build-essential debhelper dh-python python3-dev python3-setuptools
#        python3-wheel pybuild-plugin-pyproject libevent-dev libbluetooth-dev
#
# Packages land under dist/deb/.
set -e
cd "$(dirname "$0")/.."
TOP=$PWD

VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)
[ -n "$VERSION" ] || { echo "could not read version from pyproject.toml" >&2; exit 1; }

BUILDDIR=$(mktemp -d "${TMPDIR:-/tmp}/filament-deb.XXXXXX")
trap 'rm -rf "$BUILDDIR"' EXIT

git archive --prefix="filament-$VERSION/" HEAD | tar -x -C "$BUILDDIR"

cd "$BUILDDIR/filament-$VERSION"
dpkg-buildpackage -us -uc -b

mkdir -p "$TOP/dist/deb"
cp "$BUILDDIR"/*.deb "$BUILDDIR"/*.changes "$BUILDDIR"/*.buildinfo "$TOP/dist/deb/"

echo
echo "Results:"
ls "$TOP"/dist/deb/
