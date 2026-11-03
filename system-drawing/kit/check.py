#!/usr/bin/env python3
"""Render a built sheet and run the sheet checks.

Usage: check.py <sheet.html> [--png <sheet.png>]

Renders at 1800 px wide in headless Chromium, saves a full-page screenshot, and exits 1
if any check fails. Needs the Python playwright package with Chromium installed.

Readability checks:
- No text is truncated (no ellipsis on the sheet).
- No text overlaps other text.
- Text inside a box or table cell keeps PAD clear of its edges.
- No text crosses a line, an edge, a frame or the border of a box. A box that holds the
  text counts as its container, so text wider than its box or cell fails as an overflow.
  An edge label may sit on its own edge.

Edge legibility rules (context plan):
- Parallel segments of two edges run at least MIN_GAP apart where they overlap.
- No edge runs along a boundary frame line closer than MIN_FRAME_GAP.
- Edge ends on one box sit at least MIN_PORT apart.
- No edge passes through a box other than its own two ends.
- An edge bends at most MAX_BENDS times.
- The plan, the details and the box sides stay within their space; otherwise the subject is too dense
  and must be split, or CONTEXT items moved to the register.
"""
import argparse
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

GRAYS = {"#ececec", "#ffffff", "#111111", "#444444", "#7a7a7a", "#f3f3f3", "#e6e6e6"}
BARE = re.compile(r"\b\d+(?:\.\d+)?\s?(?:ms|s|sec|secs|min|mins|h|kb|mb|gb|%|rps|req/s|tokens?)\b", re.I)
SHRINK = 1.0  # glyph boxes include side bearings; ignore contact inside this margin
PAD = 2  # text keeps this much clear space from the side edges of its box or cell
MIN_GAP = 8
MIN_FRAME_GAP = 6
MIN_PORT = 12
MAX_BENDS = 4

PROBE = r"""
() => {
  const sv = document.getElementById('sh1');
  const inv = sv.getScreenCTM().inverse();
  const pt = (m, x, y) => { const p = sv.createSVGPoint(); p.x = x; p.y = y; const q = p.matrixTransform(m); return [q.x, q.y]; };
  const toSvg = el => inv.multiply(el.getScreenCTM());
  const aabb = ps => { const xs = ps.map(p => p[0]), ys = ps.map(p => p[1]);
    return {x: Math.min(...xs), y: Math.min(...ys), w: Math.max(...xs) - Math.min(...xs), h: Math.max(...ys) - Math.min(...ys)}; };
  const inDefs = el => !!el.closest('defs');
  const texts = [...sv.querySelectorAll('text')].filter(t => !inDefs(t)).map(t => {
    const b = t.getBBox(), m = toSvg(t);
    const box = aabb([pt(m, b.x, b.y), pt(m, b.x + b.width, b.y), pt(m, b.x, b.y + b.height), pt(m, b.x + b.width, b.y + b.height)]);
    return Object.assign(box, {s: t.textContent, c: t.getAttribute('class') || '', edge: t.dataset.edge || ''});
  });
  const shapes = [];
  sv.querySelectorAll('line, polyline, rect, circle').forEach(el => {
    if (inDefs(el)) return;
    const m = toSvg(el), tag = el.tagName, c = el.getAttribute('class') || '';
    let ps = [], closed = false;
    if (tag === 'line') ps = [pt(m, +el.getAttribute('x1'), +el.getAttribute('y1')), pt(m, +el.getAttribute('x2'), +el.getAttribute('y2'))];
    else if (tag === 'polyline') ps = el.getAttribute('points').trim().split(/\s+/).map(q => q.split(',').map(Number)).map(([x, y]) => pt(m, x, y));
    else if (tag === 'rect') { const x = +el.getAttribute('x'), y = +el.getAttribute('y'), w = +el.getAttribute('width'), h = +el.getAttribute('height');
      ps = [pt(m, x, y), pt(m, x + w, y), pt(m, x + w, y + h), pt(m, x, y + h)]; closed = true; }
    else { const cx = +el.getAttribute('cx'), cy = +el.getAttribute('cy'), r = +el.getAttribute('r');
      ps = [pt(m, cx - r, cy - r), pt(m, cx + r, cy - r), pt(m, cx + r, cy + r), pt(m, cx - r, cy + r)]; closed = true; }
    shapes.push({tag, c, closed, ps, box: aabb(ps), code: el.dataset.code || '', node: el.dataset.node || ''});
  });
  const nodes = [...sv.querySelectorAll('rect[data-node]')].map(r => ({code: r.dataset.node, x: +r.getAttribute('x'), y: +r.getAttribute('y'), w: +r.getAttribute('width'), h: +r.getAttribute('height')}));
  const edges = [...sv.querySelectorAll('polyline[data-code]')].map(p => ({code: p.dataset.code, from: p.dataset.from, to: p.dataset.to,
    pts: p.getAttribute('points').trim().split(/\s+/).map(q => q.split(',').map(Number))}));
  return {texts, shapes, nodes, edges, tableBottom: +sv.getAttribute('data-table-bottom'),
    skipped: sv.getAttribute('data-skipped-frames') || '', cramped: sv.hasAttribute('data-stack-cramped'),
    dense: sv.getAttribute('data-plan-dense') || '', tracks: sv.getAttribute('data-gutter-tracks') || '', detailDense: sv.hasAttribute('data-detail-dense'), ports: sv.getAttribute('data-port-crowded') || '',
    frames: [...sv.querySelectorAll('rect.bnd, rect.dep')].map(r => ({x: +r.getAttribute('x'), y: +r.getAttribute('y'), w: +r.getAttribute('width'), h: +r.getAttribute('height')}))};
}
"""


