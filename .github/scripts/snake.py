"""Draw a snake that eats the GitHub contribution graph, without leaving it.

Route: the snake starts on a random green square and hunts. Usually it heads for the
nearest uneaten square (on a tie, squares about to be cut off first, then darker
ones); sometimes it is distracted by a juicier square nearby; now and then it lunges
across the board for a dark square. It only moves between neighbouring days, so it
never leaves the grid, and it avoids running through its own body. It stops once
~90% is eaten, because chasing the last stragglers is a slow, starving tour. The
route is seeded by the date, so it changes every day.

Motion: it dashes over empty or already-eaten ground, moves slower while eating and
pauses to chew dark squares. It gets fatter as it eats and slimmer as it crosses
squares with nothing to eat; each body segment keeps the weight the head had at that
spot, so meals travel down the body as a bulge.

Finale: the snake shrinks away where it stopped and a ripple spreads out from that
spot. Eaten squares pop back with a little overshoot; the rest bounce as it passes.

Writes snake.svg (light) and snake-dark.svg (dark). Standard library only.

    GITHUB_TOKEN=... python3 snake.py --user <login> --out dist
"""
import argparse
import datetime
import json
import math
import os
import random
import urllib.request
from collections import deque

PITCH = 16                 # px between squares, as on github.com
CELL = 12                  # square size

# Timing (ms)
EAT_MS = 160               # move onto a square it eats
DASH_MS = 85               # move over empty or already-eaten ground
CHEW_MS = 320              # pause after eating a dark square (level 3-4)
FADE_IN_MS = 400           # snake fades in at the start of the loop
SHRINK_MS = 300            # snake shrinks away at the end
WAVE_MS_PER_CELL = 22      # ripple speed (per square of distance)
POP_MS = 420               # one square's pop / bounce
REST_MS = 1200             # full graph on screen before the next loop

# Body
SEGMENTS = [1.0, 0.93, 0.86, 0.79, 0.71]   # head-to-tail taper
BASE = 14                  # head size at normal weight, px

# Weight: eating a green square fattens the snake (darker squares more); crossing a
# square with nothing to eat slims it. Size = taper * BASE * (THIN + RANGE * weight),
# so a starving head is ~8px and a stuffed one ~17px, spilling over its neighbours.
START_WEIGHT = 0.3
GAIN_PER_LEVEL = 0.035
LOSS_PER_EMPTY = 0.09
THIN, RANGE = 0.6, 0.65

# Hunting. Measured on a dense graph, lunges cut how much the eating looks like a
# left-to-right wave from 0.55 to 0.25 (rank correlation of eat order vs week).
DISTRACT_CHANCE = 0.2      # chase a juicier square within DISTRACT_RADIUS of the nearest
DISTRACT_RADIUS = 3
LUNGE_CHANCE = 0.08        # lunge for a dark square at least LUNGE_MIN_DIST away
LUNGE_MIN_DIST = 8
EAT_FRACTION = 0.9

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


# ------------------------------------------------------------------ route

def adjacent(pos):
    w, d = pos
    return ((w + 1, d), (w - 1, d), (w, d + 1), (w, d - 1))


def bfs(start, cells, blocked, rng):
    """Shortest-path tree from start over real days, avoiding blocked squares."""
    prev, dist, queue = {start: None}, {start: 0}, deque([start])
    while queue:
        cur = queue.popleft()
        options = [n for n in adjacent(cur) if n in cells]
        rng.shuffle(options)                  # organic tie-breaking
        for n in options:
            if n not in prev and n not in blocked:
                prev[n], dist[n] = cur, dist[cur] + 1
                queue.append(n)
    return prev, dist


def hunt(grid, seed):
    """Route over neighbouring days that eats ~EAT_FRACTION of the green squares."""
    rng = random.Random(seed)
    cells = set(grid)
    food = {pos for pos, level in grid.items() if level}
    if not food:
        return [min(cells)]
    leave = len(food) - round(len(food) * EAT_FRACTION)
    path = [rng.choice(sorted(food))]
    food.discard(path[0])

    def appeal(f):
        # Few uneaten neighbours (about to be stranded) first, then darker.
        return (-sum(n in food for n in adjacent(f)), grid[f])

    while len(food) > leave:
        head = path[-1]
        prev, dist = bfs(head, cells, set(path[-len(SEGMENTS):-1]), rng)
        reachable = [f for f in food if f in dist]
        if not reachable:                     # boxed in by own body: allow crossing it
            prev, dist = bfs(head, cells, set(), rng)
            reachable = [f for f in food if f in dist]

        nearest = min(dist[f] for f in reachable)
        if rng.random() < LUNGE_CHANCE:
            far = [f for f in reachable if dist[f] >= LUNGE_MIN_DIST] or reachable
            target = rng.choices(far, weights=[grid[f] ** 2 for f in far])[0]
        else:
            reach = DISTRACT_RADIUS if rng.random() < DISTRACT_CHANCE else 0
            pool = [f for f in reachable if dist[f] <= nearest + reach]
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


# ----------------------------------------------------------------- timing

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


