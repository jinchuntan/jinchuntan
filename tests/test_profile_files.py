import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from test_update_profile import CONFIG, pr, profile, record, snapshot


class ProfileFileTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / 'assets').mkdir()
        (self.root / 'data').mkdir()
        (self.root / 'profile-config.json').write_text(json.dumps(CONFIG), encoding='utf-8')
        (self.root / 'data/contributions.json').write_text(json.dumps(snapshot([pr(1)])), encoding='utf-8')
        (self.root / 'README.md').write_text(
            'My projects\n' + profile.START + '\nold\n' + profile.END + '\nMy contact links', encoding='utf-8')
        (self.root / 'assets/project.svg').write_text('<svg/>', encoding='utf-8')
        root_patch = patch.object(profile, 'ROOT', self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)

    def run_updater(self, *args):
        with patch('sys.argv', ['update_profile.py', *map(str, args)]):
            profile.main()

    def render_saved(self):
        self.run_updater('--from-snapshot', self.root / 'data/contributions.json')

    def files(self):
        return {path.relative_to(self.root).as_posix(): path.read_bytes()
                for path in self.root.rglob('*') if path.is_file()}

    def test_check_uses_no_network_and_changes_no_files(self):
        self.render_saved()
        before = self.files()
        with patch.object(profile, 'collect_pull_requests', side_effect=AssertionError('Unexpected API call')):
            self.run_updater('--check')
        self.assertEqual(self.files(), before)

    def test_check_rejects_each_stale_or_missing_generated_file_without_repairing_it(self):
        self.render_saved()
        totals = profile.summary(snapshot([pr(1)])['pull_requests'])
        generated = ['README.md', 'data/contributions.json', *profile.stat_image_paths(totals)]
        for name in generated:
            for missing in [False, True]:
                with self.subTest(path=name, missing=missing):
                    path = self.root / name
                    original = path.read_bytes()
                    if missing:
                        path.unlink()
                    elif name == 'data/contributions.json':
                        path.write_bytes(original + b' ')
                    else:
                        path.write_bytes(original.replace(b'1', b'99') if name.endswith('.svg')
                                         else original.replace(b'Explore all 1', b'Explore all 99'))
                    before = self.files()
                    with self.assertRaises((ValueError, FileNotFoundError)):
                        self.run_updater('--check')
                    self.assertEqual(self.files(), before)
                    path.write_bytes(original)

    def test_duplicate_saved_prs_are_rejected_without_writes(self):
        self.render_saved()
        path = self.root / 'data/contributions.json'
        path.write_text(json.dumps(snapshot([pr(1), pr(1)])), encoding='utf-8')
        before = self.files()
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.run_updater('--check')
        self.assertEqual(self.files(), before)

    def test_offline_render_preserves_curated_content_and_artwork(self):
        old_image = self.root / 'assets/contributions-000000000000.svg'
        old_image.write_text('<svg/>', encoding='utf-8')
        self.render_saved()
        readme = (self.root / 'README.md').read_text(encoding='utf-8')
        self.assertTrue(readme.startswith('My projects\n'))
        self.assertTrue(readme.endswith('\nMy contact links'))
        self.assertEqual((self.root / 'assets/project.svg').read_text(encoding='utf-8'), '<svg/>')
        self.assertFalse(old_image.exists())
        before = self.files()
        self.render_saved()
        self.run_updater('--check')
        self.assertEqual(self.files(), before)

    def test_failed_refresh_keeps_all_published_files(self):
        self.render_saved()
        valid = [record(pr(1, 'closed', merged=True))]
        cases = [
            ('API error', TimeoutError('GitHub unavailable'), valid, False),
            ('duplicate result', None, valid * 2, False),
            ('empty result', None, [], False),
            ('broken markers', None, valid, True),
        ]
        for name, error, records, broken_markers in cases:
            with self.subTest(failure=name):
                if broken_markers:
                    (self.root / 'README.md').write_text('My projects', encoding='utf-8')
                before = self.files()
                with patch.object(profile, 'collect_pull_requests', side_effect=error,
                                  return_value=(records, None)):
                    with self.assertRaises((ValueError, TimeoutError)):
                        self.run_updater()
                self.assertEqual(self.files(), before)


if __name__ == '__main__':
    unittest.main()
