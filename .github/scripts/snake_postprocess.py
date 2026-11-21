"""Post-process Platane/snk SVGs for the profile README.

1. Keep the snake on the board: snk routes the snake through the empty strip
   above the grid and past its right edge. Clip the snake to the grid so it
   slides under the edge instead of wandering in the margin.
2. Trim the now-empty top margin from the viewBox.
3. Stop the animation for visitors who prefer reduced motion.
"""
import re
import sys

CELL = 16  # snk grid pitch in px


def process(svg: str) -> str:
    cells = [(float(x), float(y)) for x, y in re.findall(r'<rect class="c[^"]*" x="([\d.]+)" y="([\d.]+)"', svg)]
    if not cells:
        raise SystemExit("no grid cells found; snk output format changed?")
    width = max(x for x, _ in cells) - 2 + CELL   # cells sit at 16n+2
    height = max(y for _, y in cells) - 2 + CELL

    snake = re.findall(r'<rect class="s[ "][^>]*/>', svg)
    if not snake:
        raise SystemExit("no snake segments found; snk output format changed?")
    start = svg.index(snake[0])
    end = svg.index(snake[-1]) + len(snake[-1])
    clip = f'<defs><clipPath id="board"><rect x="0" y="0" width="{width:g}" height="{height:g}"/></clipPath></defs>'
    svg = svg[:start] + clip + '<g clip-path="url(#board)">' + svg[start:end] + "</g>" + svg[end:]

    # Trim the top margin the snake used to travel through.
    m = re.search(r'viewBox="(-?[\d.]+) (-?[\d.]+) ([\d.]+) ([\d.]+)"', svg)
    x, y, w, h = map(float, m.groups())
    new_y = -8.0
    if y < new_y:
        h -= new_y - y
        svg = svg.replace(m.group(0), f'viewBox="{x:g} {new_y:g} {w:g} {h:g}"', 1)
        svg = re.sub(r'(<svg[^>]*?)height="[\d.]+"', rf'\g<1>height="{h:g}"', svg, count=1)

    rule = "@media (prefers-reduced-motion: reduce){*{animation:none!important}}"
    if "</style>" not in svg:
        raise SystemExit("no <style> block found")
    return svg.replace("</style>", rule + "</style>", 1)


if __name__ == "__main__":
    for path in sys.argv[1:]:
        with open(path, encoding="utf-8") as fh:
            out = process(fh.read())
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(out)
        print(f"processed {path}")
