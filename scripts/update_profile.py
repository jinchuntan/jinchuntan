#!/usr/bin/env python3
"""Refresh public upstream PRs in the profile. Python standard library only."""

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from html import escape
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
START = "<!-- CONTRIBUTIONS:START -->"
END = "<!-- CONTRIBUTIONS:END -->"
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
STATUSES = {"Merged", "Open", "Closed"}


def github_json(path):
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "jinchuntan-profile"}
    if token := os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {token}"
    request = Request("https://api.github.com" + path, headers=headers)
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def collect_pull_requests(username, previous, get_json=github_json):
    """Discover public PR events and recheck every saved PR directly."""
    known = {(pr["repository"], pr["number"]) for pr in previous["pull_requests"]}
    candidates = set(known)
    events = []
    for page in range(1, 4):
        batch = get_json(f"/users/{username}/events/public?per_page=100&page={page}")
        if not isinstance(batch, list):
            raise ValueError("GitHub did not return a public event list")
        events.extend(batch)
        if len(batch) < 100:
            break
    checkpoint = previous.get("event_checkpoint")
    # GitHub retains at most 300 public events. Never silently skip an unseen gap.
    if (len(events) == 300 and checkpoint
            and events[-1]["created_at"] > checkpoint["created_at"]):
        raise ValueError("Public event history has a gap; keeping the verified snapshot")
    for event in events:
        if not event.get("public") or event["actor"]["login"].casefold() != username.casefold():
            continue
        payload = event["payload"]
        number = None
        if event["type"] == "PullRequestEvent":
            number = payload.get("number") or payload.get("pull_request", {}).get("number")
        elif event["type"] == "IssueCommentEvent" and payload.get("issue", {}).get("pull_request"):
            number = payload["issue"]["number"]
        if number is None:
            continue
        repo = event["repo"]["name"]
        if not REPO.fullmatch(repo) or type(number) is not int or number < 1:
            raise ValueError("Invalid public PR event")
        if repo.split("/")[0].casefold() != username.casefold():
            candidates.add((repo, number))

    def fetch_pr(identity):
        repo, number = identity
        pr = get_json(f"/repos/{repo}/pulls/{number}")
        target = pr["base"]["repo"]
        if target.get("private") is not False or target.get("visibility", "public") != "public":
            return None
        if pr["user"]["login"].casefold() != username.casefold():
            if identity in known:
                raise ValueError("A saved PR no longer has the expected author")
            return None
        repository = target["full_name"]
        if repository.split("/")[0].casefold() == username.casefold():
            return None
        if pr["state"] not in {"open", "closed"}:
            raise ValueError("Unknown PR state")
        merged = bool(pr["merged_at"])
        if merged != pr["merged"] or (merged and pr["state"] != "closed"):
            raise ValueError("Inconsistent PR merge information")
        return {
            "repository": repository, "number": pr["number"], "title": pr["title"],
            "status": "Merged" if merged else "Open" if pr["state"] == "open" else "Closed",
            "url": pr["html_url"], "updated_at": pr["updated_at"],
        }

    print(f"Read {len(events)} public events; checking {len(candidates)} PRs directly.", flush=True)
    executor = ThreadPoolExecutor(max_workers=4)
    try:
        records = [pr for pr in executor.map(fetch_pr, sorted(candidates)) if pr is not None]
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
    if previous["pull_requests"] and not records:
        raise ValueError("Refresh returned no PRs; keeping the verified snapshot")
    if events:
        checkpoint = {"id": events[0]["id"], "created_at": events[0]["created_at"]}
    return records, checkpoint


