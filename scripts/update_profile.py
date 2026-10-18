#!/usr/bin/env python3
"""Refresh public upstream PRs in the profile. Python standard library only."""

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from html import escape
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


QUERY = """
query ProfilePullRequests($userId: ID!, $after: String) {
  node(id: $userId) {
    ... on User {
      login
      pullRequests(first: 100, after: $after, orderBy: {field: CREATED_AT, direction: ASC}) {
        totalCount
        pageInfo { hasNextPage endCursor }
        nodes {
          number title url state mergedAt updatedAt
          author { login }
          repository { nameWithOwner visibility owner { login } }
        }
      }
    }
  }
}
"""


def github_graphql(variables):
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("GITHUB_TOKEN is needed for a live refresh; use --from-snapshot for an offline preview")
    request = Request(
        "https://api.github.com/graphql",
        data=json.dumps({"query": QUERY, "variables": variables}).encode("utf-8"),
        headers={"Accept": "application/vnd.github+json", "Content-Type": "application/json",
                 "User-Agent": "jinchuntan-profile", "Authorization": f"Bearer {token}"},
    )
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def collect_pull_requests(username, get_graphql=github_graphql, user_node_id="MDQ6VXNlcjc5MTQwNjIw"):
    """Read the user's PR connection directly, without relying on author search."""
    records = []
    cursor = None
    cursors = set()
    total = None
    fetched = 0
    while True:
        result = get_graphql({"userId": user_node_id, "after": cursor})
        if result.get("errors"):
            raise ValueError("GitHub GraphQL returned errors; keeping the previous snapshot")
        user = result.get("data", {}).get("node")
        if not user or user.get("login", "").casefold() != username.casefold():
            raise ValueError("GitHub did not return the expected profile owner")
        connection = user["pullRequests"]
        if total is not None and connection["totalCount"] != total:
            raise ValueError("PR count changed during pagination; try again")
        total = connection["totalCount"]
        for pr in connection["nodes"]:
            if not pr:
                raise ValueError("GitHub returned a partial PR connection")
            fetched += 1
            repo = pr["repository"]
            if (repo["visibility"] != "PUBLIC"
                    or repo["owner"]["login"].casefold() == username.casefold()):
                continue
            if not pr["author"] or pr["author"]["login"].casefold() != username.casefold():
                raise ValueError("Unexpected PR author")
            if pr["state"] not in {"OPEN", "CLOSED", "MERGED"}:
                raise ValueError("Unknown PR state")
            if (pr["state"] == "MERGED") != bool(pr["mergedAt"]):
                raise ValueError("Inconsistent PR merge information")
            records.append({
                "repository": repo["nameWithOwner"], "number": pr["number"], "title": pr["title"],
                "status": {"OPEN": "Open", "MERGED": "Merged", "CLOSED": "Closed"}[pr["state"]],
                "url": pr["url"], "updated_at": pr["updatedAt"],
            })
        page = connection["pageInfo"]
        if not page["hasNextPage"]:
            break
        cursor = page["endCursor"]
        if not cursor or cursor in cursors or not connection["nodes"]:
            raise ValueError("Invalid pagination cursor")
        cursors.add(cursor)
    if fetched != total:
        raise ValueError("PR connection count does not match its pages")
    return records


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


def render_contributions(snapshot, config):
    records, username = snapshot["pull_requests"], config["username"]
    totals = summary(records)
    alt = (f'{totals["total"]} public upstream PRs across {totals["repositories"]} repositories: '
           f'{totals["merged"]} merged, {totals["open"]} open, {totals["closed"]} closed without merging.')
    by_repo = defaultdict(list)
    for pr in records:
        by_repo[pr["repository"]].append(pr)

    def repo_line(repo, label):
        counts = Counter(pr["status"] for pr in by_repo[repo])
        state = " · ".join(f'{counts[s]} {s.lower()}' for s in ("Merged", "Open", "Closed") if counts[s])
        link = escape(search_url(username, repo), quote=True)
        return f'<a href="{link}">{escape(label)}</a><br><sub>{state}</sub>'

    lines = [
        '<table>', '  <tr>', '    <td colspan="3">',
        '<picture>',
        '  <source media="(max-width: 600px)" srcset="./assets/contributions-compact.svg">',
        f'  <img src="./assets/contributions.svg" width="100%" alt="{escape(alt, quote=True)}">',
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
        lines.append("      " + "<br><br>\n      ".join(entries))
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
    if args.from_snapshot:
        snapshot = json.loads(args.from_snapshot.read_text(encoding="utf-8"))
    else:
        snapshot = {"username": username, "checked_on": datetime.now(timezone.utc).date().isoformat(),
                    "pull_requests": collect_pull_requests(username, user_node_id=config["user_node_id"])}
    validate_snapshot(snapshot, username)
    snapshot["pull_requests"].sort(key=lambda pr: (pr["repository"].casefold(), pr["number"]))
    readme = replace_section((ROOT / "README.md").read_text(encoding="utf-8"), render_contributions(snapshot, config))
    totals = summary(snapshot["pull_requests"])
    # Build and validate every output before writing. Failed runs never reach the commit step.
    outputs = {
        "README.md": readme,
        "assets/contributions.svg": stats_svg(totals),
        "assets/contributions-compact.svg": stats_svg(totals, compact=True),
        "data/contributions.json": json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
    }
    for path, content in outputs.items():
        (ROOT / path).write_text(content, encoding="utf-8")
    print(json.dumps(totals, sort_keys=True))


if __name__ == "__main__":
    main()
