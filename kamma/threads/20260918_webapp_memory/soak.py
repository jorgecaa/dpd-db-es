#!/usr/bin/env python3
"""Bounded soak for the 20260918_webapp_memory thread (task 2.5).

Fires a fixed sequence of mixed requests at a webapp instance and samples the
target process's anonymous memory every `--sample-every` requests, so growth can be
told apart from one-off cache filling. Read-only: it never writes to the db.

    .venv/bin/python kamma/threads/20260918_webapp_memory/soak.py \
        --base-url http://127.0.0.1:8082 --requests 1000 --sample-every 100

Exit code is non-zero if any request failed. The table it prints is the evidence
pasted into `plan.md`.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
GOLDEN = HERE / "golden.json"


def pid_for_port(port: int) -> int:
    """The pid listening on `port`, via `ss` (no psutil dependency here)."""
    output = subprocess.run(
        ["ss", "-ltnp"], capture_output=True, text=True, check=True
    ).stdout
    for line in output.splitlines():
        if f":{port} " in line and "pid=" in line:
            return int(line.split("pid=")[1].split(",")[0])
    raise SystemExit(f"no process listening on port {port}")


def anon_mib(pid: int) -> float:
    with open(f"/proc/{pid}/status") as handle:
        for line in handle:
            if line.startswith("RssAnon:"):
                return int(line.split()[1]) / 1024
    raise SystemExit(f"no RssAnon for pid {pid}")


def request_paths(sample: list[str]) -> list[str]:
    """A mixed, deterministic workload: hits, misses, other tabs, status."""
    paths: list[str] = []
    for lemma in sample:
        quoted = urllib.parse.quote(lemma, safe="")
        paths.append(f"/search_json?q={quoted}")
        paths.append(f"/search_html?q={quoted}")
        paths.append(f"/gd?search={quoted}")
    paths += [
        "/bd_search?q1=dukkha&q2=suffering&option=starts_with",
        "/bd_search?q1=nibbana&q2=&option=starts_with",
        "/search_json?q=zzzzz",  # miss: exercises the covering-index fallback
        "/status",
        "/",
    ]
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8082")
    parser.add_argument("--requests", type=int, default=1000)
    parser.add_argument("--sample-every", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()

    port = int(args.base_url.rstrip("/").rsplit(":", 1)[1])
    pid = pid_for_port(port)
    sample = list(json.loads(GOLDEN.read_text(encoding="utf-8"))["sample"])
    paths = request_paths(sample)

    print(f"soak: {args.requests} requests against {args.base_url} (pid {pid})")
    print(f"{'n':>5} {'anon MiB':>9} {'ms/req':>8}")
    samples: list[tuple[int, float]] = []
    failures: list[str] = []
    latencies: list[float] = []
    start = time.perf_counter()

    for index in range(1, args.requests + 1):
        path = paths[(index - 1) % len(paths)]
        request_start = time.perf_counter()
        try:
            with urllib.request.urlopen(
                f"{args.base_url}{path}", timeout=args.timeout
            ) as response:
                response.read()
                if response.status != 200:
                    failures.append(f"{path}: HTTP {response.status}")
        except urllib.error.HTTPError as error:
            failures.append(f"{path}: HTTP {error.code}")
        latencies.append((time.perf_counter() - request_start) * 1000)

        if index % args.sample_every == 0:
            memory = anon_mib(pid)
            samples.append((index, memory))
            print(f"{index:>5} {memory:>9.1f} {statistics.median(latencies[-args.sample_every:]):>8.1f}")

    elapsed = time.perf_counter() - start
    print(f"\ntotal: {elapsed:.1f} s, {statistics.median(latencies):.1f} ms/request median")

    if len(samples) >= 3:
        warm = [memory for _, memory in samples[1:]]
        drift = (warm[-1] - warm[0]) / warm[0] * 100
        print(f"drift after the first sample: {warm[0]:.1f} -> {warm[-1]:.1f} MiB ({drift:+.1f} %)")
        print(f"peak sample: {max(memory for _, memory in samples):.1f} MiB")

    if failures:
        print(f"FAILURES ({len(failures)}): {failures[:5]}")
        return 1
    print("all requests returned 200")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
