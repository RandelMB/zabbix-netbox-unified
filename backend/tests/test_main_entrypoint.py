from pathlib import Path
import unittest


class MainEntrypointTests(unittest.TestCase):
    def test_main_is_bootstrap_only(self) -> None:
        main_path = Path("/app/main.py")
        content = main_path.read_text(encoding="utf-8")
        self.assertLessEqual(len(content.splitlines()), 150)
        self.assertNotIn('@app.get("', content)
        self.assertNotIn('@app.post("', content)
        self.assertNotIn('@app.put("', content)
        self.assertNotIn('@app.patch("', content)
        self.assertNotIn('@app.delete("', content)
