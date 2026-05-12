from pathlib import Path
import unittest


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_legacy_runtime_file_is_gone(self) -> None:
        self.assertFalse(Path("/app/app/services/legacy_runtime.py").exists())

    def test_routes_do_not_import_legacy_runtime(self) -> None:
        routes_dir = Path("/app/app/routes")
        for path in routes_dir.glob("*.py"):
            content = path.read_text(encoding="utf-8")
            self.assertNotIn("legacy_runtime", content, msg=f"legacy_runtime import found in {path.name}")
