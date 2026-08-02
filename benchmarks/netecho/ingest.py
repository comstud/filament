#!/usr/bin/env python3
"""Merge a netecho campaign log into the per-version results JSONs.

The campaign log is what netecho_campaign.sh prints: a "### venv=... version=X
ft=0|1" header per interpreter, then one "<framework>|rep<N>|NETECHO_JSON:{...}"
line per measurement.  Results are folded into results/<arch>[-ft]/<version>.json
under runs["netecho"], so RESULTS.md can render them exactly where it renders
everything else, and so a netecho number carries the same provenance stamp as
the rest of that table.

    ingest.py <campaign.log> <arch>        # arch: amd64 | arm64
"""
import json
import os
import re
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(os.path.dirname(HERE), "results")
FRAMEWORKS = ("filament", "gevent", "eventlet")


def summarise(samples):
    """One cell: mean of the reps, with the observed range and mean latencies.

    Mean rather than median because there are three reps and the point of
    alternating them is that no single one is privileged; min/max are carried
    so a wide spread cannot hide inside the mean."""
    rps = [s["requests_per_sec"] for s in samples]
    return {
        "requests_per_sec_mean": st.mean(rps),
        "requests_per_sec_min": min(rps),
        "requests_per_sec_max": max(rps),
        "p50_ms": round(st.mean([s["p50_ms"] for s in samples]), 3),
        "p99_ms": round(st.mean([s["p99_ms"] for s in samples]), 3),
        "reps": len(samples),
        "errors": sum(s["errors"] for s in samples),
        "gomaxprocs": samples[-1].get("gomaxprocs"),
        "generator": samples[-1].get("generator"),
    }


def main():
    log, arch = sys.argv[1], sys.argv[2]
    cur = None
    data = {}          # (version, ft) -> {fw: {conns: [samples]}}
    for line in open(log):
        m = re.match(r"### venv=(\S+) version=(\S+) ft=(\d)", line)
        if m:
            cur = (m.group(2), m.group(3) == "1")
            data.setdefault(cur, {})
            continue
        if cur is None or "NETECHO_JSON:" not in line:
            continue
        fw = line.split("|", 1)[0]
        j = json.loads(line.split("NETECHO_JSON:")[1])
        data[cur].setdefault(fw, {}).setdefault(j["conns"], []).append(j)

    for (version, ft), byfw in sorted(data.items()):
        d = os.path.join(RESULTS, arch + ("-ft" if ft else ""))
        path = os.path.join(d, "%s.json" % version)
        if not os.path.exists(path):
            print("  no results file for %s (%s) -- skipped" % (version, path))
            continue
        with open(path) as fh:
            doc = json.load(fh)
        entry = {}
        for fw in FRAMEWORKS:
            cells = byfw.get(fw)
            if not cells:
                # No samples at all: the server never came up for this
                # framework (gevent has no free-threaded build, for one).
                entry[fw] = {"framework": fw, "benchmark": "netecho",
                             "status": "error",
                             "error": "server did not start", "result": None}
                continue
            entry[fw] = {
                "framework": fw, "benchmark": "netecho", "status": "ok",
                "result": dict(("c%d" % c, dict(summarise(s), concurrency=c))
                               for c, s in sorted(cells.items())),
            }
        doc.setdefault("runs", {})["netecho"] = entry
        with open(path, "w") as fh:
            json.dump(doc, fh, indent=2)
        got = [fw for fw in FRAMEWORKS if entry[fw]["status"] == "ok"]
        print("  %-12s %-8s netecho <- %s"
              % (os.path.basename(d), version, ", ".join(got)))


if __name__ == "__main__":
    main()
