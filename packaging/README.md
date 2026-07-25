# OS packaging

Native OS packages for filament. Both flavors build against **whatever the
distro's default Python 3 is at build time** — no interpreter version is
hardcoded anywhere. Rebuilding the same sources on a distro with a newer
Python produces a package for that Python.

The package version comes from `pyproject.toml` (RPM: `Version:` in the spec;
Debian: the top entry of `debian/changelog`). When bumping the project
version, update those two files to match.

Both build scripts archive **git HEAD**, so commit changes before building.

## RPM (Fedora / RHEL / CentOS Stream)

Spec: `packaging/rpm/python-filament.spec`, using the standard
`pyproject-rpm-macros` flow (`%pyproject_wheel` / `%pyproject_install`), which
targets the distro's `%{python3}`. Produces `python3-filament`.

```sh
sudo dnf install rpm-build dnf-plugins-core
sudo dnf builddep packaging/rpm/python-filament.spec
packaging/build-rpm.sh          # RPMs land in dist/rpm/
```

EL9 note: the build needs setuptools >= 64; EL9's default python3.9 stack
ships 53. Build there against an alternate stack instead, e.g.
`rpmbuild --define 'python3_pkgversion 3.12' -ta dist/filament-<ver>.tar.gz`
with the `python3.12-*` packages installed. Fedora and EL10+ are fine as-is.

## Debian / Ubuntu

Standard `debhelper` + `dh-python`/`pybuild` packaging in `debian/`, built as
a native-format source package. Produces `python3-filament` for the default
`python3`.

```sh
sudo apt install build-essential debhelper dh-python python3-dev \
    python3-setuptools python3-wheel pybuild-plugin-pyproject \
    libevent-dev libbluetooth-dev
packaging/build-deb.sh          # .debs land in dist/deb/
```

(`dpkg-buildpackage -us -uc -b` straight from a checkout also works; the
script just keeps the working tree clean by building from a git archive.)

Needs setuptools >= 64, i.e. Debian 12+/Ubuntu 24.04+. Runtime dependencies
(libevent, python3-greenlet, the python3 ABI range) are filled in
automatically by `${shlibs:Depends}` / `${python3:Depends}`.

## Not covered

- The test suite is not run during package builds (it is timing-sensitive);
  run it via tox/pytest from a checkout instead. Both builds do at least
  import-check the C extensions (`%pyproject_check_import` on RPM; the deb
  build imports the extensions while byte-compiling/building the wheel).
- Python 2.7 is test-only upstream and has no OS packaging.
