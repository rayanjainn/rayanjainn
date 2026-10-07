#!/usr/bin/env python3
"""Render the animated SVGs used by the profile README.

Runs hourly in GitHub Actions. Live numbers come from the GitHub GraphQL API
(GITHUB_TOKEN) and, when WAKATIME_API_KEY is set, from WakaTime.
`python3 scripts/generate.py --demo` renders with sample data, no network.
"""

import base64
import datetime as dt
import json
import os
import sys
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

USER = "rayanjainn"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets"

SANS = "'Inter','Segoe UI',-apple-system,BlinkMacSystemFont,'Helvetica Neue',Arial,sans-serif"
MONO = "'JetBrains Mono','SF Mono',ui-monospace,Menlo,Consolas,'DejaVu Sans Mono',monospace"
CW = 0.6  # monospace advance per em; text uses textLength so this is exact

BG0, BG1 = "#07080d", "#0e1120"
INK, DIM, FAINT = "#e6e9f5", "#8b93b8", "#4a5072"
VIOLET, CYAN, PINK, GREEN, AMBER = "#a78bfa", "#22d3ee", "#f472b6", "#34d399", "#fbbf24"

PROJECTS = [
    {
        "repo": "ferrite-browser",
        "name": "Ferrite",
        "kicker": "01 / flagship",
        "accent": "#fb923c",
        "lang": ("Rust", "#dea584"),
        "desc": [
            "A browser for AI agents, built in Rust on the Servo engine.",
            "It can't be talked into things: it predicts what a task needs,",
            "dry-runs the plan, compares, and asks before anything else runs.",
        ],
        "tags": ["servo", "prompt-injection defense", "sandboxed dry-runs", "sha-256 audit log"],
    },
    {
        "repo": "sentinel",
        "name": "Sentinel",
        "kicker": "02 / systems",
        "accent": GREEN,
        "lang": ("Rust", "#dea584"),
        "desc": [
            "Cross-platform system monitor and firewall that explains",
            "every process and connection in plain English.",
        ],
        "tags": ["tauri", "react", "macOS · linux · windows"],
    },
    {
        "repo": "duckops",
        "name": "DuckOps",
        "kicker": "03 / platform",
        "accent": CYAN,
        "lang": ("TypeScript", "#3178c6"),
        "desc": [
            "Pick a stack, get a repo, a k8s deploy, CI/CD and",
            "monitoring. An internal developer platform, automated.",
        ],
        "tags": ["kubernetes", "terraform", "ansible", "jenkins"],
    },
    {
        "repo": "devhub",
        "name": "DevHub",
        "kicker": "04 / dev tools",
        "accent": VIOLET,
        "lang": ("TypeScript", "#3178c6"),
        "desc": [
            "Postman + DevTools + Burp Suite in one workspace: API",
            "client, MITM proxy, fuzzer and an AI that knows your stack.",
        ],
        "tags": ["electron", "mitm proxy", "cdp", "chrome mv3"],
    },
    {
        "repo": "neurocred",
        "name": "NeuroCred",
        "kicker": "05 / ml",
        "accent": PINK,
        "lang": ("Python", "#3572a5"),
        "desc": [
            "A live digital twin and credit engine for small businesses:",
            "Monte Carlo risk, LLM reasoning, real-time anomaly detection.",
        ],
        "tags": ["redis streams", "llm", "next.js"],
    },
]

SKIP_LANGS = {"HTML", "CSS", "SCSS", "EJS", "Makefile", "Dockerfile", "Procfile", "Batchfile",
              "Jupyter Notebook", "MDX", "HCL", "Handlebars", "Nix", "Just"}


# --------------------------------------------------------------------------- data

