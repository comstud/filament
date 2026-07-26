# RPM packaging for filament.
#
# Builds a python3-filament binary package against whichever Python 3 is the
# distro default at build time (the %%{python3} / %%{python3_pkgversion}
# macros) -- no Python version is hardcoded here.
#
# Build (from a git checkout):
#     packaging/build-rpm.sh
# or by hand:
#     packaging/make-tarball.sh          # writes dist/filament-<ver>.tar.gz
#     rpmbuild -ta dist/filament-<ver>.tar.gz
#
# Needs: rpm-build, pyproject-rpm-macros, python3-devel, gcc, gcc-c++,
# libevent-devel (dnf builddep can pull these from this spec).
#
# EL note: the build needs setuptools >= 64 (see pyproject.toml). EL9's
# default python3.9 stack ships setuptools 53, so build there against an
# alternate stack, e.g.:
#     rpmbuild --define 'python3_pkgversion 3.12' -ta dist/filament-<ver>.tar.gz
# Fedora and EL10+ default stacks are new enough as-is.

%global srcname filament

Name:           python-%{srcname}
Version:        0.9.2
Release:        1%{?dist}
Summary:        Microthreads for Python

# MIT overall; PSF-2.0 for the Stackless-derived files in the vendored
# greenlet and the CPython-derived stdlib shims (see THIRD_PARTY_NOTICES.md).
License:        MIT AND PSF-2.0
URL:            https://github.com/comstud/filament
Source0:        %{srcname}-%{version}.tar.gz

BuildRequires:  gcc
BuildRequires:  gcc-c++
# The distro CPython is built with Bluetooth support, so its pyconfig.h
# defines HAVE_BLUETOOTH_BLUETOOTH_H and _filament.socket then needs the
# bluez headers (header-only; no -lbluetooth link).
BuildRequires:  bluez-libs-devel
BuildRequires:  libevent-devel
BuildRequires:  python%{python3_pkgversion}-devel
BuildRequires:  pyproject-rpm-macros

%global _description %{expand:
Filament is a greenlet-based cooperative concurrency library for Python --
an efficient alternative to gevent and eventlet built around a small C
core. It provides lightweight "greenthreads" that yield to a scheduler on
I/O and synchronization instead of blocking OS threads, plus cooperative
drop-in replacements for the standard library and compatibility shims for
the gevent and eventlet APIs.}

%description %_description

%package -n python%{python3_pkgversion}-%{srcname}
Summary:        %{summary}

%description -n python%{python3_pkgversion}-%{srcname} %_description

%prep
%autosetup -n %{srcname}-%{version}

%generate_buildrequires
%pyproject_buildrequires

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files %{srcname} _filament _fil_greenlet

%check
# Import the top-level modules (loads the C extensions, so this catches
# missing shared libs and bad ABI). The full pytest suite is intentionally
# not run during package builds; use tox/pytest from a checkout for that.
%pyproject_check_import -t

%files -n python%{python3_pkgversion}-%{srcname} -f %{pyproject_files}
%doc README.md RELEASES.md THIRD_PARTY_NOTICES.md AUTHORS

%changelog
* Sun Jul 26 2026 Chris Behrens <cbehrens@codestud.com> - 0.9.2-1
- Fix unbounded greenthread leak in the gevent/eventlet compat shims and in
  the core (uncollectable reference cycle plus a leaked greenlet object).
- Fix hang at interpreter exit when a thread pool was never shut down.
- Fix os.read()/os.write() on regular files failing under monkey-patching,
  which broke tempfile and large WSGI request bodies.
- Fix Group/Pool retaining every greenthread they ever spawned.
- Speed up gevent joinall()/wait()/iwait(): 20-way HTTP fan-out 2386 -> 5392
  req/s.

* Sun Jul 26 2026 Chris Behrens <cbehrens@codestud.com> - 0.9.1-1
- Fix heap corruption when deallocating Python subclasses of the C types.
- Fix intermittent abort at interpreter exit on Python 3.14 caused by DNS
  resolver worker threads racing finalization.
- Fix threading.Timer never firing, and Event.wait()/Thread.join() leaking an
  internal timeout exception instead of returning on expiry.
- Fix resolver lookups with keyword arguments, and negative file descriptors
  parking forever instead of raising EBADF.
- Python 2.7 repairs across filament.os, pyqueue, subprocess.

* Sat Jul 25 2026 Chris Behrens <cbehrens@codestud.com> - 0.9.0-1
- First full release: C scheduler with per-OS-thread schedulers and safe
  cross-thread synchronization, C queues/locks/timers, libevent-backed I/O.
- Drop-in gevent and eventlet compatibility shims, cooperative stdlib
  replacements, and a monkey patcher.
- Ships its own vendored greenlet runtime with an optional private-stack
  fiber core.
- Python 3.8-3.15 on Linux (amd64/arm64); Python 2.7 still builds.

* Sat Jul 25 2026 Chris Behrens <cbehrens@codestud.com> - 0.1.0-1
- Initial package.
