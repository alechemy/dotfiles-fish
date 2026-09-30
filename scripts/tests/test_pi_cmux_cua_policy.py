import importlib.util
import tempfile
import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[2] / "stow/pi/.pi/agent/extensions/cmux-cua/policy.py"
spec = importlib.util.spec_from_file_location("cmux_cua_policy", SOURCE)
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


class ComputerUseConfigurationTests(unittest.TestCase):
    def test_jsonc_comments_trailing_commas_and_quoted_comment_markers(self):
        value = policy.jsonc('''{
          // The app supports JSONC.
          "computerUse": {"enabled": false,},
          "url": "https://example.invalid/a/*b*/?q=//x,}",
          "escaped": "a\\\"//b",
          /* A block comment. */
          "array": [1, 2,],
        }''')
        self.assertEqual(value["computerUse"], {"enabled": False})
        self.assertEqual(value["url"], "https://example.invalid/a/*b*/?q=//x,}")
        self.assertEqual(value["escaped"], 'a"//b')
        self.assertEqual(value["array"], [1, 2])

    def test_missing_file_is_distinct_from_a_present_default(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "cmux.json"
            self.assertIsNone(policy.configuration_enabled(path))
            path.write_text("{}")
            self.assertTrue(policy.configuration_enabled(path))
            path.write_text('{"computerUse":{"enabled":false}}')
            self.assertFalse(policy.configuration_enabled(path))
            path.write_text('{"computerUse":{"enabled":true}}')
            self.assertTrue(policy.configuration_enabled(path))

    def test_ambiguous_or_malformed_configuration_fails_closed(self):
        for text in ["[]", "null", "", '{"computerUse":false}', '{"computerUse":{"enabled":1}}', '{"computerUse":{"enabled":"false"}}', "{/* unclosed", "{} trailing", "{" + " " * (1024 * 1024)]:
            with self.subTest(text=text[:80]), tempfile.TemporaryDirectory() as root:
                path = Path(root) / "cmux.json"
                path.write_text(text)
                with self.assertRaises(ValueError):
                    policy.configuration_enabled(path)


if __name__ == "__main__":
    unittest.main()
