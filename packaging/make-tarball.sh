#!/bin/sh
# Create dist/filament-<version>.tar.gz from git HEAD, for OS package builds.
# (Commit your changes first -- this archives HEAD, not the working tree.)
set -e
cd "$(dirname "$0")/.."

VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)
[ -n "$VERSION" ] || { echo "could not read version from pyproject.toml" >&2; exit 1; }

mkdir -p dist
git archive --prefix="filament-$VERSION/" -o "dist/filament-$VERSION.tar.gz" HEAD
echo "dist/filament-$VERSION.tar.gz"