def validate_snapshot(snapshot, username):
    if snapshot["username"].casefold() != username.casefold():
        raise ValueError("Snapshot belongs to another user")
    date.fromisoformat(snapshot["checked_on"])
    seen = set()
    for pr in snapshot["pull_requests"]:
        repo, number = pr["repository"], pr["number"]
        if not REPO.fullmatch(repo) or repo.split("/")[0].casefold() == username.casefold():
            raise ValueError("Only external repositories belong in this showcase")
        if type(number) is not int or number < 1 or pr["status"] not in STATUSES:
            raise ValueError("Invalid PR number or state")
        if pr["url"] != f"https://github.com/{repo}/pull/{number}":
            raise ValueError("Unexpected PR link")
        if not isinstance(pr["title"], str) or not pr["title"].strip():
            raise ValueError("PR title is missing")
        identity = (repo.casefold(), number)
        if identity in seen:
            raise ValueError("Duplicate pull request")
        seen.add(identity)


def search_url(username, repository=None):
    query = f"is:pr author:{username} " + (f"repo:{repository}" if repository else f"-user:{username}")
    return "https://github.com/search?" + urlencode({"q": query, "type": "pullrequests"})


def summary(records):
    states = Counter(pr["status"] for pr in records)
    return {"total": len(records), "merged": states["Merged"], "open": states["Open"],
            "closed": states["Closed"], "repositories": len({pr["repository"] for pr in records})}


