import copy
import importlib.util
from pathlib import Path
import unittest
from urllib.parse import parse_qs, urlsplit
import xml.etree.ElementTree as ET

MODULE = Path(__file__).resolve().parents[1] / 'scripts' / 'update_profile.py'
SPEC = importlib.util.spec_from_file_location('profile_updater', MODULE)
profile = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profile)


def pr(number, state='open', merged=False, repo='upstream/tool', author='nigel', visibility='public'):
    return {
        'user': {'login': author}, 'base': {'repo': {'full_name': repo,
        'private': visibility == 'private', 'visibility': visibility}},
        'number': number, 'state': state, 'title': 'Keep <values> | stable [now]',
        'merged': merged, 'merged_at': '2026-10-17T10:00:00Z' if merged else None,
        'html_url': f'https://github.com/{repo}/pull/{number}',
        'updated_at': '2026-10-18T05:00:00Z',
    }


def record(item):
    return {'repository': item['base']['repo']['full_name'], 'number': item['number'],
            'title': item['title'], 'status': 'Merged' if item['merged'] else 'Open' if item['state'] == 'open' else 'Closed',
            'url': item['html_url'], 'updated_at': item['updated_at']}


def snapshot(items):
    return {'username': 'nigel', 'checked_on': '2026-10-18', 'pull_requests': [record(p) for p in items]}


def event(number, repo='upstream/tool'):
    return {'id': str(number), 'created_at': '2026-10-18T05:00:00Z', 'public': True,
            'actor': {'login': 'nigel'}, 'repo': {'name': repo}, 'type': 'PullRequestEvent',
            'payload': {'number': number}}


def api(events, items):
    by_path = {f'/repos/{p["base"]["repo"]["full_name"]}/pulls/{p["number"]}': p for p in items}

    def get_json(path):
        if path.startswith('/users/'):
            page = int(parse_qs(urlsplit(path).query)['page'][0])
            return events[(page - 1) * 100:page * 100]
        return by_path[path]
    return get_json


CONFIG = {'username': 'nigel', 'groups': [{'title': 'Tools', 'repositories': {'upstream/tool': 'Tool'}}]}