def shrink(b, d=SHRINK):
    return {"x": b["x"] + d, "y": b["y"] + d, "w": max(0, b["w"] - 2 * d), "h": max(0, b["h"] - 2 * d)}


def overlap(a, b):
    w = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    h = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    return w * h if w > 0 and h > 0 else 0


def contains(outer, inner, tol=0.5):
    return (outer["x"] - tol <= inner["x"] and outer["y"] - tol <= inner["y"]
            and inner["x"] + inner["w"] <= outer["x"] + outer["w"] + tol
            and inner["y"] + inner["h"] <= outer["y"] + outer["h"] + tol)


def seg_rect(p, q, r):
    """Liang-Barsky clip: does segment p-q pass through the open rectangle r?"""
    x0, y0, x1, y1 = r["x"], r["y"], r["x"] + r["w"], r["y"] + r["h"]
    if r["w"] <= 0 or r["h"] <= 0:
        return False
    dx, dy = q[0] - p[0], q[1] - p[1]
    t0, t1 = 0.0, 1.0
    for pk, qk in ((-dx, p[0] - x0), (dx, x1 - p[0]), (-dy, p[1] - y0), (dy, y1 - p[1])):
        if pk == 0:
            if qk <= 0:
                return False
        else:
            t = qk / pk
            if pk < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
            if t0 >= t1:
                return False
    return True


def segments(s):
    ps = s["ps"]
    pairs = list(zip(ps, ps[1:]))
    if s["closed"]:
        pairs.append((ps[-1], ps[0]))
    return pairs


def seg_hits(p, q, r, pad=2):
    return seg_rect(p, q, {"x": r["x"] + pad, "y": r["y"] + pad, "w": r["w"] - 2 * pad, "h": r["h"] - 2 * pad})


def describe(s):
    if s["code"]:
        return f"edge {s['code']}"
    if s["node"]:
        return f"box {s['node']}"
    return f"{s['tag']}.{s['c'] or 'plain'}"


