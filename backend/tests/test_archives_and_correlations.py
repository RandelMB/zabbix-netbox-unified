import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

from app.repositories import app_db as app_db_module
from app.repositories.archive_repository import archive_status, clear_archive, list_archives, set_archive
from app.repositories.correlations_repository import find_correlation_by_item, list_correlations, remove_correlation_item, save_correlation
from app.schemas.correlations import CorrelationItemPayload, CorrelationLinkPayload


class ArchiveAndCorrelationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmpdir.name) / "test.db")
        self.original_db_path = app_db_module.cfg.APP_DB_PATH
        app_db_module.cfg.APP_DB_PATH = self.db_path
        app_db_module.init_app_db()

    def tearDown(self) -> None:
        app_db_module.cfg.APP_DB_PATH = self.original_db_path
        self.tmpdir.cleanup()

    def test_archive_round_trip(self) -> None:
        set_archive("netbox", "101", "sw1", {"site": "lab"})
        self.assertTrue(archive_status("netbox", "101"))
        archives = list_archives("netbox")
        self.assertEqual(archives[0]["details"]["site"], "lab")
        clear_archive("netbox", "101")
        self.assertFalse(archive_status("netbox", "101"))

    def test_correlation_conflict_is_rejected(self) -> None:
        save_correlation(
            CorrelationLinkPayload(
                zabbix=CorrelationItemPayload(id="z1"),
                netbox=CorrelationItemPayload(id="n1"),
            )
        )
        save_correlation(
            CorrelationLinkPayload(
                zabbix=CorrelationItemPayload(id="z2"),
                observium=CorrelationItemPayload(id="o2"),
            )
        )
        with self.assertRaises(HTTPException) as ctx:
            save_correlation(
                CorrelationLinkPayload(
                    zabbix=CorrelationItemPayload(id="z1"),
                    observium=CorrelationItemPayload(id="o2"),
                )
            )
        self.assertEqual(ctx.exception.status_code, 409)

    def test_remove_correlation_item_deletes_small_group(self) -> None:
        saved = save_correlation(
            CorrelationLinkPayload(
                zabbix=CorrelationItemPayload(id="z1"),
                netbox=CorrelationItemPayload(id="n1"),
            )
        )
        result = remove_correlation_item(saved["id"], "zabbix")
        self.assertEqual(result["status"], "deleted")
        self.assertIsNone(find_correlation_by_item("netbox", "n1"))
        self.assertEqual(list_correlations(), [])
