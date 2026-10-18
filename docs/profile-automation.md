# Profile updates

The contribution totals, repository groups, PR list and two summary SVGs refresh
about every six hours with GitHub Actions. The introduction, projects, skills and
community links are curated in `README.md`.

## What is counted

- Public pull requests authored by `jinchuntan` against repositories owned by others.
- Open, merged and closed without merging are distinct states. A closed PR only
  counts as merged when GitHub supplies a merge date.
- Repository totals count distinct target repositories, including targets with
  closed PRs. Personal-repository PRs, private contributions, commits, issues,
  reviews, fork counts and stars are not included in the PR totals.
- GitHub search indexing may lag behind recent activity.

The checked-in `data/contributions.json` records the public source data. Edit
`profile-config.json` to curate category labels. New targets still appear under
“More repositories” and in the complete expandable PR list.

## Run or maintain

Use **Actions → Refresh profile → Run workflow** to refresh manually, or run:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/update_profile.py
```

The updater uses only Python's standard library. Actions supplies its temporary,
repository-scoped `GITHUB_TOKEN`; no personal access token or external stats
service is required. It commits only its four generated files, and only when
their content changes. The displayed refresh date is recorded in UTC.

For an offline preview, use:

```sh
python3 scripts/update_profile.py --from-snapshot data/contributions.json
```

API failures, incomplete search results, duplicates and missing README markers
fail the workflow before publishing. Search is paginated and explicitly stops
at GitHub's 1,000-result search limit rather than silently undercounting.
Scheduled jobs may be delayed by GitHub, and public-repository schedules can be
disabled after 60 days of inactivity. The last successful snapshot remains
visible if a run fails; the Actions page shows its status.

Design references: [Simon Willison](https://github.com/simonw/simonw) for generated
README sections; [lowlighter](https://github.com/lowlighter/lowlighter) for compact
visual summaries; [Anthony Fu](https://github.com/antfu/antfu) for concise links.
All profile illustrations are original SVGs in this repository.
