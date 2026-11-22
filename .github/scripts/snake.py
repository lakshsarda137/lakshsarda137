"""Draw a snake that eats the GitHub contribution graph, without leaving it.

Route: the snake eats every green square. Each day (seeded by the date) it tries a
few random starting squares; for each it plans the order to eat things in (nearest
first, rescuing squares about to be cut off) and then tidies that order so nothing is
left behind for a long trip later. It keeps the plan with the least walking over
empty squares, skipping plans that eat the board in a left-to-right wave. It only
moves between neighbouring days, prefers paths over food, and avoids its own body.

Motion: it moves at one steady speed (variable speed and chewing pauses read as
lag). It gets fatter as it eats and slimmer as it crosses
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
STEP_MS = 120              # one move, constant
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

# Route
CANDIDATES = 8             # random starts tried per day; the tidiest plan wins
MAX_WAVE = 0.65            # reject plans whose eat order tracks the week this closely

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


def manhattan(a, b):
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def plan_order(start, food, rng):
    """Order to eat squares in: nearest next; on a tie, squares about to be cut off."""
    left, order = set(food) - {start}, [start]
    while left:
        head = order[-1]
        nearest = min(manhattan(head, f) for f in left)
        pool = [f for f in left if manhattan(head, f) == nearest]
        lonely = {f: sum(n in left for n in adjacent(f)) for f in pool}
        least = min(lonely.values())
        nxt = rng.choice(sorted(f for f in pool if lonely[f] == least))
        order.append(nxt)
        left.discard(nxt)
    return order


def tidy(order, longest=3):
    """Or-opt: move runs of up to `longest` squares to wherever they make the whole
    route shorter. This is what stops squares being left behind for a long trip later."""
    def gain_spot(o, i, j):
        a, b = o[i - 1], o[j] if j < len(o) else None
        cost = manhattan(a, o[i]) + (manhattan(o[j - 1], b) - manhattan(a, b) if b else 0)
        rest = o[:i] + o[j:]
        for k in range(len(rest)):
            p, q = rest[k], rest[k + 1] if k + 1 < len(rest) else None
            for seg in (o[i:j], o[i:j][::-1]):
                add = manhattan(p, seg[0]) + (manhattan(seg[-1], q) - manhattan(p, q) if q else 0)
                if add < cost:
                    return rest[:k + 1] + seg + rest[k + 1:]
        return None

    improved = True
    while improved:
        improved = False
        for size in range(1, longest + 1):
            for i in range(1, len(order) - size + 1):
                better = gain_spot(order, i, i + size)
                if better:
                    order, improved = better, True
                    break
            if improved:
                break
    return order


def distances_to(target, cells, blocked):
    dist, queue = {target: 0}, deque([target])
    while queue:
        cur = queue.popleft()
        for n in adjacent(cur):
            if n in cells and n not in dist and n not in blocked:
                dist[n] = dist[cur] + 1
                queue.append(n)
    return dist


def walk(grid, order, rng):
    """Follow the plan square by square: shortest way to the next uneaten target,
    preferring steps onto food, not through its own body."""
    cells = set(grid)
    food = {pos for pos, level in grid.items() if level}
    path = [order[0]]
    food.discard(order[0])
    for target in order[1:]:
        if target not in food:
            continue                          # already eaten on the way
        dist = distances_to(target, cells, set(path[-len(SEGMENTS):-1]) - {target})
        if path[-1] not in dist:              # boxed in by own body: allow crossing it
            dist = distances_to(target, cells, set())
        while path[-1] != target:
            here = path[-1]
            steps = [n for n in adjacent(here) if dist.get(n) == dist[here] - 1]
            rng.shuffle(steps)
            nxt = max(steps, key=lambda n: grid[n] if n in food else -1)
            path.append(nxt)
            food.discard(nxt)
    return path


def score(grid, path):
    """(looks like a wave, empty steps + 3 * longest empty stretch): lower is better."""
    eaten, empty, stretch, longest = {}, 0, 0, 0
    for t, pos in enumerate(path):
        if grid.get(pos) and pos not in eaten:
            eaten[pos], stretch = t, 0
        else:
            empty, stretch = empty + 1, stretch + 1
            longest = max(longest, stretch)
    return (abs(spearman(list(eaten.values()), [w for w, _ in eaten])) > MAX_WAVE,
            empty + 3 * longest)


def spearman(xs, ys):
    def ranks(v):
        r = [0] * len(v)
        for k, i in enumerate(sorted(range(len(v)), key=lambda i: v[i])):
            r[i] = k
        return r
    n = len(xs)
    if n < 3:
        return 0.0
    return 1 - 6 * sum((a - b) ** 2 for a, b in zip(ranks(xs), ranks(ys))) / (n * (n * n - 1))


def hunt(grid, seed):
    """Route over neighbouring days that eats every green square."""
    food = sorted(pos for pos, level in grid.items() if level)
    if not food:
        return [min(grid)]
    best = None
    for k in range(CANDIDATES):
        rng = random.Random(f"{seed}/{k}")
        path = walk(grid, tidy(plan_order(rng.choice(food), food, rng)), rng)
        s = score(grid, path)
        if best is None or s < best[0]:
            best = (s, path)
    return best[1]


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


def timeline(path, extra):
    """Head arrival time at each path index, plus `extra` trailing moves so the tail
    catches up with the head at the end."""
    return [FADE_IN_MS + i * STEP_MS for i in range(len(path) + extra)]


def pct(ms, total):
    return f"{ms / total * 100:.4f}".rstrip("0").rstrip(".") + "%"


# ----------------------------------------------------------------- render

def render(grid, weeks, path, theme):
    times = timeline(path, extra=0)   # shrink starts the moment the head stops; body slurps in
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
            # Empty squares just bounce as the ripple passes.
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
    assert all(manhattan(a, b) == 1 for a, b in zip(path, path[1:])), "path jumped"
    assert {p for p, level in grid.items() if level} <= set(path), "left squares uneaten"

    os.makedirs(args.out, exist_ok=True)
    for name, theme in THEMES.items():
        svg = render(grid, weeks, path, theme)
        with open(os.path.join(args.out, name), "w", encoding="utf-8") as fh:
            fh.write(svg)
        print(f"wrote {name}: {len(svg) // 1024} KB, {len(path)} moves")


if __name__ == "__main__":
    main()