class ProfileUpdaterTests(unittest.TestCase):
    def test_open_closed_and_merged_are_distinct(self):
        items = [pr(1), pr(2, 'closed'), pr(3, 'closed', merged=True)]
        records, _ = profile.collect_pull_requests('nigel', snapshot(items), api([], items))
        self.assertEqual([p['status'] for p in records], ['Open', 'Closed', 'Merged'])
        self.assertEqual(profile.summary(records), {
            'total': 3, 'open': 1, 'closed': 1, 'merged': 1, 'repositories': 1})

    def test_old_history_refreshes_even_with_no_public_events(self):
        old = pr(1)
        merged = pr(1, 'closed', merged=True)
        records, _ = profile.collect_pull_requests('nigel', snapshot([old]), api([], [merged]))
        self.assertEqual(records[0]['status'], 'Merged')
        self.assertEqual(len(records), 1)

    def test_new_repository_is_discovered_and_directly_verified(self):
        items = [pr(1), pr(2, repo='new/repository')]
        records, checkpoint = profile.collect_pull_requests('nigel', snapshot(items[:1]), api([event(2, 'new/repository')], items))
        self.assertEqual({p['repository'] for p in records}, {'upstream/tool', 'new/repository'})
        self.assertEqual(checkpoint['id'], '2')

    def test_own_private_and_internal_repositories_are_excluded(self):
        items = [pr(1), pr(2, repo='nigel/personal'), pr(3, visibility='private'), pr(4, visibility='internal')]
        events = [event(p['number'], p['base']['repo']['full_name']) for p in items]
        records, _ = profile.collect_pull_requests('nigel', snapshot(items[:1]), api(events, items))
        self.assertEqual([p['number'] for p in records], [1])

    def test_event_pages_and_comment_discovery_are_supported(self):
        events = [dict(event(i), type='PushEvent', payload={}) for i in range(100)]
        comment = dict(event(101), type='IssueCommentEvent', payload={'issue': {'number': 101, 'pull_request': {'url': 'public'}}})
        events.append(comment)
        records, _ = profile.collect_pull_requests('nigel', snapshot([]), api(events, [pr(101)]))
        self.assertEqual([p['number'] for p in records], [101])

    def test_event_history_gap_is_rejected(self):
        previous = snapshot([pr(1)])
        previous['event_checkpoint'] = {'id': 'old', 'created_at': '2026-10-17T00:00:00Z'}
        events = [dict(event(i), type='PushEvent', payload={}) for i in range(300)]
        with self.assertRaisesRegex(ValueError, 'gap'):
            profile.collect_pull_requests('nigel', previous, api(events, [pr(1)]))

    def test_nonpublic_or_foreign_events_do_not_discover_prs(self):
        hidden, foreign = event(2), event(3)
        hidden['public'] = False
        foreign['actor']['login'] = 'someone-else'
        records, _ = profile.collect_pull_requests('nigel', snapshot([pr(1)]), api([hidden, foreign], [pr(1)]))
        self.assertEqual(len(records), 1)

    def test_saved_author_mismatch_and_empty_refresh_fail(self):
        with self.assertRaisesRegex(ValueError, 'author'):
            profile.collect_pull_requests('nigel', snapshot([pr(1)]), api([], [pr(1, author='someone-else')]))
        with self.assertRaisesRegex(ValueError, 'keeping'):
            profile.collect_pull_requests('nigel', snapshot([pr(1)]), api([], [pr(1, visibility='private')]))

    def test_direct_api_failure_is_not_hidden(self):
        def fail(path):
            if path.startswith('/users/'):
                return []
            raise TimeoutError('GitHub unavailable')
        with self.assertRaises(TimeoutError):
            profile.collect_pull_requests('nigel', snapshot([pr(1)]), fail)

    def test_inconsistent_merge_information_is_rejected(self):
        item = pr(1, 'closed', merged=True)
        item['merged_at'] = None
        with self.assertRaisesRegex(ValueError, 'merge information'):
            profile.collect_pull_requests('nigel', snapshot([pr(1)]), api([], [item]))

    def test_duplicate_snapshot_cannot_inflate_totals(self):
        item = record(pr(1))
        data = snapshot([])
        data['pull_requests'] = [item, copy.deepcopy(item)]
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            profile.validate_snapshot(data, 'nigel')

    def test_invalid_snapshot_state_repository_and_link_are_rejected(self):
        item = record(pr(1))
        for key, value in [('status', 'Accepted'), ('url', 'https://example.com'), ('repository', 'nigel/personal'), ('repository', '../outside')]:
            data = snapshot([])
            data['pull_requests'] = [dict(item, **{key: value})]
            with self.subTest(key=key), self.assertRaises(ValueError):
                profile.validate_snapshot(data, 'nigel')

    def test_titles_are_escaped_and_new_targets_remain_visible(self):
        rendered = profile.render_contributions(snapshot([pr(1), pr(2, repo='new/repository')]), CONFIG)
        self.assertIn('More repositories', rendered)
        self.assertIn('new/repository', rendered)
        self.assertIn('&lt;values&gt; &#124; stable &#91;now&#93;', rendered)
        self.assertNotIn('<values>', rendered)
        self.assertIn('Explore all 2 pull requests', rendered)

    def test_refresh_preserves_curated_text_and_is_idempotent(self):
        readme = 'My projects\n' + profile.START + '\nold\n' + profile.END + '\nMy contact links'
        once = profile.replace_section(readme, 'New contribution data')
        self.assertEqual(profile.replace_section(once, 'New contribution data'), once)
        self.assertTrue(once.startswith('My projects\n'))
        self.assertTrue(once.endswith('\nMy contact links'))

    def test_missing_duplicate_or_reversed_markers_fail(self):
        for text in ['no markers', profile.START * 2 + profile.END, profile.END + profile.START]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                profile.replace_section(text, 'new')

    def test_both_summary_images_are_valid_and_accessible(self):
        totals = {'total': 41, 'merged': 6, 'open': 35, 'repositories': 19}
        for compact in [False, True]:
            root = ET.fromstring(profile.stats_svg(totals, compact))
            self.assertEqual(root.attrib['role'], 'img')
            numbers = {node.text for node in root.iter() if node.tag.endswith('text')}
            self.assertTrue({'41', '6', '35', '19'}.issubset(numbers))


if __name__ == '__main__':
    unittest.main()
