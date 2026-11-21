"""Draw a snake that eats the GitHub contribution graph, without leaving it.

The snake sweeps the graph column by column (down one week, up the next), so it
never needs to step outside the grid. Each square empties when the head reaches
it. At the end of the loop the snake fades out while the squares refill, then it
fades back in at the start.

Writes snake.svg (light) and snake-dark.svg (dark). Standard library only.

    GITHUB_TOKEN=... python3 snake.py --user <login> --out dist
"""
import argparse
import json
import os
import urllib.request

PITCH = 16            # px between squares, as on github.com
CELL = 12             # square size
STEP_MS = 150         # time per move
FADE_MS = 2400        # end-of-loop fade (snake out, squares refill)
SEGMENTS = [14, 13, 12, 11, 10]   # head first; sizes in px

LEVELS = {"NONE": 0, "FIRST_QUARTILE": 1, "SECOND_QUARTILE": 2, "THIRD_QUARTILE": 3, "FOURTH_QUARTILE": 4}

THEMES = {
    "snake.svg": {"empty": "#ebedf0", "levels": ["#9be9a8", "#40c463", "#30a14e", "#216e39"],
                  "snake": "#1f6feb", "border": "#1b1f230f"},
    # Transparent, so it sits on whichever dark theme the viewer uses. Every colour is
    # checked against GitHub dark (#0d1117), dark dimmed (#22272e) and dark high contrast
    # (#0a0c10): empty squares >= 1.7:1 on all three, each green step >= 1.5:1 from the last.
    "snake-dark.svg": {"empty": "#454c56", "levels": ["#1a7f37", "#30a14e", "#56d364", "#aff5b4"],
                       "snake": "#79c0ff", "border": "#ffffff0d"},
}

QUERY = """query($login: String!) {
  user(login: $login) { contributionsCollection { contributionCalendar {
    weeks { contributionDays { weekday contributionLevel } } } } } }"""


def fetch_grid(login, token):
    """Returns {(week, weekday): level} for every day shown on the profile."""
    req = urllib.request.Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": QUERY, "variables": {"login": login}}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                 "User-Agent": "profile-snake"},
    )
    with urllib.request.urlopen(req, timeout=30) as res:
        body = json.load(res)
    if body.get("errors"):
        raise SystemExit(f"GitHub API error: {body['errors']}")
    weeks = body["data"]["user"]["contributionsCollection"]["contributionCalendar"]["weeks"]
    return {(w, d["weekday"]): LEVELS[d["contributionLevel"]]
            for w, week in enumerate(weeks) for d in week["contributionDays"]}


def sweep(grid):
    """Column-by-column path that stays on the board.

    The last week goes top-down so the path ends on today's square. Blank spots
    in the partial first/last week are skipped at the ends of the path; in the
    middle of the first column they are crossed (still inside the board)."""
    weeks = max(w for w, _ in grid) + 1
    path = []
    for w in range(weeks):
        down = (weeks - 1 - w) % 2 == 0
        rows = range(7) if down else range(6, -1, -1)
        path.extend((w, d) for d in rows)
    # Trim blank spots before the first real day and after the last one.
    while path[0] not in grid:
        path.pop(0)
    while path[-1] not in grid:
        path.pop()
    return weeks, path


def pct(ms, total):
    return f"{ms / total * 100:.3f}".rstrip("0").rstrip(".") + "%"


def render(grid, weeks, path, theme):
    moves = len(path) - 1 + len(SEGMENTS) - 1          # tail catches up with the head at the end
    move_ms = moves * STEP_MS
    total = move_ms + FADE_MS
    fade_start, fade_end = move_ms + 200, move_ms + FADE_MS - 600

    css = [
        f":root{{--ce:{theme['empty']};--cs:{theme['snake']};--cb:{theme['border']};"
        + "".join(f"--c{i + 1}:{c};" for i, c in enumerate(theme["levels"])) + "}",
        f".c{{fill:var(--ce);stroke:var(--cb);stroke-width:1px;animation:none {total}ms linear infinite}}",
        f".s{{fill:var(--cs);animation:none {total}ms linear infinite}}",
        f".g{{animation:g {total}ms linear infinite}}",
        f"@keyframes g{{0%{{opacity:0}}{pct(400, total)},{pct(fade_start, total)}{{opacity:1}}"
        f"{pct(fade_start + 500, total)},100%{{opacity:0}}}}",
    ]

    # Squares: full colour until the head arrives, then empty, then refill during the fade.
    eaten_at = {}
    for t, pos in enumerate(path):
        eaten_at.setdefault(pos, t * STEP_MS)
    rects = []
    for (w, d), level in sorted(grid.items()):
        x, y = w * PITCH + 2, d * PITCH + 2
        if level == 0:
            rects.append(f'<rect class="c" x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="2"/>')
            continue
        name = f"e{w}_{d}"
        at = eaten_at[(w, d)]
        css.append(
            f"@keyframes {name}{{0%,{pct(at, total)}{{fill:var(--c{level})}}"
            f"{pct(at + 1, total)},{pct(fade_start, total)}{{fill:var(--ce)}}"
            f"{pct(fade_end, total)},100%{{fill:var(--c{level})}}}}"
            f".{name}{{fill:var(--c{level});animation-name:{name}}}")
        rects.append(f'<rect class="c {name}" x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="2"/>')

    # Snake: segment i trails the head by i moves.
    segs = []
    for i, size in enumerate(SEGMENTS):
        frames = []
        last = None
        for t in range(moves + 1):
            w, d = path[min(max(t - i, 0), len(path) - 1)]
            here = (w * PITCH, d * PITCH)
            if here != last:
                frames.append(f"{pct(t * STEP_MS, total)}{{transform:translate({here[0]}px,{here[1]}px)}}")
                last = here
        frames.append(f"100%{{transform:translate({last[0]}px,{last[1]}px)}}")
        css.append(f"@keyframes s{i}{{{''.join(frames)}}}.s{i}{{animation-name:s{i}}}")
        off = (PITCH - size) / 2
        r = round(size * 0.32, 1)
        segs.append(f'<rect class="s s{i}" x="{off:g}" y="{off:g}" width="{size}" height="{size}" rx="{r}"/>')

    css.append("@media (prefers-reduced-motion: reduce){*{animation:none!important}.g{opacity:0}}")
    width, height = weeks * PITCH, 7 * PITCH
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
            f'width="{width}" height="{height}"><desc>Contribution snake</desc>'
            f"<style>{''.join(css)}</style>{''.join(rects)}"
            f'<g class="g">{"".join(reversed(segs))}</g></svg>')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", required=True)
    ap.add_argument("--out", default="dist")
    args = ap.parse_args()
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit("GITHUB_TOKEN is not set")

    grid = fetch_grid(args.user, token)
    weeks, path = sweep(grid)
    assert all(0 <= w < weeks and 0 <= d < 7 for w, d in path), "path left the board"
    assert all(pos in set(path) for pos, lvl in grid.items() if lvl), "a square was never visited"

    os.makedirs(args.out, exist_ok=True)
    for name, theme in THEMES.items():
        svg = render(grid, weeks, path, theme)
        with open(os.path.join(args.out, name), "w", encoding="utf-8") as fh:
            fh.write(svg)
        print(f"wrote {name}: {len(svg) // 1024} KB, {len(path)} moves")


if __name__ == "__main__":
    main()
