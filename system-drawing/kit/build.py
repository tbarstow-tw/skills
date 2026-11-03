#!/usr/bin/env python3
"""Validate a system model and build its sheet.

Usage: build.py <model.yaml> [-o <sheet.html>]

Exits 1 and prints every failure if the model breaks the schema or a model check.
Needs PyYAML and jsonschema.
"""
import argparse
import base64
import json
import re
import sys
from pathlib import Path

import jsonschema
import yaml

KIT = Path(__file__).resolve().parent
SCHEMA = KIT.parent / "schema" / "system-model.schema.json"
TEMPLATE = KIT / "template.html"
FONT = KIT / "arimo.woff2"
# The characters in FONT. Keep in step with the subset command in THIRD_PARTY_NOTICES.md.
OUTSIDE_FONT = re.compile(r"[^\t\n\u0020-\u007e\u00a0-\u017f\u2010-\u2027\u2030-\u203a\u2190-\u2199\u2212\u2264\u2265]")
TABLE_ROWS = 58  # (1032 - 82 px) at 14 px a row, less headers and gaps for four tables

SENSITIVE = [
    (re.compile(r"\b\d{12}\b"), "12-digit number (account ID?)"),
    (re.compile(r"\barn:aws[a-z-]*:", re.I), "ARN"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "IPv4 address"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"), "email address"),
    (re.compile(r"https?://", re.I), "URL"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AWS access key"),
    (re.compile(r"\b[a-z0-9-]+\.(?:com|net|io|org|internal|aws)\b", re.I), "hostname"),
]


def strings(node, path=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield from strings(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from strings(v, f"{path}[{i}]")
    elif isinstance(node, str):
        yield path, node


def model_checks(m):
    errs = []
    comps = {c["code"]: c for c in m["components"]}
    ifaces = {i["code"]: i for i in m["interfaces"]}
    bounds = {b["code"]: b for b in m.get("boundaries", [])}
    budgets = {b["code"]: b for b in m.get("budgets", [])}
    tags = {e["tag"] for e in m["evidence"]}
    lanes = set(m["lanes"])

    codes = [c["code"] for c in m["components"]] + list(ifaces) + list(bounds) + list(budgets)
    dupes = {c for c in codes if codes.count(c) > 1}
    if dupes:
        errs.append(f"duplicate codes: {sorted(dupes)}")
    if len(tags) != len(m["evidence"]):
        errs.append("duplicate evidence tags")

    mode = m["subject"]["mode"]
    for c in m["components"]:
        if c["lane"] not in lanes:
            errs.append(f"{c['code']}: lane {c['lane']} is not in lanes")
        if c["scope"] == "CHANGE" and mode != "change":
            errs.append(f"{c['code']}: scope CHANGE is for change mode; use FOCUS")
        if c["scope"] == "FOCUS" and mode == "change":
            errs.append(f"{c['code']}: scope FOCUS is for question and research modes; use CHANGE")
        if c["kind"] == "store" and not c["code"].startswith("D"):
            errs.append(f"{c['code']}: stores use the D prefix")
    per_lane = {}
    for c in m["components"]:
        per_lane[c["lane"]] = per_lane.get(c["lane"], 0) + 1
    for lane, n in per_lane.items():
        if n > 7:
            errs.append(f"lane {lane} has {n} components; the plan fits 7. Split the subject or move CONTEXT items to the register")

    for i in m["interfaces"]:
        for end in ("from", "to"):
            if i[end] not in comps:
                errs.append(f"{i['code']}: {end} {i[end]} is not a component")
        for k, pre in (("timeout", "T"), ("retries", "N")):
            v = i.get(k)
            if v and v not in ("[TBD]", "none", "unbounded") and v not in budgets:
                errs.append(f"{i['code']}: {k} {v} is not a budget")
        if i["style"] == "data" and i["to"] in comps and comps[i["to"]]["kind"] not in ("store", "queue", "managed"):
            errs.append(f"{i['code']}: data interfaces point at a store, queue or managed service")

    on_plan = {c["code"] for c in m["components"] if c.get("plan", True)}
    for f in m["flows"]:
        for s in f["steps"]:
            if s not in ifaces:
                errs.append(f"flow {f['id']}: step {s} is not an interface")
            elif f.get("critical") and (not ifaces[s].get("plan", True) or not {ifaces[s]["from"], ifaces[s]["to"]} <= on_plan):
                errs.append(f"flow {f['id']}: critical step {s} must be on the plan (plan: false on it or an end)")
    if sum(1 for f in m["flows"] if f.get("critical")) != 1:
        errs.append("mark exactly one flow as critical")

    for b in m.get("boundaries", []):
        for mem in b["members"]:
            if mem not in comps:
                errs.append(f"{b['code']}: member {mem} is not a component")

    for b in m.get("budgets", []):
        f = b.get("formula")
        if f:
            refs = set(re.findall(r"\b[TLNM][0-9]+\b", f)) - {b["code"]}
            missing = refs - set(budgets)
            if missing:
                errs.append(f"{b['code']}: formula uses unknown codes {sorted(missing)}")
            if any(budgets[r]["confidence"] == "TBD" for r in refs - missing) and b["value"] != "[TBD]":
                errs.append(f"{b['code']}: an input is [TBD], so the value must be [TBD]")

    for kind, items in (("component", m["components"]), ("interface", m["interfaces"]),
                        ("boundary", m.get("boundaries", [])), ("budget", m.get("budgets", []))):
        for o in items:
            if o["confidence"] != "TBD" and not o["evidence"]:
                errs.append(f"{o['code']}: {kind} has no evidence; add a tag or mark it TBD")
            for t in o["evidence"]:
                if t not in tags:
                    errs.append(f"{o['code']}: evidence tag {t} is not in the register")
            if o["confidence"] == "TBD" and o.get("value") not in (None, "[TBD]"):
                errs.append(f"{o['code']}: confidence TBD needs value [TBD]")
    for f in m["flows"]:
        for t in f["evidence"]:
            if t not in tags:
                errs.append(f"flow {f['id']}: evidence tag {t} is not in the register")

    ns = [n["n"] for n in m["notes"]]
    if ns != list(range(1, len(ns) + 1)):
        errs.append(f"notes must be numbered 1..n in order, got {ns}")
    for n in m["notes"]:
        if not n["evidence"]:
            errs.append(f"note {n['n']}: every note needs evidence")
        for t in n["evidence"]:
            if t not in tags:
                errs.append(f"note {n['n']}: evidence tag {t} is not in the register")
        if n.get("at") and n["at"] not in comps:
            errs.append(f"note {n['n']}: at {n['at']} is not a component")
        elif n.get("at") and n["at"] not in on_plan:
            errs.append(f"note {n['n']}: at {n['at']} is not on the plan")

    used = set()
    for o in m["components"] + m["interfaces"] + m.get("boundaries", []) + m.get("budgets", []) + m["flows"] + m["notes"]:
        used.update(o.get("evidence", []))
    unused = tags - used
    if unused:
        errs.append(f"evidence tags not used by any item: {sorted(unused)}")

    v = m["views"]
    if not any(f["id"] == v["sequence_flow"] for f in m["flows"]):
        errs.append(f"views.sequence_flow {v['sequence_flow']} is not a flow id")
    if v["detail"] == "failure" and v.get("failure_interface") not in ifaces:
        errs.append("views.failure_interface must name an interface")
    if v["detail"] == "blast_radius":
        br = v.get("blast_radius")
        if not br:
            errs.append("views.blast_radius is required for detail blast_radius")
        else:
            for k in ("E1", "E2", "E3"):
                for c in br[k]:
                    if c not in comps and c not in ifaces:
                        errs.append(f"blast_radius {k}: {c} is not a component or interface")

    # Table space: the right column fits about TABLE_ROWS rows. Budgets show only when a drawn code uses them.
    vis_i = [i for i in m["interfaces"] if i.get("plan", True) and {i["from"], i["to"]} <= on_plan]
    used_b = {i.get(k) for i in vis_i for k in ("timeout", "retries")} | {f["code"] for f in m["key_figures"]}
    for n in m["notes"]:
        used_b |= set(re.findall(r"\b[TLNM][0-9]+\b", n["text"]))
    rows = len(on_plan) + len(vis_i) + len(m.get("boundaries", [])) + sum(1 for b in m.get("budgets", []) if b["code"] in used_b)
    if rows > TABLE_ROWS:
        errs.append(f"sheet tables need about {rows} rows; the right column fits {TABLE_ROWS}. Set plan: false on CONTEXT leaves or repeated edges, or split the subject")

    for path, s in strings({k: v for k, v in m.items() if k != "evidence"}):
        for rx, what in SENSITIVE:
            if rx.search(s):
                errs.append(f"{path}: looks like a {what}: {s[:60]!r}")
    for e in m["evidence"]:
        for rx, what in SENSITIVE[:4] + SENSITIVE[5:6]:
            if rx.search(e["locator"]) or rx.search(e.get("what", "")):
                errs.append(f"evidence {e['tag']}: looks like a {what}")
    for path, s in strings(m):
        bad = sorted(set(OUTSIDE_FONT.findall(s)))
        if bad:
            errs.append(f"{path}: characters the sheet font cannot show: {' '.join(f'U+{ord(c):04X}' for c in bad)}")
    return errs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("model")
    ap.add_argument("-o", "--out")
    a = ap.parse_args()
    src = Path(a.model)
    out = Path(a.out) if a.out else src.with_name(src.name.replace(".model.yaml", "").replace(".yaml", "") + ".html")
    m = yaml.safe_load(src.read_text())

    schema = json.loads(SCHEMA.read_text())
    errs = [f"schema: {'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
            for e in jsonschema.Draft202012Validator(schema).iter_errors(m)]
    if not errs:
        errs = model_checks(m)
    if errs:
        print(f"FAIL {src}: {len(errs)} problem(s)")
        for e in errs:
            print(f"  - {e}")
        sys.exit(1)

    data = json.dumps(m, ensure_ascii=False).replace("</", "<\\/")
    html = TEMPLATE.read_text().replace("/*__FONT__*/", base64.b64encode(FONT.read_bytes()).decode()).replace("/*__MODEL__*/", data).replace("/*__TITLE__*/", m["subject"]["title"] + " · System Drawing")
    out.write_text(html)
    print(f"OK {src} -> {out}")


if __name__ == "__main__":
    main()
