import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("polestar_scraper", ROOT / "scripts" / "scrape.py")
SCRAPER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCRAPER)


class PolestarApiParserTests(unittest.TestCase):
    def test_parses_versions_and_flattens_structured_notes(self):
        document = {
            "releaseNotesDocument": {
                "body": [
                    {
                        "subtype": "release-notes",
                        "cmsSoftwareVersion": 26380,
                        "children": [
                            {"type": "title", "children": "Software Version P4.3.0"},
                            {"type": "paragraph", "children": ["A general ", "improvement."]},
                            {
                                "type": "paragraph",
                                "validities": "MU66",
                                "children": ["Market-specific improvement."],
                            },
                            {
                                "type": "paragraph",
                                "validities": "!MU32",
                                "children": ["Available in all other markets."],
                            },
                        ],
                    },
                    {
                        "subtype": "release-notes",
                        "cmsSoftwareVersion": 26380,
                        "validities": "MU64",
                        "children": [
                            {"type": "title", "children": "Software Version P4.3.0"},
                            {"type": "paragraph", "children": ["Second market note."]},
                        ],
                    },
                ]
            }
        }

        versions = SCRAPER.parse_api_release_notes(document)

        self.assertEqual(len(versions), 1)
        self.assertEqual(versions[0]["version"], "P4.3.0")
        self.assertEqual(
            versions[0]["notes"],
            [
                "A general improvement.",
                "Market-specific improvement.",
                "Available in all other markets.",
                "Second market note.",
            ],
        )
        self.assertEqual(versions[0]["internal_version"], 26380)
        self.assertEqual(
            versions[0]["market_specific"],
            ["Market-specific improvement.", "Second market note."],
        )

    def test_parses_p5_manual_html_without_remix_blob(self):
        page = """
        <html><body>
          <h2>Updates in Software Version P5.0.3</h2>
          <p>This software update provides general improvements and bug fixes.</p>
          <h2>Updates in Software Version P5.0.2</h2>
          <p>Bug fix for when the Polestar app is showing incorrect car status information.</p>
          <h2>Updates in Software Version P5.0.1</h2>
          <p>This software update provides general improvements and compatibility updates.</p>
        </body></html>
        """
        versions = SCRAPER.parse_manual_html_versions(page)
        self.assertEqual([v["version"] for v in versions], ["P5.0.3", "P5.0.2", "P5.0.1"])
        self.assertEqual(versions[0]["notes"], ["This software update provides general improvements and bug fixes."])

    def test_negative_only_gate_is_not_market_specific(self):
        self.assertEqual(SCRAPER.positive_gates("(!MU13 & !MU32)"), [])
        self.assertEqual(SCRAPER.positive_gates("(MU66 | MU64)"), ["MU66", "MU64"])


if __name__ == "__main__":
    unittest.main()