def timeline(grid, path, extra):
    """Head arrival time at each path index (plus `extra` trailing dash moves so the
    tail catches up), and how long the snake pauses there to chew."""
    times, holds, eaten, t = [], [], set(), FADE_IN_MS
    for i in range(len(path) + extra):
        pos = path[min(i, len(path) - 1)]
        level = grid.get(pos, 0)
        fresh = i < len(path) and level and pos not in eaten
        if i:
            t += holds[-1] + (EAT_MS if fresh else DASH_MS)
        eaten.add(pos)
        times.append(t)
        holds.append(CHEW_MS if fresh and level >= 3 else 0)
    return times, holds


def pct(ms, total):
    return f"{ms / total * 100:.4f}".rstrip("0").rstrip(".") + "%"


# ----------------------------------------------------------------- render

def render(grid, weeks, path, theme):
    times, holds = timeline(grid, path, extra=len(SEGMENTS) - 1)
    end = times[-1]
    origin = path[-1]
    wave_start = end + SHRINK_MS // 2

    def ripple_at(pos):
        return wave_start + round(math.dist(pos, origin) * WAVE_MS_PER_CELL)

    total = max(ripple_at(c) for c in grid) + POP_MS + REST_MS
    P = lambda ms: pct(ms, total)  # noqa: E731
    ox, oy = origin[0] * PITCH + PITCH / 2, origin[1] * PITCH + PITCH / 2

    css = [
        f":root{{--ce:{theme['empty']};--cs:{theme['snake']};--cb:{theme['border']};"
        + "".join(f"--c{i + 1}:{c};" for i, c in enumerate(theme["levels"])) + "}",
        f".c{{fill:var(--ce);stroke:var(--cb);stroke-width:1px;transform-box:fill-box;"
        f"transform-origin:center;animation:none {total}ms linear infinite}}",
        f".s{{fill:var(--cs);transform-box:fill-box;transform-origin:center;"
        f"animation:none {total}ms linear infinite}}",
        # Whole snake: fade in at the start, shrink into its last square at the end.
        f".g{{transform-box:view-box;transform-origin:{ox:g}px {oy:g}px;animation:g {total}ms linear infinite}}",
        f"@keyframes g{{0%{{opacity:0;transform:scale(1)}}{P(FADE_IN_MS)},{P(end)}{{opacity:1;transform:scale(1)}}"
        f"{P(end + SHRINK_MS)},100%{{opacity:0;transform:scale(0)}}}}",
    ]

    eaten_at = {}
    for t, pos in enumerate(path):
        if grid.get(pos):
            eaten_at.setdefault(pos, times[t])

    rects = []
    for (w, d), level in sorted(grid.items()):
        x, y = w * PITCH + 2, d * PITCH + 2
        r = ripple_at((w, d))
        name = f"e{w}_{d}"
        if (w, d) in eaten_at:
            # Full colour -> eaten (grey) -> when the ripple arrives: dip, then pop back
            # in colour with an overshoot. Every keyframe sets transform explicitly so the
            # browser never interpolates a size change across the whole loop.
            at = eaten_at[(w, d)]
            css.append(
                f"@keyframes {name}{{0%,{P(at)}{{fill:var(--c{level});transform:scale(1)}}"
                f"{P(at + 1)},{P(r)}{{fill:var(--ce);transform:scale(1)}}"
                f"{P(r + POP_MS * .2)}{{fill:var(--ce);transform:scale(.6)}}"
                f"{P(r + POP_MS * .55)}{{fill:var(--c{level});transform:scale(1.3)}}"
                f"{P(r + POP_MS)},100%{{fill:var(--c{level});transform:scale(1)}}}}")
            base = f"fill:var(--c{level});"
        else:
            # Empty squares and uneaten stragglers just bounce as the ripple passes.
            css.append(
                f"@keyframes {name}{{0%,{P(r)}{{transform:scale(1)}}"
                f"{P(r + POP_MS * .4)}{{transform:scale(1.22)}}{P(r + POP_MS)},100%{{transform:scale(1)}}}}")
            base = f"fill:var(--c{level});" if level else ""
        css.append(f".{name}{{{base}animation-name:{name}}}")
        rects.append(f'<rect class="c {name}" x="{x}" y="{y}" width="{CELL}" height="{CELL}" rx="2"/>')

    # Snake: segment i trails the head by i moves and keeps the weight the head had there.
    weight = weights(grid, path)
    moves = len(times) - 1
    segs = []
    for i, taper in enumerate(SEGMENTS):
        frames = []
        for t in range(moves + 1):
            k = min(max(t - i, 0), len(path) - 1)
            w, d = path[k]
            scale = round(taper * (THIN + RANGE * weight[k]), 3)
            frame = f"{{transform:translate({w * PITCH}px,{d * PITCH}px) scale({scale})}}"
            frames.append(f"{P(times[t])}{frame}")
            if holds[t]:                      # whole snake pauses while the head chews
                frames.append(f"{P(times[t] + holds[t])}{frame}")
        frames.insert(0, f"0%{frames[0][frames[0].index('{'):]}")
        frames.append(f"100%{frame}")
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

    os.makedirs(args.out, exist_ok=True)
    for name, theme in THEMES.items():
        svg = render(grid, weeks, path, theme)
        with open(os.path.join(args.out, name), "w", encoding="utf-8") as fh:
            fh.write(svg)
        print(f"wrote {name}: {len(svg) // 1024} KB, {len(path)} moves")


if __name__ == "__main__":
    main()
