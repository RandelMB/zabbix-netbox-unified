import os
import unittest
from unittest import mock

from app.core.settings import env_flag


class EnvFlagTests(unittest.TestCase):
    def test_env_flag_uses_default_when_missing(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertTrue(env_flag("MISSING_FLAG", True))
            self.assertFalse(env_flag("MISSING_FLAG", False))

    def test_env_flag_parses_truthy_values(self) -> None:
        for value in ("1", "true", "TRUE", "yes", "on"):
            with self.subTest(value=value):
                with mock.patch.dict(os.environ, {"TEST_FLAG": value}, clear=True):
                    self.assertTrue(env_flag("TEST_FLAG"))

    def test_env_flag_parses_falsy_values(self) -> None:
        for value in ("0", "false", "FALSE", "no", "off", "unexpected"):
            with self.subTest(value=value):
                with mock.patch.dict(os.environ, {"TEST_FLAG": value}, clear=True):
                    self.assertFalse(env_flag("TEST_FLAG", True))
