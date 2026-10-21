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
- Discovery reads public PR activity, including new target repositories. Every
  saved or newly discovered PR is checked through its direct REST endpoint.
  It does not depend on author search or the profile's activity graph.
- Repository transfers and case variants use GitHub's current repository name
  so each PR is counted once.

The checked-in `data/contributions.json` records the public source data. Edit
`profile-config.json` to curate category labels. New targets still appear under
“More repositories” and in the complete expandable PR list.

## Run or maintain

Use **Actions → Refresh profile → Run workflow** to refresh manually, or run:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/update_profile.py --check
python3 scripts/update_profile.py
```

The updater uses only Python's standard library. Actions supplies its temporary,
repository-scoped `GITHUB_TOKEN`; no personal access token or external stats
service is required. It commits only its four generated files, and only when
their content changes. The displayed refresh date is recorded in UTC.
Summary-image filenames include a content hash so GitHub cannot keep displaying
an older set of totals. Only the current pair is retained; other artwork is preserved.

For an offline preview, use:

```sh
python3 scripts/update_profile.py --from-snapshot data/contributions.json
```

The **Profile checks** workflow runs on every pull request and push to `main`.
It tests the updater and checks that the saved JSON, README contribution section
and both summary images agree. These checks use no network access and cannot
publish changes. Run `python3 scripts/update_profile.py --check` locally for the
same validation without changing any files. After editing category labels or
generated data, use the offline preview command above to regenerate the files.

The refresh workflow runs these checks before fetching live data and validates
the generated files again before committing. File-level regression tests cover
failed API calls, duplicate or empty results and broken README markers. They
also verify that curated text and project artwork survive a refresh.
The checks report failures on pull requests. Making them required before a merge
is a separate branch-protection setting.

The initial 41-PR history was verified against GitHub's connected search and
seeded in the snapshot. The updater retains that history, checks every saved
PR, and discovers new PRs from up to 300 public events on every refresh. GitHub
can delay event delivery, so a new PR may appear after a later refresh.

API failures, duplicate saved PRs, missing README markers and an empty refresh
after a nonempty snapshot fail before publishing. An event checkpoint detects when
the 300-event window may have skipped activity; such a gap also fails rather
than publishing incomplete totals. If that happens, reconcile the snapshot
with GitHub's authored-PR list and reset the checkpoint. Saved PRs remain in
the history even after their opening event expires.
Scheduled jobs may be delayed by GitHub, and public-repository schedules can be
disabled after 60 days of inactivity. The last successful snapshot remains
visible if a run fails; the Actions page shows its status.

Design references: [Simon Willison](https://github.com/simonw/simonw) for generated
README sections; [lowlighter](https://github.com/lowlighter/lowlighter) for compact
visual summaries; [Anthony Fu](https://github.com/antfu/antfu) for concise links.
All profile illustrations are original SVGs in this repository.
