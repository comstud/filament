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
# EL note: the build needs setuptools >= 77 (see pyproject.toml -- that is the
# floor for the PEP 639 license metadata, raised from 64). EL9's default
# python3.9 stack ships setuptools 53, so build there against an alternate
# stack, e.g.:
#     rpmbuild --define 'python3_pkgversion 3.12' -ta dist/filament-<ver>.tar.gz
# Check the chosen stack's setuptools against the floor: distro Pythons older
# than the 77 cutoff fail in %%generate_buildrequires with a `project.license`
# ValueError rather than anything that names setuptools.

%global srcname filament
# Prerelease: rpm would sort a Version of 0.9.5a1 as NEWER than 0.9.5, so the
# alpha keeps the release version and carries the marker in Release (0.x.<pre>
# sorts before the eventual 1%%{?dist}).  upstream_version is what pyproject
# says and what make-tarball.sh names the archive, which is not the same
# string -- Source0 and %%autosetup follow it, not %%{version}.
%global upstream_version 0.9.5a1

Name:           python-%{srcname}
Version:        0.9.5
Release:        0.1.a1%{?dist}
Summary:        Microthreads for Python

# MIT overall; PSF-2.0 for the Stackless-derived files in the vendored
# greenlet and the CPython-derived stdlib shims (see THIRD_PARTY_NOTICES.md).
License:        MIT AND PSF-2.0
URL:            https://github.com/comstud/filament
Source0:        %{srcname}-%{upstream_version}.tar.gz

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
%autosetup -n %{srcname}-%{upstream_version}

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
* Sun Aug 02 2026 Chris Behrens <cbehrens@codestud.com> - 0.9.5-0.1.a1
- The io thread performs a blocked socket recv()/send() itself, into the
  caller's own buffer, so the wakeup hands back data rather than readiness.
  Echo +9.7%% to +88%%; locust FastPingUser @1000 +11.6%%.
- Free-threaded (PEP 703) support: builds and runs with the GIL genuinely
  disabled, out of the box. 5.93x on six cores for CPU-bound work.
- Fix Queue.task_done() raising for a task that really was queued, and
  Queue.join() then waiting forever.
- Fix a blocked read/write with more than one waiter hanging forever, ignoring
  settimeout().
- A whole-codebase memory-safety audit: fix a use-after-free freeing a
  socket's cached wait state under a second parked waiter; a refcount
  underflow (then use-after-free) raising a custom timeout_exc instance, and
  a crash on a non-exception timeout_exc; eager-io data loss when a transfer
  completed as its deadline expired; queues leaking every item still enqueued
  at deallocation; fd leaks in accept()/dup()/socketpair(); sendall()
  silently truncating buffers over 4 GiB; a ThreadPool shutdown
  use-after-free and a construction deadlock under thread exhaustion; and
  Queue.join() waiting forever after a blocked putter was killed.
- Queue, SimpleQueue and Condition support cyclic garbage collection:
  reference cycles through queued items or a condition's lock used to be
  invisible to the collector and leaked permanently.
- Condition.wait() joins the waiter list before releasing the lock, so a
  Python-level lock whose release() switches greenthreads can no longer miss
  its own notification.
- Timer callbacks that raise are reported via the unraisable hook instead of
  poisoning the next scheduler event; Timeout no longer throws into a
  greenlet that already finished.
- Free-threading: ThreadPool and Timer get their own locks (both had still
  been relying on the GIL; concurrent shutdown() calls could free the pool
  twice); a timed wait racing its own wakeup no longer swallows the signal;
  the queue chunk freelists, the io thread singleton and the per-socket
  cached-wait slot are safe to hit from two threads at once.
- Remove the socket attribute fil_first_misses (reporting-only, and unsafe to
  increment without a GIL).

* Thu Jul 30 2026 Chris Behrens <cbehrens@codestud.com> - 0.9.4-1
- A socket with settimeout() set now uses the same cheap cached edge-triggered
  wait as one without, instead of being pushed onto the classic io path and
  paying two epoll_ctl syscalls, an event_new/free, two mutex/cond init+destroy
  pairs and a malloc per blocked operation. Since connection pools set a
  timeout on every pooled connection, client workloads paid that on essentially
  every request. epoll_ctl per blocked operation drops from ~4.6 to ~0.002; a
  geventhttpclient load benchmark goes from 8% behind gevent to 21% ahead at
  100 connections and 26% ahead at 1000, at roughly half the p95 latency.
- Fix sendall() blocking forever on a socket with settimeout() set: a first
  segment that partially succeeded returned before computing the deadline, so
  every later segment ran with none at all.
- Drop Python 3.8 (end-of-life October 2024); the floor is now 3.9, and 3.9 and
  3.15 are both in the tested matrix for the first time. This is what allows the
  PEP 639 license metadata, which needs setuptools >= 77 -- so the build
  requirement rises from 64 to 77.
- Build on macOS: setup.py discovers a Homebrew libevent, and the vendored
  greenlet's fiber-switch assembly assembles on Mach-O.
- src/ and the vendored greenlet compile warning-free under -Wall
  -Wsign-compare with both gcc and clang.
- Fix FIL_SCHED_EVENT_FREELIST_MAX being defined twice with different values
  (256 in the header, 2048 in fil_scheduler.c).

* Tue Jul 28 2026 Chris Behrens <cbehrens@codestud.com> - 0.9.3-1
- gevent compat: install() now also owns the top-level greenlet name, so
  greenlet.getcurrent() returns the running gevent Greenlet as it does under
  real gevent; code branching on that identity no longer deadlocks.
- gevent compat: links registered before join() now run before it returns,
  matching gevent, so unhandled-exception logging through a link works.
- gevent compat: cooperative select.poll(), hub loop io() watchers (all of
  zmq.green), HTTP/1.1 keep-alive and chunked bodies in pywsgi,
  signal_handler(), Timeout.close(), Greenlet introspection attributes, and
  LifoQueue/PriorityQueue under patch_all().
- Fix an untimed wait reporting a timeout, from a cut-short sleep leaving its
  wakeup queued to fire into a later, unrelated wait.
- Fix a use-after-free that segfaulted the scheduler when a greenthread was
  killed while a wakeup was already queued for it.
- Fix a libevent event leaked per blocking io operation (~144 bytes), which
  grew an HTTP client's memory by ~2 MB/s under sustained load.
- Fix three cases of C code returning a result with an exception still set
  (recv/send, RLock at interpreter exit, and a primitive handed ownership and
  thrown into in the same wakeup -- the last also stranded locks).
- Fix an assertion in the scheduler's deallocator that aborted builds with
  assertions enabled.
- Scheduler: immediate-wakeup FIFO plus a timer min-heap, and Timeout cancel()
  now removes the event. Arming a timeout with 50000 queued went 19.0us ->
  0.5us; 20000 armed-and-cancelled timeouts retained 9.4MB -> 0.

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
