#!/usr/bin/env python3
"""Print the source lines behind each evidence locator, read at its pinned commit.

Usage: locators.py <model.yaml> --repo <name>=<path> [--repo ...] [--check] [--max-lines N]

Reads `<repo>@<sha>:<path>:<lines>` locators with `git show`, never the working tree.
Exits 1 if a locator does not resolve: an unknown repository, a commit that is not a
source, a missing file, or lines past the end of the file. Skips `vendor:` locators
and locators of other forms, and lists them. Needs PyYAML and git.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

import yaml

LOCATOR = re.compile(r"^([\w.-]+)@([0-9a-f]{7,40}):([^:]+?)(?::(\d+)(?:-(\d+))?)?$")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("--repo", action="append", default=[], help="name=path of a local clone; repeat for each source")
    ap.add_argument("--check", action="store_true", help="resolve only; do not print lines")
    ap.add_argument("--max-lines", type=int, default=40, help="lines to print for each locator")
    a = ap.parse_args()

    m = yaml.safe_load(Path(a.model).read_text())
    repos = {}
    for r in a.repo:
        name, _, path = r.partition("=")
        repos[name] = Path(path).expanduser()
    shas = {s["name"]: s["sha"] for s in m["subject"]["sources"]}

    errs, skipped = [], []
    for e in m["evidence"]:
        loc = e["locator"]
        hit = LOCATOR.match(loc)
        if not hit:
            skipped.append(f"{e['tag']}: {loc}")
            continue
        name, sha, path, first, last = hit.groups()
        if name not in repos:
            errs.append(f"{e['tag']}: no --repo path for {name}")
            continue
        if name not in shas or not shas[name].startswith(sha):
            errs.append(f"{e['tag']}: {name}@{sha} is not the commit in subject.sources")
            continue
        out = subprocess.run(["git", "-C", str(repos[name]), "show", f"{shas[name]}:{path}"],
                             capture_output=True, text=True)
        if out.returncode:
            errs.append(f"{e['tag']}: {path} not found at {sha}")
            continue
        lines = out.stdout.splitlines()
        lo = int(first) if first else 1
        hi = int(last) if last else (lo if first else len(lines))
        if lo > hi or hi > len(lines):
            errs.append(f"{e['tag']}: lines {lo}-{hi} are outside {path} ({len(lines)} lines)")
            continue
        if a.check:
            continue
        print(f"## {e['tag']} {loc}")
        print(f"   what: {e.get('what', '')}")
        for n in range(lo, min(hi, lo + a.max_lines - 1) + 1):
            print(f"{n:6d}  {lines[n - 1]}")
        if hi - lo + 1 > a.max_lines:
            print(f"   ... {hi - lo + 1 - a.max_lines} more lines")
        print()

    for s in skipped:
        print(f"SKIP {s}")
    if errs:
        print(f"FAIL {a.model}: {len(errs)} locator(s) do not resolve")
        for x in errs:
            print(f"  - {x}")
        sys.exit(1)
    print(f"OK {a.model}: {len(m['evidence']) - len(skipped)} locator(s) resolve, {len(skipped)} skipped")


if __name__ == "__main__":
    main()