def http_json(url, body=None, headers=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body else None,
                                 headers={"User-Agent": USER, **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def gql(token, query, variables=None):
    res = http_json("https://api.github.com/graphql", {"query": query, "variables": variables or {}},
                    {"Authorization": f"bearer {token}", "Content-Type": "application/json"})
    if res.get("errors"):
        raise RuntimeError(res["errors"])
    return res["data"]


def fetch_github(token):
    base = gql(token, """
    query($login: String!) {
      user(login: $login) {
        followers { totalCount }
        contributionsCollection { contributionYears }
        repositories(ownerAffiliations: OWNER, isFork: false, privacy: PUBLIC, first: 100) {
          totalCount
          nodes {
            name stargazerCount
            languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
              edges { size node { name color } }
            }
          }
        }
      }
    }""", {"login": USER})["user"]

    years = sorted(base["contributionsCollection"]["contributionYears"])
    parts = []
    for y in years:
        parts.append(f"""y{y}: contributionsCollection(from: "{y}-01-01T00:00:00Z", to: "{y}-12-31T23:59:59Z") {{
          contributionCalendar {{ totalContributions weeks {{ contributionDays {{ date contributionCount }} }} }}
        }}""")
    cal = gql(token, "query($login: String!) { user(login: $login) { %s } }" % "\n".join(parts),
              {"login": USER})["user"]

    days, per_year = {}, {}
    for y in years:
        c = cal[f"y{y}"]["contributionCalendar"]
        per_year[y] = c["totalContributions"]
        for w in c["weeks"]:
            for d in w["contributionDays"]:
                days[d["date"]] = d["contributionCount"]

    langs, colors = {}, {}
    stars, repo_stars = 0, {}
    for r in base["repositories"]["nodes"]:
        stars += r["stargazerCount"]
        repo_stars[r["name"]] = r["stargazerCount"]
        for e in r["languages"]["edges"]:
            n = e["node"]["name"]
            if n in SKIP_LANGS:
                continue
            langs[n] = langs.get(n, 0) + e["size"]
            colors[n] = e["node"]["color"] or "#888888"

    today = dt.date.today()
    cur, longest = streaks(days, today)
    return {
        "year": today.year,
        "year_total": per_year.get(today.year, 0),
        "all_time": sum(per_year.values()),
        "streak": cur,
        "longest": longest,
        "stars": stars,
        "last30": sum(c for d, c in days.items()
                      if 0 <= (today - dt.date.fromisoformat(d)).days < 30),
        "followers": base["followers"]["totalCount"],
        "repos": base["repositories"]["totalCount"],
        "langs": top_langs(langs, colors),
        "repo_stars": repo_stars,
    }


def streaks(days, today):
    ordered = sorted(d for d in days if d <= today.isoformat())
    longest = run = 0
    prev = None
    for d in ordered:
        date = dt.date.fromisoformat(d)
        if days[d] > 0:
            run = run + 1 if prev and (date - prev).days == 1 and run else 1
            longest = max(longest, run)
        else:
            run = 0
        prev = date
    cur = 0
    day = today if days.get(today.isoformat(), 0) > 0 else today - dt.timedelta(days=1)
    while days.get(day.isoformat(), 0) > 0:
        cur += 1
        day -= dt.timedelta(days=1)
    return cur, longest


def top_langs(sizes, colors, n=6):
    total = sum(sizes.values()) or 1
    ranked = sorted(sizes.items(), key=lambda kv: -kv[1])
    out = [(name, size / total, colors[name]) for name, size in ranked[:n]]
    rest = sum(s for _, s in ranked[n:]) / total
    if rest > 0.005:
        out.append(("Other", rest, "#3b4061"))
    return out


def fetch_wakatime(key):
    auth = base64.b64encode(key.encode()).decode()
    data = http_json("https://wakatime.com/api/v1/users/current/stats/last_7_days",
                     headers={"Authorization": f"Basic {auth}"})["data"]
    secs = data.get("total_seconds") or 0
    return int(secs) if secs else None


DEMO = {
    "year": dt.date.today().year, "year_total": 409, "all_time": 1287, "streak": 12, "longest": 41,
    "stars": 18, "followers": 64, "repos": 40, "last30": 96,
    "langs": [("TypeScript", .52, "#3178c6"), ("Rust", .18, "#dea584"), ("Python", .12, "#3572a5"),
              ("Swift", .06, "#f05138"), ("Go", .04, "#00add8"), ("Java", .03, "#b07219"),
              ("Other", .05, "#3b4061")],
    "repo_stars": {},
}


# --------------------------------------------------------------------------- svg helpers

def fmt(n):
    return f"{n:,}"


def mono(x, y, text, size, fill, extra=""):
    """Monospace text with an exact width, so clip/cursor maths line up on every OS."""
    w = len(text) * size * CW
    return (f'<text x="{x:.1f}" y="{y}" font-family="{MONO}" font-size="{size}" fill="{fill}" '
            f'textLength="{w:.1f}" lengthAdjust="spacingAndGlyphs" xml:space="preserve" {extra}>'
            f'{escape(text)}</text>')


def discrete(points, total):
    """points: [(t_seconds, value)] -> (keyTimes, values) for calcMode=discrete over `total`."""
    pts = sorted(points)
    kt, vs, last_t = [], [], -1.0
    for t, v in pts:
        k = round(t / total, 5)
        if k <= last_t:
            vs[-1] = v
            continue
        kt.append(k)
        vs.append(v)
        last_t = k
    if kt[0] != 0:
        kt.insert(0, 0.0)
        vs.insert(0, 0)
    return ";".join(f"{k:g}" for k in kt), ";".join(f"{v:g}" for v in vs)


def type_points(start, n, cw, cps, hold_until=None, erase_cps=None):
    pts = [(0, 0)]
    for i in range(1, n + 1):
        pts.append((start + i / cps, i * cw))
    if hold_until is not None:
        if erase_cps:
            for i in range(1, n + 1):
                pts.append((hold_until + i / erase_cps, (n - i) * cw))
        else:
            pts.append((hold_until, 0))
    return pts


def card_frame(w, h, title, right="", accent=VIOLET, uid="c"):
    return f"""
  <defs>
    <linearGradient id="{uid}bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#0d1020"/><stop offset="1" stop-color="#090a12"/>
    </linearGradient>
    <linearGradient id="{uid}edge" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="{w}" y2="{h}">
      <stop offset="0" stop-color="{accent}" stop-opacity=".9"/>
      <stop offset=".45" stop-color="#262a45" stop-opacity=".6"/>
      <stop offset="1" stop-color="{CYAN}" stop-opacity=".7"/>
      <animateTransform attributeName="gradientTransform" type="rotate"
        values="0 {w/2} {h/2};360 {w/2} {h/2}" dur="12s" repeatCount="indefinite"/>
    </linearGradient>
  </defs>
  <rect x="1" y="1" width="{w-2}" height="{h-2}" rx="16" fill="url(#{uid}bg)" stroke="url(#{uid}edge)" stroke-width="1.5"/>
  <line x1="1" y1="44" x2="{w-1}" y2="44" stroke="#1d2138"/>
  <circle cx="24" cy="22" r="6" fill="#ff5f57"/><circle cx="44" cy="22" r="6" fill="#febc2e"/><circle cx="64" cy="22" r="6" fill="#28c840"/>
  {mono(88, 27, title, 13, DIM)}
  {mono(w - 20 - len(right) * 12 * CW, 27, right, 12, FAINT) if right else ""}"""


def svg(w, h, body, style="", label=""):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'role="img" aria-label="{escape(label)}" fill="none">\n'
            f'<style>{style}</style>{body}\n</svg>\n')


# --------------------------------------------------------------------------- header

def header():
    W, H = 1200, 380
    phrases = [
        "building a browser AI agents can't be tricked by",
        "full-stack engineer · systems tinkerer",
        "rust  ·  typescript  ·  go  ·  swift  ·  python",
        "ship fast. break things. fix faster.",
    ]
    size, slot = 22, 6.5
    cw = size * CW
    total = slot * len(phrases)
    typing = []
    for i, p in enumerate(phrases):
        full = "❯ " + p
        n = len(full)
        x0 = W / 2 - n * cw / 2
        t0 = i * slot
        hold = t0 + slot - 0.2 - n / 70
        pts = type_points(t0 + 0.3, n, cw, 28, hold, 70)
        kt, vals = discrete(pts, total)
        cx_vals = ";".join(f"{x0 + float(v):.1f}" for v in vals.split(";"))
        vis_kt, vis_v = discrete([(0, 0), (t0, 1), (t0 + slot, 0)], total)
        typing.append(f"""
  <clipPath id="tc{i}"><rect x="{x0:.1f}" y="226" height="40" width="0">
    <animate attributeName="width" calcMode="discrete" dur="{total}s" repeatCount="indefinite" keyTimes="{kt}" values="{vals}"/>
  </rect></clipPath>
  <g opacity="0">
    <animate attributeName="opacity" calcMode="discrete" dur="{total}s" repeatCount="indefinite" keyTimes="{vis_kt}" values="{vis_v}"/>
    <g clip-path="url(#tc{i})">
      {mono(x0, 254, "❯", size, CYAN)}
      {mono(x0 + 2 * cw, 254, p, size, "#c9cee6")}
    </g>
    <rect class="cur" y="234" width="{cw * .55:.1f}" height="26" rx="2" fill="{PINK}" x="{x0:.1f}">
      <animate attributeName="x" calcMode="discrete" dur="{total}s" repeatCount="indefinite" keyTimes="{kt}" values="{cx_vals}"/>
    </rect>
  </g>""")

    style = f"""
    .blob {{ transform-box: fill-box; transform-origin: center; }}
    .b1 {{ animation: drift1 18s ease-in-out infinite alternate; }}
    .b2 {{ animation: drift2 22s ease-in-out infinite alternate; }}
    .b3 {{ animation: drift3 26s ease-in-out infinite alternate; }}
    @keyframes drift1 {{ to {{ transform: translate(260px, 60px) scale(1.25); }} }}
    @keyframes drift2 {{ to {{ transform: translate(-300px, -40px) scale(.85); }} }}
    @keyframes drift3 {{ to {{ transform: translate(-160px, 80px) scale(1.2); }} }}
    .cur {{ animation: blink 1s steps(1) infinite; }}
    @keyframes blink {{ 50% {{ opacity: 0; }} }}
    .rise {{ opacity: 0; animation: rise 1.1s cubic-bezier(.2,.8,.2,1) forwards; }}
    .d1 {{ animation-delay: .15s; }} .d2 {{ animation-delay: .45s; }} .d3 {{ animation-delay: .8s; }}
    @keyframes rise {{ from {{ opacity: 0; transform: translateY(14px); }} to {{ opacity: 1; transform: none; }} }}
    .pulse {{ transform-box: fill-box; transform-origin: center; animation: pulse 2s ease-out infinite; }}
    @keyframes pulse {{ from {{ transform: scale(1); opacity: .7; }} to {{ transform: scale(3); opacity: 0; }} }}
    .star {{ animation: tw 3s ease-in-out infinite; }}
    @keyframes tw {{ 50% {{ opacity: .15; }} }}
    """

    stars = []
    import random
    rnd = random.Random(7)
    for _ in range(46):
        x, y = rnd.uniform(20, W - 20), rnd.uniform(16, H - 16)
        r = rnd.choice([.6, .8, 1, 1.2])
        stars.append(f'<circle class="star" cx="{x:.0f}" cy="{y:.0f}" r="{r}" fill="#fff" opacity=".55" '
                     f'style="animation-delay:{rnd.uniform(0, 3):.2f}s;animation-duration:{rnd.uniform(2.5, 5):.1f}s"/>')

    pill = "open to collabs & interesting problems"
    pill_w = len(pill) * 12 * CW + 44
    px = W / 2 - pill_w / 2
    loc = ["mumbai, in", "raycode.tech", "rayansjain@gmail.com"]
    loc_gap = 34
    loc_w = sum(len(t) * 13 * CW for t in loc) + loc_gap * (len(loc) - 1)
    lx, loc_parts = W / 2 - loc_w / 2, []
    for i, t in enumerate(loc):
        if i:
            loc_parts.append(f'<circle cx="{lx - loc_gap / 2:.1f}" cy="318" r="2" fill="#6b7299"/>')
        loc_parts.append(mono(lx, 322, t, 13, "#6b7299"))
        lx += len(t) * 13 * CW + loc_gap

    body = f"""
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{BG1}"/><stop offset="1" stop-color="{BG0}"/></linearGradient>
    <radialGradient id="gv"><stop offset="0" stop-color="{VIOLET}" stop-opacity=".55"/><stop offset="1" stop-color="{VIOLET}" stop-opacity="0"/></radialGradient>
    <radialGradient id="gc"><stop offset="0" stop-color="{CYAN}" stop-opacity=".40"/><stop offset="1" stop-color="{CYAN}" stop-opacity="0"/></radialGradient>
    <radialGradient id="gp"><stop offset="0" stop-color="{PINK}" stop-opacity=".40"/><stop offset="1" stop-color="{PINK}" stop-opacity="0"/></radialGradient>
    <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M40 0H0V40" stroke="#ffffff" stroke-opacity=".045"/></pattern>
    <radialGradient id="fade" cx=".5" cy=".45" r=".6"><stop offset="0" stop-color="#fff"/><stop offset="1" stop-color="#000"/></radialGradient>
    <mask id="m"><rect width="{W}" height="{H}" fill="url(#fade)"/></mask>
    <clipPath id="frame"><rect width="{W}" height="{H}" rx="22"/></clipPath>
    <linearGradient id="name" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="{W}" y2="0" spreadMethod="reflect">
      <stop offset="0" stop-color="{CYAN}"/><stop offset=".35" stop-color="{VIOLET}"/>
      <stop offset=".65" stop-color="{PINK}"/><stop offset="1" stop-color="{CYAN}"/>
      <animateTransform attributeName="gradientTransform" type="translate" values="0 0;{W} 0" dur="9s" repeatCount="indefinite"/>
    </linearGradient>
    <linearGradient id="rim" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="{W}" y2="{H}">
      <stop offset="0" stop-color="{VIOLET}" stop-opacity=".8"/><stop offset=".5" stop-color="#ffffff" stop-opacity=".06"/><stop offset="1" stop-color="{CYAN}" stop-opacity=".8"/>
    </linearGradient>
  </defs>
  <g clip-path="url(#frame)">
    <rect width="{W}" height="{H}" fill="url(#bg)"/>
    <circle class="blob b1" cx="260" cy="120" r="260" fill="url(#gv)"/>
    <circle class="blob b2" cx="980" cy="260" r="280" fill="url(#gc)"/>
    <circle class="blob b3" cx="700" cy="40" r="220" fill="url(#gp)"/>
    <rect width="{W}" height="{H}" fill="url(#grid)" mask="url(#m)"/>
    {''.join(stars)}
  </g>
  <rect x=".75" y=".75" width="{W - 1.5}" height="{H - 1.5}" rx="22" stroke="url(#rim)" stroke-width="1.5"/>

  <g class="rise">
    <rect x="{px:.1f}" y="38" width="{pill_w:.1f}" height="30" rx="15" fill="#ffffff" fill-opacity=".05" stroke="#ffffff" stroke-opacity=".12"/>
    <circle class="pulse" cx="{px + 20:.1f}" cy="53" r="4" fill="{GREEN}"/>
    <circle cx="{px + 20:.1f}" cy="53" r="4" fill="{GREEN}"/>
    {mono(px + 34, 57.5, pill, 12, "#b7bdd8")}
  </g>
  <g class="rise d1">
    <text x="{W / 2}" y="178" text-anchor="middle" font-family="{SANS}" font-size="104" font-weight="800"
      letter-spacing="-3" fill="url(#name)">Rayan Jain</text>
  </g>
  {''.join(typing)}
  <g class="rise d3">
    {''.join(loc_parts)}
  </g>"""
    return svg(W, H, body, style, "Rayan Jain")


# --------------------------------------------------------------------------- terminal

def terminal():
    W, H = 600, 340
    cycle = 22.0
    size = 14.5
    cw = size * CW
    lines = [
        ("cmd", "whoami"),
        ("out", [("Rayan Jain", INK), (" · full-stack & systems engineer", DIM)]),
        ("cmd", "cat now.txt"),
        ("out", [("building ", DIM), ("ferrite", "#fb923c"), (": a rust browser for", DIM)]),
        ("out", [("AI agents that can't be prompt-injected", DIM)]),
        ("cmd", "ls ~/into"),
        ("out", [("rust/  ", "#dea584"), ("browsers/  ", CYAN), ("infra/  ", VIOLET), ("dev-tools/", GREEN)]),
        ("cmd", "echo $MOTTO"),
        ("out", [("ship fast. break things. fix faster.", PINK)]),
    ]
    end_fade = cycle - 0.8
    t = 0.6
    y = 76
    parts = []
    for kind, content in lines:
        if kind == "cmd":
            n = len(content)
            pts = type_points(t, n, cw, 16)
            pts.append((end_fade, 0))
            kt, vals = discrete(pts, cycle)
            vk, vv = discrete([(0, 0), (t - .05, 1), (end_fade, 0)], cycle)
            parts.append(f"""
  <g opacity="0"><animate attributeName="opacity" calcMode="discrete" dur="{cycle}s" repeatCount="indefinite" keyTimes="{vk}" values="{vv}"/>
    {mono(24, y, "❯", size, GREEN)}{mono(24 + 2 * cw, y, "~", size, CYAN)}
  </g>
  <clipPath id="l{y}"><rect x="{24 + 4 * cw:.1f}" y="{y - 16}" height="22" width="0">
    <animate attributeName="width" calcMode="discrete" dur="{cycle}s" repeatCount="indefinite" keyTimes="{kt}" values="{vals}"/>
  </rect></clipPath>
  <g clip-path="url(#l{y})">{mono(24 + 4 * cw, y, content, size, INK)}</g>""")
            t += n / 16 + 0.45
        else:
            x = 24 + 4 * cw
            spans = []
            for text, col in content:
                spans.append(mono(x, y, text, size, col))
                x += len(text) * cw
            vk, vv = discrete([(0, 0), (t, 1), (end_fade, 0)], cycle)
            parts.append(f"""
  <g opacity="0"><animate attributeName="opacity" calcMode="discrete" dur="{cycle}s" repeatCount="indefinite" keyTimes="{vk}" values="{vv}"/>
    {''.join(spans)}
  </g>""")
            t += 0.35
        y += 26
    # resting prompt with blinking cursor
    vk, vv = discrete([(0, 0), (t, 1), (end_fade, 0)], cycle)
    parts.append(f"""
  <g opacity="0"><animate attributeName="opacity" calcMode="discrete" dur="{cycle}s" repeatCount="indefinite" keyTimes="{vk}" values="{vv}"/>
    {mono(24, y, "❯", size, GREEN)}{mono(24 + 2 * cw, y, "~", size, CYAN)}
    <rect class="cur" x="{24 + 4 * cw:.1f}" y="{y - 13}" width="{cw:.1f}" height="17" rx="1.5" fill="{PINK}"/>
  </g>""")
    style = """.cur { animation: blink 1s steps(1) infinite; } @keyframes blink { 50% { opacity: 0; } }"""
    return svg(W, H, card_frame(W, H, "rayan@raycode: ~", "zsh", VIOLET, "t") + "".join(parts), style,
               "whoami: Rayan Jain, full-stack and systems engineer")


# --------------------------------------------------------------------------- stats

def stats(d, waka_secs, synced):
    W, H = 600, 340
    metrics = [
        (fmt(d["year_total"]), f"contributions in {d['year']}", CYAN),
        (fmt(d["all_time"]), "all-time contributions", VIOLET),
        (f"{d['streak']}d", "current streak", PINK),
        (f"{d['longest']}d", "longest streak", AMBER),
        (fmt(d["repos"]), "public repos", GREEN),
        (fmt(d["stars"]), "stars earned", "#fb923c"),
    ]
    if not d["streak"]:
        metrics[2] = (fmt(d["last30"]), "contributions, last 30d", PINK)
    if d["stars"] < 5:
        metrics[5] = (fmt(d["followers"]), "followers", "#fb923c")
    if waka_secs:
        h, m = divmod(waka_secs // 60, 60)
        metrics[4] = (f"{h}h {m:02d}m", "coded this week", GREEN)
    cells = []
    for i, (val, label, col) in enumerate(metrics):
        cx = 24 + (i % 3) * 188
        cy = 102 + (i // 3) * 82
        cells.append(f"""
  <g class="rise" style="animation-delay:{.15 + i * .12:.2f}s">
    <rect x="{cx}" y="{cy - 36}" width="176" height="68" rx="10" fill="#ffffff" fill-opacity=".025" stroke="#ffffff" stroke-opacity=".06"/>
    <rect x="{cx}" y="{cy - 36}" width="3" height="68" rx="1.5" fill="{col}"/>
    <text x="{cx + 16}" y="{cy}" font-family="{SANS}" font-size="27" font-weight="800" fill="{INK}">{escape(val)}</text>
    {mono(cx + 16, cy + 21, label, 11, DIM)}
  </g>""")

    bar_x, bar_y, bar_w = 24, 264, W - 48
    segs, legend = [], []
    x = bar_x
    for i, (name, frac, color) in enumerate(d["langs"]):
        w = max(frac * bar_w, 2)
        segs.append(f'<rect class="grow" style="animation-delay:{.9 + i * .1:.2f}s" x="{x:.1f}" y="{bar_y}" '
                    f'width="{w:.1f}" height="10" fill="{color}"/>')
        x += w
    lx, ly = bar_x, bar_y + 36
    for i, (name, frac, color) in enumerate(d["langs"][:6]):
        label = f"{name} {frac * 100:.0f}%"
        lw = len(label) * 11 * CW + 26
        if lx + lw > W - 20:
            break
        legend.append(f"""<g class="rise" style="animation-delay:{1.2 + i * .08:.2f}s">
    <circle cx="{lx + 5}" cy="{ly - 4}" r="4.5" fill="{color}"/>{mono(lx + 15, ly, label, 11, DIM)}</g>""")
        lx += lw

    style = """
    .rise { opacity: 0; animation: rise .9s cubic-bezier(.2,.8,.2,1) forwards; }
    @keyframes rise { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: none; } }
    .grow { transform-box: fill-box; transform-origin: left; transform: scaleX(0); animation: grow 1.1s cubic-bezier(.2,.8,.2,1) forwards; }
    @keyframes grow { to { transform: scaleX(1); } }
    .live { animation: live 1.6s ease-in-out infinite; } @keyframes live { 50% { opacity: .25; } }
    """
    body = card_frame(W, H, "~/stats", "", CYAN, "s") + f"""
  <circle class="live" cx="{W - 24 - len('synced ' + synced) * 12 * CW - 12:.1f}" cy="22.5" r="3.5" fill="{GREEN}"/>
  {mono(W - 24 - len('synced ' + synced) * 12 * CW, 27, 'synced ' + synced, 12, FAINT)}
  {''.join(cells)}
  {mono(bar_x, bar_y - 12, "languages", 11, FAINT)}
  <clipPath id="barclip"><rect x="{bar_x}" y="{bar_y}" width="{bar_w}" height="10" rx="5"/></clipPath>
  <rect x="{bar_x}" y="{bar_y}" width="{bar_w}" height="10" rx="5" fill="#ffffff" fill-opacity=".05"/>
  <g clip-path="url(#barclip)">{''.join(segs)}</g>
  {''.join(legend)}"""
    return svg(W, H, body, style, f"GitHub stats: {d['year_total']} contributions in {d['year']}")


# --------------------------------------------------------------------------- projects

def pipeline(x0, y0, accent):
    """Ferrite's guard loop: predict -> dry-run -> compare -> consent, with a travelling pulse."""
    steps = ["predict", "dry-run", "compare", "consent"]
    gap, r = 118, 26
    nodes, labels = [], []
    pts = [(x0 + i * gap, y0) for i in range(len(steps))]
    for i, ((cx, cy), s) in enumerate(zip(pts, steps)):
        nodes.append(f"""
    <circle cx="{cx}" cy="{cy}" r="{r}" fill="#0d1020" stroke="{accent}" stroke-opacity=".35"/>
    <circle cx="{cx}" cy="{cy}" r="{r}" fill="{accent}" fill-opacity="0" stroke="{accent}" stroke-width="2" opacity="0">
      <animate attributeName="opacity" values="0;0;1;0;0" keyTimes="0;{i * .22:.2f};{i * .22 + .08:.2f};{i * .22 + .2:.2f};1" dur="4.5s" repeatCount="indefinite"/>
    </circle>
    <text x="{cx}" y="{cy + 5}" text-anchor="middle" font-family="{MONO}" font-size="15" fill="{INK}">{i + 1}</text>""")
        labels.append(mono(cx - len(s) * 11 * CW / 2, cy + r + 22, s, 11, DIM))
    path = f"M{pts[0][0]} {y0} H{pts[-1][0]}"
    return f"""
  <path d="{path}" stroke="#262a45" stroke-width="2"/>
  <path d="{path}" stroke="{accent}" stroke-width="2" stroke-dasharray="6 10" opacity=".6">
    <animate attributeName="stroke-dashoffset" values="0;-32" dur="1s" repeatCount="indefinite"/>
  </path>
  <circle r="5" fill="{accent}">
    <animateMotion path="{path}" dur="4.5s" repeatCount="indefinite" keyPoints="0;1;1" keyTimes="0;.85;1" calcMode="linear"/>
    <animate attributeName="opacity" values="1;1;0" keyTimes="0;.85;1" dur="4.5s" repeatCount="indefinite"/>
  </circle>
  {''.join(nodes)}
  {''.join(labels)}
  <rect x="{x0 - 40}" y="{y0 + 70}" width="{gap * 3 + 80}" height="30" rx="8" fill="#ffffff" fill-opacity=".03" stroke="#ffffff" stroke-opacity=".07"/>
  {mono(x0 + gap * 1.5 - 33 * 11 * CW / 2, y0 + 90, "anything off-plan waits for a yes", 11, FAINT)}"""


def project(p, stars, hero=False):
    W, H = (1200, 260) if hero else (600, 240)
    a = p["accent"]
    uid = "h" if hero else "p"
    lang, lcol = p["lang"]
    desc_size = 17 if hero else 14.5
    lines = "".join(
        f'<text x="32" y="{(124 if hero else 118) + i * (desc_size + 9)}" font-family="{SANS}" font-size="{desc_size}" fill="#aab1d0">{escape(t)}</text>'
        for i, t in enumerate(p["desc"]))
    tx, ty = 32, H - 34
    tags = []
    for t in p["tags"]:
        w = len(t) * 11 * CW + 20
        tags.append(f'<rect x="{tx:.1f}" y="{ty - 16}" width="{w:.1f}" height="24" rx="12" fill="{a}" fill-opacity=".08" stroke="{a}" stroke-opacity=".3"/>'
                    + mono(tx + 10, ty, t, 11, a))
        tx += w + 8
    star_txt = f"★ {stars}" if stars else ""
    meta_w = len(lang) * 12 * CW + 22 + (len(star_txt) * 12 * CW + 18 if stars else 0)
    mx = W - 30 - meta_w
    style = f"""
    .sweep {{ animation: sweep 7s ease-in-out infinite; }}
    @keyframes sweep {{ 0%, 55% {{ transform: translateX(-{W // 2}px); }} 100% {{ transform: translateX({W + 200}px); }} }}
    .orb {{ transform-box: fill-box; transform-origin: center; animation: orb 9s ease-in-out infinite alternate; }}
    @keyframes orb {{ to {{ transform: translate(-60px, 30px) scale(1.2); }} }}
    """
    body = f"""
  <defs>
    <linearGradient id="{uid}bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0e1122"/><stop offset="1" stop-color="#08090f"/></linearGradient>
    <radialGradient id="{uid}orb"><stop offset="0" stop-color="{a}" stop-opacity=".28"/><stop offset="1" stop-color="{a}" stop-opacity="0"/></radialGradient>
    <linearGradient id="{uid}sh" x1="0" x2="1"><stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset=".5" stop-color="#fff" stop-opacity=".06"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>
    <linearGradient id="{uid}edge" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="{W}" y2="{H}">
      <stop offset="0" stop-color="{a}"/><stop offset=".5" stop-color="#262a45" stop-opacity=".5"/><stop offset="1" stop-color="{a}" stop-opacity=".4"/>
      <animateTransform attributeName="gradientTransform" type="rotate" values="0 {W / 2} {H / 2};360 {W / 2} {H / 2}" dur="10s" repeatCount="indefinite"/>
    </linearGradient>
    <clipPath id="{uid}clip"><rect width="{W}" height="{H}" rx="18"/></clipPath>
  </defs>
  <g clip-path="url(#{uid}clip)">
    <rect width="{W}" height="{H}" fill="url(#{uid}bg)"/>
    <circle class="orb" cx="{W - 120}" cy="40" r="{220 if hero else 170}" fill="url(#{uid}orb)"/>
    <rect class="sweep" x="0" y="0" width="{W // 3}" height="{H}" fill="url(#{uid}sh)" transform="skewX(-20)"/>
  </g>
  <rect x=".75" y=".75" width="{W - 1.5}" height="{H - 1.5}" rx="18" stroke="url(#{uid}edge)" stroke-width="1.5"/>
  <g>
    {mono(32, 40, p["kicker"], 11.5, a, 'letter-spacing="1"')}
    <circle cx="{mx + 6}" cy="35" r="5" fill="{lcol}"/>
    {mono(mx + 17, 40, lang, 12, DIM)}
    {mono(mx + 17 + len(lang) * 12 * CW + 18, 40, star_txt, 12, DIM) if stars else ""}
    <text x="30" y="{88 if hero else 82}" font-family="{SANS}" font-size="{40 if hero else 30}" font-weight="800" letter-spacing="-1" fill="{INK}">{escape(p["name"])}</text>
  </g>
  <g>{lines}</g>
  <g>{''.join(tags)}</g>
  {pipeline(W - 440, 128, a) if hero else ""}"""
    return svg(W, H, body, style, f'{p["name"]}: {" ".join(p["desc"])}')


# --------------------------------------------------------------------------- footer

def footer():
    W, H = 1200, 120
    waves = []
    for i, (col, op, dur, amp) in enumerate([(VIOLET, .5, 9, 14), (CYAN, .35, 12, 10), (PINK, .3, 15, 18)]):
        y = 58 + i * 6
        d1 = f"M0 {y} C 200 {y - amp}, 400 {y + amp}, 600 {y} S 1000 {y - amp}, 1200 {y}"
        d2 = f"M0 {y} C 200 {y + amp}, 400 {y - amp}, 600 {y} S 1000 {y + amp}, 1200 {y}"
        waves.append(f'<path d="{d1}" stroke="{col}" stroke-opacity="{op}" stroke-width="1.5">'
                     f'<animate attributeName="d" values="{d1};{d2};{d1}" dur="{dur}s" repeatCount="indefinite"/></path>')
    msg = "thanks for stopping by  ·  go build something cool"
    body = f"""
  <defs><linearGradient id="fm" x1="0" x2="1"><stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset=".2" stop-color="#fff"/><stop offset=".8" stop-color="#fff"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>
  <mask id="fmask"><rect width="{W}" height="{H}" fill="url(#fm)"/></mask></defs>
  <g mask="url(#fmask)">{''.join(waves)}</g>
  {mono(W / 2 - len(msg) * 12 * CW / 2, 108, msg, 12, FAINT)}"""
    return svg(W, H, body, "", "thanks for stopping by")


# --------------------------------------------------------------------------- main

def write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists() or path.read_text() != content:
        path.write_text(content)
        print("wrote", path)


def main():
    demo = "--demo" in sys.argv
    live_dir = Path(sys.argv[sys.argv.index("--out") + 1]) if "--out" in sys.argv else ROOT / "dist"
    token = os.environ.get("GITHUB_TOKEN")
    if demo:
        data, waka = DEMO, None
    elif not token:
        sys.exit("GITHUB_TOKEN is not set (use --demo for sample data)")
    else:
        data = fetch_github(token)
        waka = None
        if os.environ.get("WAKATIME_API_KEY"):
            try:
                waka = fetch_wakatime(os.environ["WAKATIME_API_KEY"])
            except Exception as e:  # WakaTime is optional garnish
                print("wakatime skipped:", e)

    # Static art lives in assets/ on main; live cards are published to the `output` branch.
    write(OUT / "header.svg", header())
    write(OUT / "terminal.svg", terminal())
    write(OUT / "footer.svg", footer())

    ist = dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30)))
    synced = ist.strftime("%d %b · %H:%M ist").lower()
    write(live_dir / "stats.svg", stats(data, waka, synced))
    for i, p in enumerate(PROJECTS):
        write(live_dir / f"{p['repo']}.svg", project(p, data["repo_stars"].get(p["repo"], 0), hero=i == 0))


if __name__ == "__main__":
    main()
