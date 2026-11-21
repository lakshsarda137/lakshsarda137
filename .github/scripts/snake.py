"""Draw a snake that eats the GitHub contribution graph, without leaving it.

The snake hunts: it heads for the nearest uneaten square (on a tie, squares about
to be cut off first, so it doesn't strand stragglers, then darker squares), now and
then gets distracted by a juicier square a few steps away, and
avoids running through its own body. It only ever moves between neighbouring days,
so it never leaves the grid. The route is seeded by the date, so it changes daily.
Each square empties when the head reaches it. The snake gets fatter as it eats and slimmer as it crosses empty squares; each
body segment keeps the weight the head had at that spot, so meals travel down the body. At the end of the loop the snake fades out while the squares refill, then it
fades back in at the start.

Writes snake.svg (light) and snake-dark.svg (dark). Standard library only.

    GITHUB_TOKEN=... python3 snake.py --user <login> --out dist
"""
import argparse
import datetime
import json
import os
import random
import urllib.request
from collections import deque

PITCH = 16            # px between squares, as on github.com
CELL = 12             # square size
STEP_MS = 150         # time per move
FADE_MS = 2400        # end-of-loop fade (snake out, squares refill)
SEGMENTS = [1.0, 0.93, 0.86, 0.79, 0.71]   # head-to-tail taper
BASE = 14             # head size at normal weight, px

# Weight: eating a green square fattens the snake (darker squares more); crossing an
# empty square slims it. Size = taper * BASE * (THIN + RANGE * weight), so a starving
# head is ~8px and a stuffed one ~17px, spilling slightly over its neighbours.
START_WEIGHT = 0.3
GAIN_PER_LEVEL = 0.035
LOSS_PER_EMPTY = 0.12
THIN, RANGE = 0.6, 0.65

# Hunting: chance per target of chasing a juicier square within DISTRACT_RADIUS
# instead of the nearest one.
DISTRACT_CHANCE = 0.2
DISTRACT_RADIUS = 3

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


def neighbours(pos, cells, rng):
    w, d = pos
    out = [n for n in ((w + 1, d), (w - 1, d), (w, d + 1), (w, d - 1)) if n in cells]
    rng.shuffle(out)
    return out


def bfs(start, cells, blocked, rng):
    """Shortest-path tree from start over real days, avoiding blocked squares."""
    prev, dist, queue = {start: None}, {start: 0}, deque([start])
    while queue:
        cur = queue.popleft()
        for n in neighbours(cur, cells, rng):
            if n not in prev and n not in blocked:
                prev[n], dist[n] = cur, dist[cur] + 1
                queue.append(n)
    return prev, dist


def hunt(grid, seed):
    """Route that eats every green square, moving only between neighbouring days."""
    rng = random.Random(seed)
    cells = set(grid)
    food = {pos for pos, level in grid.items() if level}
    path = [min(cells)]                      # start on the oldest day
    food.discard(path[0])
    while food:
        head = path[-1]
        body = set(path[-len(SEGMENTS):-1])  # don't slither through yourself
        prev, dist = bfs(head, cells, body, rng)
        reachable = [f for f in food if f in dist]
        if not reachable:                    # boxed in by own body: allow crossing it
            prev, dist = bfs(head, cells, set(), rng)
            reachable = [f for f in food if f in dist]
        nearest = min(dist[f] for f in reachable)
        if rng.random() < DISTRACT_CHANCE:
            pool = [f for f in reachable if dist[f] <= nearest + DISTRACT_RADIUS]
        else:
            pool = [f for f in reachable if dist[f] == nearest]

        # Prefer squares with few uneaten neighbours (about to be stranded), then darker ones.
        def appeal(f, food=food):
            w, d = f
            return (-sum(n in food for n in ((w + 1, d), (w - 1, d), (w, d + 1), (w, d - 1))), grid[f])

        best = max(appeal(f) for f in pool)
        target = rng.choice([f for f in pool if appeal(f) == best])
        steps = []
        while target != head:
            steps.append(target)
            target = prev[target]
        for pos in reversed(steps):
            path.append(pos)
            food.discard(pos)
    return path


def weights(grid, path):
    """Snake weight (0..1) after the head reaches each step of the path."""
    w, out, eaten = START_WEIGHT, [], set()
    for pos in path:
        level = grid.get(pos, 0)
        if level and pos not in eaten:
            w = min(1.0, w + GAIN_PER_LEVEL * level)
            eaten.add(pos)
        else:
            w = max(0.0, w - LOSS_PER_EMPTY)
        out.append(w)
    return out


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
        f".s{{fill:var(--cs);transform-box:fill-box;transform-origin:center;animation:none {total}ms linear infinite}}",
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

    # Snake: segment i trails the head by i moves and keeps the weight the head had there.
    weight = weights(grid, path)
    segs = []
    for i, taper in enumerate(SEGMENTS):
        frames = []
        last = None
        for t in range(moves + 1):
            k = min(max(t - i, 0), len(path) - 1)
            w, d = path[k]
            scale = round(taper * (THIN + RANGE * weight[k]), 3)
            state = (w * PITCH, d * PITCH, scale)
            if state != last:
                frames.append(f"{pct(t * STEP_MS, total)}{{transform:translate({state[0]}px,{state[1]}px) scale({scale})}}")
                last = state
        frames.append(f"100%{{transform:translate({last[0]}px,{last[1]}px) scale({last[2]})}}")
        css.append(f"@keyframes s{i}{{{''.join(frames)}}}.s{i}{{animation-name:s{i}}}")
        off = (PITCH - BASE) / 2
        segs.append(f'<rect class="s s{i}" x="{off:g}" y="{off:g}" width="{BASE}" height="{BASE}" rx="4.5"/>')

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
    ap.add_argument("--seed", help="route seed (default: today's date, so the route changes daily)")
    args = ap.parse_args()
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise SystemExit("GITHUB_TOKEN is not set")

    grid = fetch_grid(args.user, token)
    weeks = max(w for w, _ in grid) + 1
    path = hunt(grid, seed=args.seed or datetime.date.today().isoformat())
    assert all(pos in grid for pos in path), "path left the board"
    assert all(abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1 for a, b in zip(path, path[1:])), "path jumped"
    assert all(pos in set(path) for pos, lvl in grid.items() if lvl), "a square was never visited"

    os.makedirs(args.out, exist_ok=True)
    for name, theme in THEMES.items():
        svg = render(grid, weeks, path, theme)
        with open(os.path.join(args.out, name), "w", encoding="utf-8") as fh:
            fh.write(svg)
        print(f"wrote {name}: {len(svg) // 1024} KB, {len(path)} moves")


if __name__ == "__main__":
    main()
