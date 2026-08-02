#!/bin/bash
# Drive the networked echo benchmark: the server under test runs on one host,
# the neutral Go generator on another, and this script sits on a third (or on
# either) and orchestrates over ssh -- which avoids needing ssh trust between
# the two benchmark hosts.
#
#   usage: PROCS=<n> run.sh <srv-ssh> <srv-python> <cli-ssh> <srv-ip> [conns...]
#
# PROCS sets the generator's GOMAXPROCS and matters more than it looks: sweep it
# against `loadgen -serve` first and use the value that maximises the ceiling.
# On an 18-core Apple Silicon host the Go default understates by 1.6x.
#
# Build and ship the generator first (Go is not needed on either host, the
# binaries are static):
#   GOOS=linux  GOARCH=amd64 go build -o loadgen-linux-amd64  .
#   GOOS=darwin GOARCH=arm64 go build -o loadgen-darwin-arm64 .
#   scp loadgen-<target> <host>:~/filbench/loadgen
#
# The generator host must not share hardware with the server host -- a VM on the
# machine under test is not a second host.
set -u
SRV=$1; PY=$2; CLI=$3; SRVIP=$4; shift 4
PROCS=${PROCS:-0}
REPS=${REPS:-1}
CONNS=${@:-"200 1000"}
# Arms are alternated within one session rather than run once each in a fixed
# order: absolute numbers on these hosts drift 10-12% between sessions while the
# ratios hold, so a single pass in a fixed order attributes drift to whichever
# framework happened to run during it.
for rep in $(seq 1 "$REPS"); do
if [ $((rep % 2)) -eq 1 ]; then ORDER="filament gevent eventlet"; else ORDER="eventlet gevent filament"; fi
for fw in $ORDER; do
  # The kill MUST be its own ssh call.  Put it in the same command line as the
  # launch and pkill matches that very command line -- it contains the literal
  # "netecho_server.py" in the nohup part -- so the shell kills itself before
  # starting anything.  Same trap as pkill -f run_all.py earlier.
  ssh -o BatchMode=yes "$SRV" "pkill -f '[n]etecho_server[.]py'" </dev/null >/dev/null 2>&1
  sleep 1
  ssh -o BatchMode=yes "$SRV" "cd ~/filbench/bench-head && ulimit -n 65536 && # macOS has no setsid; nohup + a subshell is enough to survive the ssh exit
  (nohup $PY benchmarks/netecho_server.py --mode $fw --port 18899 > /tmp/nes_$fw.log 2>&1 &) ; sleep 4" </dev/null >/dev/null 2>&1
  ready=$(ssh -o BatchMode=yes "$SRV" "grep -c READY /tmp/nes_$fw.log 2>/dev/null || echo 0" </dev/null 2>/dev/null | tr -d '[:space:]')
  if [ "$ready" != "1" ]; then
    echo "$fw SERVER FAILED: $(ssh -o BatchMode=yes "$SRV" "tail -3 /tmp/nes_$fw.log 2>/dev/null | tr '\n' ' '" </dev/null 2>/dev/null)"
    continue
  fi
  for c in $CONNS; do
    out=$(ssh -o BatchMode=yes "$CLI" "ulimit -n 65536; ~/filbench/loadgen -procs $PROCS -host $SRVIP -port 18899 -conns $c -warmup 2s -duration 6s" </dev/null 2>/dev/null | grep NETECHO_JSON)
    echo "$fw|rep$rep|$out"
  done
  ssh -o BatchMode=yes "$SRV" "pkill -f '[n]etecho_server[.]py'" </dev/null >/dev/null 2>&1
done
done
echo DRIVE_DONE
