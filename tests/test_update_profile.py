import copy
import importlib.util
from pathlib import Path
import unittest
from urllib.parse import parse_qs, urlsplit
import xml.etree.ElementTree as ET

MODULE = Path(__file__).resolve().parents[1] / "scripts" / "update_profile.py"
SPEC = importlib.util.spec_from_file_location("profile_updater", MODULE)
profile = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(profile)


def issue(number, state="open", merged_at=None, repository="upstream/tool"):
    return {
        "user": {"login": "nigel"}, "repository_url": f"https://api.github.com/repos/{repository}",
        "number": number, "state": state, "title": "Keep <values> | stable [now]",
        "pull_request": {"merged_at": merged_at}, "updated_at": "2026-10-18T05:00:00Z",
    }


def response(items, **extra):
    return {"total_count": len(items), "incomplete_results": False, "items": items, **extra}


def snapshot(records):
    return {"username": "nigel", "checked_on": "2026-10-18", "pull_requests": records}


CONFIG = {"username": "nigel", "groups": [{"title": "Tools", "repositories": {"upstream/tool": "Tool"}}]}


class ProfileUpdaterTests(unittest.TestCase):
    def test_open_closed_and_merged_are_distinct(self):
        items = [issue(1), issue(2, "closed"), issue(3, "closed", "2026-10-17T10:00:00Z")]
        records = profile.collect_pull_requests("nigel", lambda _: response(items))
        self.assertEqual([p["status"] for p in records], ["Open", "Closed", "Merged"])
        self.assertEqual(profile.summary(records), {"total": 3, "open": 1, "closed": 1, "merged": 1, "repositories": 1})

    def test_missing_merge_information_is_fetched(self):
        item = issue(2, "closed")
        del item["pull_request"]["merged_at"]
        calls = []

        def get_json(path):
            calls.append(path)
            return response([item]) if path.startswith("/search/") else {"merged_at": "2026-10-17T10:00:00Z"}

        self.assertEqual(profile.collect_pull_requests("nigel", get_json)[0]["status"], "Merged")
        self.assertEqual(calls[-1], "/repos/upstream/tool/pulls/2")

    def test_own_repository_prs_are_excluded(self):
        items = [issue(1), issue(2, repository="Nigel/personal")]
        records = profile.collect_pull_requests("nigel", lambda _: response(items))
        self.assertEqual(len(records), 1)

    def test_every_search_page_is_collected(self):
        pages = []

        def get_json(path):
            page = int(parse_qs(urlsplit(path).query)["page"][0])
            pages.append(page)
            items = [issue(i) for i in range(1, 101)] if page == 1 else [issue(101)]
            return response(items, total_count=101)

        records = profile.collect_pull_requests("nigel", get_json)
        self.assertEqual(len(records), 101)
        self.assertEqual(pages, [1, 2])

    def test_partial_or_changed_search_is_rejected(self):
        for payload in [response([issue(1)], incomplete_results=True), response([], total_count=1001), response([], total_count=2)]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                profile.collect_pull_requests("nigel", lambda _: payload)

        calls = iter([response([issue(i) for i in range(1, 101)], total_count=101), response([issue(101)], total_count=102)])
        with self.assertRaisesRegex(ValueError, "changed"):
            profile.collect_pull_requests("nigel", lambda _: next(calls))

    def test_foreign_author_and_unsafe_repository_are_rejected(self):
        wrong_author, unsafe_url = issue(1), issue(2)
        wrong_author["user"]["login"] = "someone-else"
        unsafe_url["repository_url"] = "https://example.com/steal-token"
        for item in [wrong_author, unsafe_url]:
            with self.subTest(item=item), self.assertRaises(ValueError):
                profile.collect_pull_requests("nigel", lambda _: response([item]))

    def test_duplicate_snapshot_cannot_inflate_totals(self):
        record = profile.collect_pull_requests("nigel", lambda _: response([issue(1)]))[0]
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            profile.validate_snapshot(snapshot([record, copy.deepcopy(record)]), "nigel")

    def test_invalid_snapshot_state_and_link_are_rejected(self):
        record = profile.collect_pull_requests("nigel", lambda _: response([issue(1)]))[0]
        for key, value in [("status", "Accepted"), ("url", "https://example.com"), ("repository", "nigel/personal")]:
            broken = dict(record, **{key: value})
            with self.subTest(key=key), self.assertRaises(ValueError):
                profile.validate_snapshot(snapshot([broken]), "nigel")

    def test_titles_are_escaped_and_new_targets_remain_visible(self):
        records = profile.collect_pull_requests("nigel", lambda _: response([issue(1), issue(2, repository="new/repository")]))
        rendered = profile.render_contributions(snapshot(records), CONFIG)
        self.assertIn("More repositories", rendered)
        self.assertIn("new/repository", rendered)
        self.assertIn("&lt;values&gt; &#124; stable &#91;now&#93;", rendered)
        self.assertNotIn("<values>", rendered)
        self.assertIn("Explore all 2 pull requests", rendered)

    def test_refresh_preserves_curated_text_and_is_idempotent(self):
        readme = "My projects\n" + profile.START + "\nold\n" + profile.END + "\nMy contact links"
        once = profile.replace_section(readme, "New contribution data")
        self.assertEqual(profile.replace_section(once, "New contribution data"), once)
        self.assertTrue(once.startswith("My projects\n"))
        self.assertTrue(once.endswith("\nMy contact links"))

    def test_missing_duplicate_or_reversed_markers_fail(self):
        for text in ["no markers", profile.START * 2 + profile.END, profile.END + profile.START]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                profile.replace_section(text, "new")

    def test_both_summary_images_are_valid_and_accessible(self):
        totals = {"total": 41, "merged": 6, "open": 35, "repositories": 19}
        for compact in [False, True]:
            root = ET.fromstring(profile.stats_svg(totals, compact))
            self.assertEqual(root.attrib["role"], "img")
            numbers = {node.text for node in root.iter() if node.tag.endswith("text")}
            self.assertTrue({"41", "6", "35", "19"}.issubset(numbers))


if __name__ == "__main__":
    unittest.main()