def readability(T, shapes):
    fails = []
    for t in T:
        if "…" in t["s"] or "..." in t["s"]:
            fails.append(f"truncated text: {t['s']!r}")
    for i, t in enumerate(T):
        a = shrink(t)
        for u in T[i + 1:]:
            if overlap(a, shrink(u)) > 1:
                fails.append(f"text overlaps text: {t['s']!r} and {u['s']!r}")
    for t in T:
        a = shrink(t)
        hits = []
        for s in shapes:
            if s["closed"] and contains(s["box"], t):
                inner = {"x": s["box"]["x"] + PAD, "y": s["box"]["y"], "w": s["box"]["w"] - 2 * PAD, "h": s["box"]["h"]}  # glyph boxes already carry vertical leading
                if s["tag"] == "rect" and s["box"]["w"] > 20 and s["box"]["h"] > 13 and not contains(inner, t, tol=0):
                    hits.append("touches the edge of " + describe(s))
                continue  # its container (box, cell, circle)
            if s["code"] and s["code"] == t["edge"]:
                continue  # an edge label sits on its own edge
            cx, cy = t["x"] + t["w"] / 2, t["y"] + t["h"] / 2
            if s["closed"] and s["tag"] == "rect" and s["box"]["x"] < cx < s["box"]["x"] + s["box"]["w"] \
                    and s["box"]["y"] < cy < s["box"]["y"] + s["box"]["h"] and s["box"]["w"] > 20:
                hits.append("overflows " + describe(s))  # its box, but the text spills past an edge
                continue
            if any(seg_rect(p, q, a) for p, q in segments(s)):
                inside = s["closed"] and s["box"]["x"] < t["x"] + t["w"] / 2 < s["box"]["x"] + s["box"]["w"] \
                    and s["box"]["y"] < t["y"] + t["h"] / 2 < s["box"]["y"] + s["box"]["h"]
                hits.append(("overflows " if inside else "crosses ") + describe(s))
        for h in sorted(set(hits)):
            fails.append(f"text {h}: {t['s']!r}")
    return fails


