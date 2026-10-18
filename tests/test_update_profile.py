import copy
import importlib.util
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

MODULE = Path(__file__).resolve().parents[1] / 'scripts' / 'update_profile.py'
SPEC = importlib.util.spec_from_file_location('profile_updater', MODULE)
profile = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profile)


def issue(number, state='OPEN', repository='upstream/tool', visibility='PUBLIC'):
    return {
        'author': {'login': 'nigel'},
        'repository': {'nameWithOwner': repository, 'visibility': visibility,
                       'owner': {'login': repository.split('/')[0]}},
        'number': number, 'state': state, 'title': 'Keep <values> | stable [now]',
        'mergedAt': '2026-10-17T10:00:00Z' if state == 'MERGED' else None,
        'url': f'https://github.com/{repository}/pull/{number}',
        'updatedAt': '2026-10-18T05:00:00Z',
    }


def response(items, total=None, more=False, cursor=None):
    return {'data': {'node': {'login': 'nigel', 'pullRequests': {
        'totalCount': len(items) if total is None else total, 'nodes': items,
        'pageInfo': {'hasNextPage': more, 'endCursor': cursor},
    }}}}


def snapshot(records):
    return {'username': 'nigel', 'checked_on': '2026-10-18', 'pull_requests': records}


CONFIG = {'username': 'nigel', 'groups': [{'title': 'Tools', 'repositories': {'upstream/tool': 'Tool'}}]}


class ProfileUpdaterTests(unittest.TestCase):
    def test_open_closed_and_merged_are_distinct(self):
        records = profile.collect_pull_requests('nigel', lambda _: response([
            issue(1), issue(2, 'CLOSED'), issue(3, 'MERGED')]))
        self.assertEqual([p['status'] for p in records], ['Open', 'Closed', 'Merged'])
        self.assertEqual(profile.summary(records), {
            'total': 3, 'open': 1, 'closed': 1, 'merged': 1, 'repositories': 1})

    def test_inconsistent_merge_information_is_rejected(self):
        for state, merged_at in [('MERGED', None), ('CLOSED', '2026-10-17T10:00:00Z')]:
            item = issue(1, state)
            item['mergedAt'] = merged_at
            with self.subTest(state=state), self.assertRaisesRegex(ValueError, 'merge information'):
                profile.collect_pull_requests('nigel', lambda _: response([item]))

    def test_own_private_and_internal_repositories_are_excluded(self):
        items = [issue(1), issue(2, repository='Nigel/personal'),
                 issue(3, visibility='PRIVATE'), issue(4, visibility='INTERNAL')]
        records = profile.collect_pull_requests('nigel', lambda _: response(items))
        self.assertEqual([p['number'] for p in records], [1])

    def test_every_connection_page_is_collected(self):
        calls = []

        def graphql(variables):
            calls.append(variables['after'])
            if variables['after'] is None:
                return response([issue(i) for i in range(1, 101)], total=101, more=True, cursor='next')
            return response([issue(101)], total=101)

        self.assertEqual(len(profile.collect_pull_requests('nigel', graphql)), 101)
        self.assertEqual(calls, [None, 'next'])

    def test_errors_null_nodes_and_incomplete_connections_are_rejected(self):
        payloads = [{'errors': [{'message': 'Unavailable'}]}, {'data': {'node': None}},
                    response([None]), response([issue(1)], total=2)]
        for payload in payloads:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                profile.collect_pull_requests('nigel', lambda _: payload)

    def test_changed_totals_and_invalid_cursors_are_rejected(self):
        pages = iter([response([issue(1)], total=2, more=True, cursor='next'),
                      response([issue(2)], total=3)])
        with self.assertRaisesRegex(ValueError, 'changed'):
            profile.collect_pull_requests('nigel', lambda _: next(pages))
        for cursor in [None, 'repeated']:
            payload = response([issue(1)], total=3, more=True, cursor=cursor)
            with self.subTest(cursor=cursor), self.assertRaisesRegex(ValueError, 'cursor'):
                profile.collect_pull_requests('nigel', lambda _: payload)

    def test_foreign_author_and_profile_owner_are_rejected(self):
        item = issue(1)
        item['author']['login'] = 'someone-else'
        with self.assertRaisesRegex(ValueError, 'author'):
            profile.collect_pull_requests('nigel', lambda _: response([item]))
        payload = response([issue(1)])
        payload['data']['node']['login'] = 'someone-else'
        with self.assertRaisesRegex(ValueError, 'profile owner'):
            profile.collect_pull_requests('nigel', lambda _: payload)

    def test_duplicate_snapshot_cannot_inflate_totals(self):
        record = profile.collect_pull_requests('nigel', lambda _: response([issue(1)]))[0]
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            profile.validate_snapshot(snapshot([record, copy.deepcopy(record)]), 'nigel')

    def test_invalid_snapshot_state_repository_and_link_are_rejected(self):
        record = profile.collect_pull_requests('nigel', lambda _: response([issue(1)]))[0]
        for key, value in [('status', 'Accepted'), ('url', 'https://example.com'),
                           ('repository', 'nigel/personal'), ('repository', '../outside')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                profile.validate_snapshot(snapshot([dict(record, **{key: value})]), 'nigel')

    def test_titles_are_escaped_and_new_targets_remain_visible(self):
        records = profile.collect_pull_requests('nigel', lambda _: response([
            issue(1), issue(2, repository='new/repository')]))
        rendered = profile.render_contributions(snapshot(records), CONFIG)
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
