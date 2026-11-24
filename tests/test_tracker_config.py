import ast
import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class TrackerConfigurationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scraper = (ROOT / "scripts" / "scrape.py").read_text(encoding="utf-8")
        cls.workflow = (ROOT / ".github" / "workflows" / "update-data.yml").read_text(encoding="utf-8")
        cls.index = (ROOT / "index.html").read_text(encoding="utf-8")

        tree = ast.parse(cls.scraper)
        assignment = next(
            node for node in tree.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "MODELS" for target in node.targets)
        )
        cls.models = ast.literal_eval(assignment.value)

    def test_all_four_models_are_configured(self):
        expected = {
            "polestar-2": ("html", None),
            "polestar-3": ("api", "359"),
            "polestar-4": ("api", "814"),
            "polestar-5": ("api", "824"),
        }
        actual = {
            model["slug"]: (model["source"], model.get("model_code"))
            for model in self.models
        }
        self.assertEqual(actual, expected)

    def test_model_sources_and_manual_urls_are_valid(self):
        for model in self.models:
            self.assertIn(model["source"], {"html", "api"})
            self.assertIn(model["slug"], model["manual_url"])
            if model["source"] == "api":
                self.assertTrue(model.get("model_code"))

    def test_api_uses_verified_host_only(self):
        self.assertIn('"https://support-car-content.volvo.care"', self.scraper)
        self.assertNotIn('"https://support-car-content.polestar.volvo.care"', self.scraper)
        self.assertIn("for base in API_BASES:", self.scraper)

    def test_polestar_4_api_is_primary_and_manual_is_fallback(self):
        self.assertIn('"model_code": "814"', self.scraper)
        self.assertIn('meta["source_used"] = "api"', self.scraper)
        self.assertIn('meta["source_used"] = "manual_fallback"', self.scraper)
        self.assertIn("parse_api_release_notes", self.scraper)
        self.assertIn("market_specific", self.scraper)
        self.assertIn('"model_code": "824"', self.scraper)
        self.assertIn("parse_manual_html_versions", self.scraper)

    def test_partial_scrapes_preserve_data_without_discarding_successes(self):
        self.assertIn("successful_models += 1", self.scraper)
        self.assertIn("::warning title=Partial Polestar scrape::", self.scraper)
        self.assertIn("if successful_models == 0:", self.scraper)

    def test_generated_page_contains_every_model(self):
        data_match = re.search(r"^const DATA = (.*);$", self.index, re.MULTILINE)
        models_match = re.search(r"^const MODELS = (.*);$", self.index, re.MULTILINE)
        meta_match = re.search(r"^const META = (.*);$", self.index, re.MULTILINE)
        self.assertIsNotNone(data_match)
        self.assertIsNotNone(models_match)
        self.assertIsNotNone(meta_match)

        data = json.loads(data_match.group(1))
        page_models = json.loads(models_match.group(1))
        meta = json.loads(meta_match.group(1))
        expected_slugs = {model["slug"] for model in self.models}

        self.assertEqual(set(data), expected_slugs)
        self.assertEqual({model["slug"] for model in page_models}, expected_slugs)
        self.assertEqual(set(meta), expected_slugs)

    def test_data_and_feed_files_exist_for_each_model(self):
        for model in self.models:
            slug = model["slug"]
            with self.subTest(model=slug):
                data = json.loads((ROOT / "data" / f"{slug}.json").read_text(encoding="utf-8"))
                self.assertIsInstance(data, list)
                self.assertTrue((ROOT / "data" / f"{slug}.meta.json").exists())
                feed = (ROOT / f"feed-{slug}.xml").read_text(encoding="utf-8")
                self.assertIn("<rss", feed)
                self.assertIn(f"{slug}.xml", feed)

    def test_new_models_are_included_in_release_notifications(self):
        for slug in ("polestar-2", "polestar-3", "polestar-4", "polestar-5"):
            with self.subTest(model=slug):
                self.assertIn(f'"{slug}": "Polestar {slug[-1]}"', self.workflow)
                self.assertIn(f"'{slug}': 'https://www.polestar.com/uk/manual/", self.workflow)

        # Empty seed files represent a historical backfill, not new releases.
        self.assertIn("if not old:", self.workflow)


if __name__ == "__main__":
    unittest.main()