def edge_rules(edges, nodes, frames):
    fails, crossings = [], 0
    segs = []
    for e in edges:
        pts = e["pts"]
        if len(pts) - 2 > MAX_BENDS:
            fails.append(f"edge {e['code']} bends {len(pts) - 2} times (max {MAX_BENDS})")
        for p, q in zip(pts, pts[1:]):
            segs.append((e["code"], p, q))
            for n in nodes:
                if n["code"] not in (e["from"], e["to"]) and seg_hits(p, q, n):
                    fails.append(f"edge {e['code']} passes through box {n['code']}")
    fseg = []
    for f in frames:
        x0, y0, x1, y1 = f["x"], f["y"], f["x"] + f["w"], f["y"] + f["h"]
        fseg += [((x0, y0), (x1, y0)), ((x0, y1), (x1, y1)), ((x0, y0), (x0, y1)), ((x1, y0), (x1, y1))]

    def par(p, q, r, s, gap):
        """Gap between two parallel axis-aligned segments that overlap by more than 4 px, else None."""
        if p[1] == q[1] and r[1] == s[1]:
            ov = min(max(p[0], q[0]), max(r[0], s[0])) - max(min(p[0], q[0]), min(r[0], s[0]))
            d = abs(p[1] - r[1])
        elif p[0] == q[0] and r[0] == s[0]:
            ov = min(max(p[1], q[1]), max(r[1], s[1])) - max(min(p[1], q[1]), min(r[1], s[1]))
            d = abs(p[0] - r[0])
        else:
            return None
        return d if ov > 4 and d < gap else None

    seen = set()
    for i, (a, p, q) in enumerate(segs):
        for b, r, s in segs[i + 1:]:
            if a == b:
                continue
            d = par(p, q, r, s, MIN_GAP)
            if d is not None and (a, b) not in seen:
                seen.add((a, b))
                fails.append(f"edges {a} and {b} run {d:.0f} px apart (min {MIN_GAP})")
            hor1, hor2 = p[1] == q[1], r[1] == s[1]
            if hor1 != hor2:
                h, v = ((p, q), (r, s)) if hor1 else ((r, s), (p, q))
                if min(h[0][0], h[1][0]) < v[0][0] < max(h[0][0], h[1][0]) and min(v[0][1], v[1][1]) < h[0][1] < max(v[0][1], v[1][1]):
                    crossings += 1
        for fp, fq in fseg:
            d = par(p, q, fp, fq, MIN_FRAME_GAP)
            if d is not None:
                fails.append(f"edge {a} runs {d:.0f} px from a boundary frame line (min {MIN_FRAME_GAP})")
    ends = []
    for e in edges:
        ends += [(e["code"], e["pts"][0]), (e["code"], e["pts"][-1])]
    for i, (a, p) in enumerate(ends):
        for b, q in ends[i + 1:]:
            if a != b and ((p[0] == q[0] and abs(p[1] - q[1]) < MIN_PORT) or (p[1] == q[1] and abs(p[0] - q[0]) < MIN_PORT)):
                fails.append(f"edges {a} and {b} meet a box {max(abs(p[0]-q[0]), abs(p[1]-q[1])):.0f} px apart (min {MIN_PORT})")
    return sorted(set(fails)), crossings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sheet")
    ap.add_argument("--png")
    a = ap.parse_args()
    sheet = Path(a.sheet).resolve()
    png = Path(a.png) if a.png else sheet.with_suffix(".png")
    html = sheet.read_text()
    fails, warns = [], []

    with sync_playwright() as pw:
        b = pw.chromium.launch()
        page = b.new_page(viewport={"width": 1800, "height": 1200}, device_scale_factor=1)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(sheet.as_uri())
        page.wait_for_function("document.body.dataset.ready === '1'", timeout=15000)
        page.evaluate("document.fonts.ready")
        d = page.evaluate(PROBE)
        font = page.evaluate("[...document.fonts].some(f => f.family.replace(/['\"]/g, '') === 'Arimo' && f.status === 'loaded')")
        page.screenshot(path=str(png), full_page=True)
        b.close()

    fails += [f"page error: {e}" for e in errors]
    if not font:
        fails.append("the embedded Arimo font did not load; rebuild the sheet with build.py")
    T = [t for t in d["texts"] if t["s"].strip()]
    for t in T:
        if t["x"] < 22 or t["y"] < 22 or t["x"] + t["w"] > 1678 or t["y"] + t["h"] > 1038:
            fails.append(f"text in the sheet margin: {t['s']!r}")
        if t["c"] not in ("tc", "th") and BARE.search(t["s"]):
            fails.append(f"bare value on the drawing (use a code): {t['s']!r}")
    fails += readability(T, d["shapes"])
    if d["tableBottom"] > 1032:
        fails.append(f"tables run into the sheet margin (bottom {d['tableBottom']:.0f}); split the subject or trim rows")
    ef, crossings = edge_rules(d["edges"], d["nodes"], d["frames"])
    fails += ef
    if crossings > len(d["edges"]):
        warns.append(f"{crossings} edge crossings for {len(d['edges'])} edges; reorder components or lanes in the model")
    if d["dense"]:
        fails.append(f"context plan needs {d['dense']} px of height (tracks per gutter: {d['tracks']}); the subject is too dense. Split it or move CONTEXT items to the register")
    if d["detailDense"]:
        fails.append("Detail A or B does not fit; shorten the critical flow or the failure text")
    if d["ports"]:
        fails.append(f"too many edges on one side of: {d['ports'].strip()}; edge ends would sit closer than the port spacing")
    for hx in set(m.lower() for m in re.findall(r"#[0-9a-fA-F]{6}\b", html)):
        if hx not in GRAYS:
            fails.append(f"color outside the gray tokens: {hx}")
    for hx in re.findall(r"#[0-9a-fA-F]{3}\b(?![0-9a-fA-F])", html):
        fails.append(f"short hex color, use a gray token: {hx}")
    if d["cramped"]:
        fails.append("notes leave too little room for the layer stack; shorten or remove notes")
    if d["skipped"]:
        warns.append(f"boundary frames not drawn: {d['skipped']}. The register still lists them")

    for w in sorted(set(warns)):
        print(f"  warn: {w}")
    if fails:
        print(f"FAIL {sheet.name}: {len(fails)} problem(s), screenshot {png}")
        for f in fails:
            print(f"  - {f}")
        sys.exit(1)
    print(f"OK {sheet.name}: {len(T)} labels, {len(d['edges'])} edges, {crossings} crossings, {len(set(warns))} warning(s), screenshot {png}")


if __name__ == "__main__":
    main()
