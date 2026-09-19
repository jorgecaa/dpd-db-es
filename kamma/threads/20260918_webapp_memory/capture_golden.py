#!/usr/bin/env python3
"""Capture the golden-output oracle for the 20260918_webapp_memory thread.

Hashes the response bodies of a fixed request set, so a later change can prove
that what the dictionary serves did not change. Read-only against the running
webapp and against `dpd.db` (opened with sqlite3 in read-only mode): it only
writes the oracle JSON next to itself.

Run from the project root:

    .venv/bin/python kamma/threads/20260918_webapp_memory/capture_golden.py
    .venv/bin/python kamma/threads/20260918_webapp_memory/capture_golden.py \
        --compare-to kamma/threads/20260918_webapp_memory/golden.json

The sample is derived from the live db the first time (100 lemmas spread over
the whole dictionary plus ten fixed ones), and then reused from the oracle file,
so captures stay comparable even if the db release changes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
DEFAULT_OUT = HERE / "golden.json"
SAMPLE_SIZE = 100

# Fixed lemmas: diacritics, a compound, a root-derived word and plain forms.
GOLDEN_LEMMAS = [
    "buddha",
    "dhamma",
    "dhammacakkha",
    "paṭiccasamuppāda",
    "satipaṭṭhāna",
    "anattā",
    "nibbāna",
    "saṅgha",
    "bhagavā",
    "jhāna",
]

# Extra query surface: GoldenDict HTML, plain-ASCII input (exercises the
# ascii→unicode map) and bold-definitions search.
EXTRA_REQUESTS = [
    ("/gd?search=buddha", "gd buddha"),
    ("/gd?search=dhamma", "gd dhamma"),
    ("/gd?search=nibbana", "gd nibbana (ascii)"),
    ("/gd?search=satipatthana", "gd satipatthana (ascii)"),
    ("/gd?search=paticcasamuppada", "gd paticcasamuppada (ascii)"),
    ("/search_html?q=buddha", "html buddha"),
    ("/search_html?q=nibbana", "html nibbana (ascii)"),
    ("/search_html?q=jhana", "html jhana (ascii)"),
    ("/search_json?q=sangha", "json sangha (ascii)"),
    ("/search_json?q=anatta", "json anatta (ascii)"),
    ("/bd_search?q1=dukkha&q2=suffering&option=starts_with", "bd dukkha/suffering"),
    ("/bd_search?q1=anicca&q2=impermanent&option=starts_with", "bd anicca/impermanent"),
    ("/bd_search?q1=nibbana&q2=&option=starts_with", "bd nibbana"),
    ("/bd_search?q1=metta&q2=love&option=starts_with", "bd metta/love"),
    ("/bd_search?q1=sati&q2=mindful&option=starts_with", "bd sati/mindful"),
    ("/search_json?q=dukkha", "json dukkha"),
    ("/search_json?q=anatta", "json anatta"),
    ("/search_json?q=metta", "json metta"),
    ("/search_json?q=kamma", "json kamma"),
    ("/search_json?q=vipassana", "json vipassana (ascii)"),
]


def derive_sample(db_path: Path, size: int) -> list[str]:
    """Spread `size` lemmas across the whole headword table, plus the fixed ones."""
    uri = f"file:{db_path}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        lemmas = [
            row[0]
            for row in conn.execute("SELECT lemma_1 FROM dpd_headwords ORDER BY id")
        ]
    step = max(1, len(lemmas) // size)
    sample = lemmas[::step][:size]
    for lemma in GOLDEN_LEMMAS:
        if lemma not in sample:
            sample.append(lemma)
    return sample


def build_requests(sample: list[str]) -> list[tuple[str, str]]:
    requests = [
        (f"/search_json?q={quote(lemma, safe='')}", f"json {lemma}") for lemma in sample
    ]
    return requests + EXTRA_REQUESTS


def fetch(base_url: str, path: str, timeout: float) -> tuple[int, bytes]:
    url = f"{base_url.rstrip('/')}{path}"
    request = urllib.request.Request(url, headers={"Accept-Encoding": "identity"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def digest(results: dict[str, dict[str, object]]) -> str:
    canonical = json.dumps(results, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--db", type=Path, default=Path("dpd.db"))
    parser.add_argument("--sample-size", type=int, default=SAMPLE_SIZE)
    parser.add_argument(
        "--compare-to",
        type=Path,
        default=None,
        help="compare against a previous oracle instead of writing a new one",
    )
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    previous: dict[str, object] | None = None
    if args.out.exists():
        previous = json.loads(args.out.read_text(encoding="utf-8"))

    if previous is not None:
        sample = list(previous["sample"])  # type: ignore[arg-type]
    else:
        sample = derive_sample(args.db, args.sample_size)

    if args.compare_to is not None:
        return compare(
            args.compare_to, args.base_url, build_requests(sample), args.timeout
        )

    results: dict[str, dict[str, object]] = {}
    failures: list[str] = []
    for path, label in build_requests(sample):
        status, body = fetch(args.base_url, path, args.timeout)
        results[label] = {
            "path": path,
            "status": status,
            "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
        }
        if status != 200:
            failures.append(f"{label}: HTTP {status}")
        if not args.quiet:
            print(f"{status}  {len(body):>9} B  {label}")

    payload = {
        "meta": {
            "base_url": args.base_url,
            "requests": len(results),
            "digest": digest(results),
        },
        "sample": sample,
        "results": results,
    }
    args.out.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"\n{len(results)} requests -> {args.out}")
    print(f"digest: {payload['meta']['digest']}")
    if failures:
        print("FAILURES:\n  " + "\n  ".join(failures), file=sys.stderr)
        return 1
    return 0


def compare(
    previous_path: Path, base_url: str, requests: list[tuple[str, str]], timeout: float
) -> int:
    """Re-run the same requests and diff the hashes against a previous oracle."""
    previous = json.loads(previous_path.read_text(encoding="utf-8"))
    old: dict[str, dict[str, object]] = previous["results"]
    new: dict[str, dict[str, object]] = {}
    for path, label in requests:
        status, body = fetch(base_url, path, timeout)
        new[label] = {
            "path": path,
            "status": status,
            "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
        }

    changed = [
        label
        for label in new
        if label in old and new[label]["sha256"] != old[label]["sha256"]
    ]
    added = [label for label in new if label not in old]
    missing = [label for label in old if label not in new]
    print(
        f"same: {len(new) - len(changed) - len(added)}  changed: {len(changed)}  new: {len(added)}"
    )
    for label in changed:
        print(f"  CHANGED {label}: {old[label]['bytes']} B -> {new[label]['bytes']} B")
    for label in added:
        print(f"  ADDED   {label}")
    for label in missing:
        print(f"  MISSING {label}")
    print(f"old digest: {previous['meta']['digest']}")
    print(f"new digest: {digest(new)}")
    return 1 if (changed or added or missing) else 0


if __name__ == "__main__":
    raise SystemExit(main())
