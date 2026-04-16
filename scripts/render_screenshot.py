"""
render_screenshot.py -- Capture a pyvis HTML graph exactly as rendered.

Loads the HTML in a headless Chromium browser, lets vis.js finish
its physics simulation, then screenshots the canvas. Output is pixel-
identical to what you see in the browser.

Pyvis renders to <canvas>, which is raster. True SVG export is
impossible; this is the faithful alternative.

Usage:
    python scripts/render_screenshot.py visualizations/knowledge_graph.html
    python scripts/render_screenshot.py visualizations/expertise_map.html \
        --width 2400 --height 1600 --wait 8
    python scripts/render_screenshot.py visualizations/*.html  # batch

Requirements:
    pip install playwright
    playwright install chromium
"""

import argparse
import logging
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


_FILTER_JS = """
({ minSize, keepLargest }) => {
    if (!window.nodes || !window.edges || !window.network) {
        return { error: "pyvis globals (nodes/edges/network) not found" };
    }

    // Build adjacency. vis.DataSet#forEach yields (item, id)
    const adj = {};
    window.edges.forEach(e => {
        (adj[e.from] = adj[e.from] || []).push(e.to);
        (adj[e.to] = adj[e.to] || []).push(e.from);
    });

    // Connected components via DFS
    const seen = new Set();
    const comps = [];
    window.nodes.forEach(n => {
        if (seen.has(n.id)) return;
        const comp = new Set();
        const stack = [n.id];
        while (stack.length) {
            const x = stack.pop();
            if (seen.has(x)) continue;
            seen.add(x);
            comp.add(x);
            (adj[x] || []).forEach(y => stack.push(y));
        }
        comps.push(comp);
    });

    // Decide which components to keep
    let keep;
    if (keepLargest) {
        comps.sort((a, b) => b.size - a.size);
        keep = comps[0] || new Set();
    } else {
        keep = new Set();
        comps.forEach(c => { if (c.size >= minSize) c.forEach(id => keep.add(id)); });
    }

    const dropNodes = [];
    window.nodes.forEach(n => { if (!keep.has(n.id)) dropNodes.push(n.id); });
    const dropEdges = [];
    window.edges.forEach(e => {
        if (!keep.has(e.from) || !keep.has(e.to)) dropEdges.push(e.id);
    });

    window.nodes.remove(dropNodes);
    window.edges.remove(dropEdges);
    window.network.stabilize(400);
    window.network.fit({ animation: false });

    return {
        components: comps.length,
        kept: keep.size,
        droppedNodes: dropNodes.length,
        droppedEdges: dropEdges.length,
    };
}
"""


def capture(
    html: Path,
    out: Path,
    width: int,
    height: int,
    wait_seconds: float,
    device_scale: float,
    min_component: int,
    keep_largest: bool,
) -> None:
    url = html.resolve().as_uri()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=device_scale,
        )
        page = context.new_page()
        page.goto(url, wait_until="networkidle")

        # Initial physics settle (partial) before filtering
        page.wait_for_timeout(int(wait_seconds * 1000 * 0.4))

        # Filter out small components if requested
        if keep_largest or min_component > 1:
            stats = page.evaluate(
                _FILTER_JS,
                {"minSize": min_component, "keepLargest": keep_largest},
            )
            if "error" in stats:
                logger.warning(f"  {html.name}: filter skipped — {stats['error']}")
            else:
                logger.info(
                    f"  {html.name}: {stats['components']} component(s), "
                    f"kept {stats['kept']} nodes, "
                    f"dropped {stats['droppedNodes']} nodes / "
                    f"{stats['droppedEdges']} edges"
                )
            # Let physics re-settle after node removal
            page.wait_for_timeout(int(wait_seconds * 1000 * 0.6))
        else:
            page.wait_for_timeout(int(wait_seconds * 1000 * 0.6))

        # Hide scrollbars + make canvas fill the page
        page.add_style_tag(content="""
            html, body { margin: 0; padding: 0; overflow: hidden; background: #fff; }
            #mynetwork { width: 100vw !important; height: 100vh !important; border: 0 !important; }
        """)
        page.wait_for_timeout(500)  # reflow

        out.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(out), full_page=False, omit_background=False)
        browser.close()
    logger.info(f"Wrote {out} ({width}x{height} @ {device_scale}x)")


def main():
    parser = argparse.ArgumentParser(
        description="Screenshot a pyvis HTML graph to PNG.",
    )
    parser.add_argument("html", nargs="+", type=Path,
                        help="One or more pyvis HTML files.")
    parser.add_argument("--out", type=Path,
                        help="Output PNG (single-input only). "
                             "Batch mode writes next to each HTML.")
    parser.add_argument("--width", type=int, default=1600,
                        help="Viewport width in CSS pixels (default 1600).")
    parser.add_argument("--height", type=int, default=1100,
                        help="Viewport height (default 1100).")
    parser.add_argument("--scale", type=float, default=2.0,
                        help="Device scale factor — 2 = retina (default 2).")
    parser.add_argument("--wait", type=float, default=6.0,
                        help="Seconds to wait for physics to settle (default 6).")
    parser.add_argument("--min-component", type=int, default=1,
                        help="Drop connected components with fewer than N nodes "
                             "(default 1 = keep all). Use 3 to hide orphan pairs.")
    parser.add_argument("--keep-largest", action="store_true",
                        help="Keep ONLY the largest connected component. "
                             "Overrides --min-component.")
    args = parser.parse_args()

    if args.out and len(args.html) > 1:
        parser.error("--out only works with a single input file.")

    for path in args.html:
        if not path.exists():
            logger.error(f"Not found: {path}")
            continue
        out = args.out or path.with_suffix(".png")
        capture(
            path, out, args.width, args.height, args.wait, args.scale,
            args.min_component, args.keep_largest,
        )


if __name__ == "__main__":
    main()