def stats_svg(totals, compact=False):
    width, height, columns = (600, 300, 2) if compact else (1200, 150, 4)
    stats = [("total", "Upstream PRs", "#19352f"), ("merged", "Merged", "#8050a4"),
             ("open", "Open", "#28724e"), ("repositories", "Repositories", "#c75132")]
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title">',
             '<title id="title">Public upstream pull request summary</title>']
    for index, (key, label, color) in enumerate(stats):
        x, y = (index % columns) * 300, (index // columns) * 150
        parts.extend([
            f'<rect x="{x + 4}" y="{y + 4}" width="288" height="138" rx="12" fill="#f6f3ea" stroke="#e3e3d9"/>',
            f'<rect x="{x + 24}" y="{y + 25}" width="24" height="4" rx="2" fill="{color}"/>',
            f'<text x="{x + 24}" y="{y + 88}" font-family="Arial,Helvetica,sans-serif" font-size="54" font-weight="700" fill="{color}">{totals[key]}</text>',
            f'<text x="{x + 24}" y="{y + 119}" font-family="Arial,Helvetica,sans-serif" font-size="21" fill="#496257">{label}</text>',
        ])
    return "\n".join(parts + ["</svg>\n"])


def stat_image_paths(totals):
    version = sha256(stats_svg(totals).encode("utf-8")).hexdigest()[:12]
    return f"assets/contributions-{version}.svg", f"assets/contributions-compact-{version}.svg"


def render_contributions(snapshot, config):
    records, username = snapshot["pull_requests"], config["username"]
    totals = summary(records)
    wide_image, compact_image = stat_image_paths(totals)
    alt = (f'{totals["total"]} public upstream PRs across {totals["repositories"]} repositories: '
           f'{totals["merged"]} merged, {totals["open"]} open, {totals["closed"]} closed without merging.')
    by_repo = defaultdict(list)
    for pr in records:
        by_repo[pr["repository"]].append(pr)

    def repo_line(repo, label):
        counts = Counter(pr["status"] for pr in by_repo[repo])
        state = " · ".join(f'{counts[s]} {s.lower()}' for s in ("Merged", "Open", "Closed") if counts[s])
        link = escape(search_url(username, repo), quote=True)
        return f'<a href="{link}">{escape(label)}</a> <sub>{state}</sub>'

    lines = [
        '<table>', '  <tr>', '    <td colspan="3">',
        '<picture>',
        f'  <source media="(max-width: 600px)" srcset="./{compact_image}">',
        f'  <img src="./{wide_image}" width="100%" alt="{escape(alt, quote=True)}">',
        '</picture>', '    </td>', '  </tr>', '  <tr>',
    ]
    known = set()
    for group in config["groups"]:
        lines.append('    <td width="33%" valign="top">')
        lines.append(f'      <h4>{escape(group["title"])}</h4>')
        entries = []
        for repo, label in group["repositories"].items():
            known.add(repo)
            if repo in by_repo:
                entries.append(repo_line(repo, label))
        lines.append("      " + "<br>\n      ".join(entries))
        lines.append("    </td>")
    lines.extend(['  </tr>', '</table>', ''])
    unknown = sorted(set(by_repo) - known, key=str.casefold)
    if unknown:
        lines.extend(['<p><strong>More repositories</strong><br>',
                      '<br><br>'.join(repo_line(r, r) for r in unknown), '</p>', ''])

    lines.extend([
        '<details>', f'<summary><strong>Explore all {totals["total"]} pull requests</strong></summary>', '',
        '| Repository | Pull request | Status |', '| :--- | :--- | :--- |',
    ])
    for pr in sorted(records, key=lambda p: (p["repository"].casefold(), -p["number"])):
        title = escape(" ".join(pr["title"].split()), quote=True)
        title = title.replace("|", "&#124;").replace("[", "&#91;").replace("]", "&#93;")
        lines.append(f'| {escape(pr["repository"])} | <a href="{pr["url"]}">#{pr["number"]} · {title}</a> | {pr["status"]} |')
    lines.extend([
        '', '</details>', '',
        f'<p><sub>Public upstream PRs · Updated {snapshot["checked_on"]} · '
        '<a href="https://github.com/jinchuntan/jinchuntan/actions/workflows/update-profile.yml">Auto-refresh ~6h</a>'
        '</sub></p>',
    ])
    return "\n".join(lines)


def replace_section(readme, content):
    if readme.count(START) != 1 or readme.count(END) != 1:
        raise ValueError("README needs exactly one pair of contribution markers")
    before, rest = readme.split(START)
    if END not in rest:
        raise ValueError("Contribution markers are reversed")
    _, after = rest.split(END)
    return before + START + "\n\n" + content + "\n\n" + END + after


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-snapshot", type=Path, help="Render a saved snapshot without network access")
    args = parser.parse_args()
    config = json.loads((ROOT / "profile-config.json").read_text(encoding="utf-8"))
    username = config["username"]
    if not re.fullmatch(r"[A-Za-z0-9-]+", username):
        raise ValueError("Invalid GitHub username")
    previous = json.loads((ROOT / "data/contributions.json").read_text(encoding="utf-8"))
    validate_snapshot(previous, username)
    if args.from_snapshot:
        snapshot = json.loads(args.from_snapshot.read_text(encoding="utf-8"))
    else:
        records, checkpoint = collect_pull_requests(username, previous)
        snapshot = {"username": username, "checked_on": datetime.now(timezone.utc).date().isoformat(),
                    "pull_requests": records, "event_checkpoint": checkpoint}
    if previous["pull_requests"] and not snapshot["pull_requests"]:
        raise ValueError("Refresh returned no PRs; keeping the verified snapshot")
    validate_snapshot(snapshot, username)
    snapshot["pull_requests"].sort(key=lambda pr: (pr["repository"].casefold(), pr["number"]))
    readme = replace_section((ROOT / "README.md").read_text(encoding="utf-8"), render_contributions(snapshot, config))
    totals = summary(snapshot["pull_requests"])
    wide_image, compact_image = stat_image_paths(totals)
    # Build and validate every output before writing. Failed runs never reach the commit step.
    outputs = {
        "README.md": readme,
        wide_image: stats_svg(totals),
        compact_image: stats_svg(totals, compact=True),
        "data/contributions.json": json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
    }
    for path, content in outputs.items():
        (ROOT / path).write_text(content, encoding="utf-8")
    for old in (ROOT / "assets").glob("contributions*.svg"):
        owned = (old.name in {"contributions.svg", "contributions-compact.svg"}
                 or re.fullmatch(r"contributions-(?:compact-)?[0-9a-f]{12}\.svg", old.name))
        if owned and old.relative_to(ROOT).as_posix() not in outputs:
            old.unlink()
    print(json.dumps(totals, sort_keys=True))


if __name__ == "__main__":
    main()
