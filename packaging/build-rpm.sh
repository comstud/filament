#!/bin/sh
# Build source + binary RPMs for the system Python 3.
#
# Needs: rpm-build plus the spec's BuildRequires. Install them with:
#     sudo dnf install rpm-build dnf-plugins-core
#     sudo dnf builddep packaging/rpm/python-filament.spec
#
# RPMs land under dist/rpm/.
set -e
cd "$(dirname "$0")/.."

TARBALL=$(packaging/make-tarball.sh)

rpmbuild -ta "$TARBALL" \
    --define "_rpmdir $PWD/dist/rpm" \
    --define "_srcrpmdir $PWD/dist/rpm"

echo
echo "Results:"
find dist/rpm -name '*.rpm'
