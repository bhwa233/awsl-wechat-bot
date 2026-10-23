import sqlite3
import tempfile
import unittest
from pathlib import Path

from common.models import MessageType
from cortex.services.scheduled_task import ScheduledTaskCreate, ScheduledTaskService, ScheduledTaskUpdate


class LegacyTaskSchemaTest(unittest.TestCase):
    def test_legacy_constraints_are_removed_after_preserving_payloads(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "tasks.db")
            with sqlite3.connect(path) as connection:
                connection.executescript("""
                    CREATE TABLE scheduled_tasks (
                        id INTEGER PRIMARY KEY, name TEXT NOT NULL, cron_expression TEXT NOT NULL,
                        message TEXT NOT NULL, message_type TEXT NOT NULL, image_base64 TEXT NOT NULL,
                        is_file BOOLEAN NOT NULL, filename TEXT NOT NULL, target_groups TEXT NOT NULL,
                        enabled BOOLEAN NOT NULL, created_at TEXT, updated_at TEXT, last_run TEXT,
                        mention_targets TEXT NOT NULL DEFAULT '[]'
                    );
                    CREATE INDEX task_enabled ON scheduled_tasks(enabled);
                """)
                for ident, kind, message, image, is_file in (
                    (1, "text", "hello", "", 0),
                    (2, "image", "", "aW1hZ2U=", 0),
                    (3, "text", "ZmlsZQ==", "", 1),
                ):
                    connection.execute("""
                        INSERT INTO scheduled_tasks VALUES (?, ?, '0 9 * * *', ?, ?, ?, ?,
                            'attachment.bin', '[]', 0, 'created', 'updated', 'last-run', '[]')
                    """, (ident, str(ident), message, kind, image, is_file))

            service = ScheduledTaskService(path)
            try:
                tasks = {task.id: task for task in service.get_all_tasks()}
                self.assertEqual(tasks[1].message, "hello")
                self.assertEqual((tasks[2].message_type, tasks[2].message), (MessageType.IMAGE, "aW1hZ2U="))
                self.assertEqual((tasks[3].message_type, tasks[3].message), (MessageType.FILE, "ZmlsZQ=="))
                for task in tasks.values():
                    self.assertEqual((task.created_at, task.updated_at, task.last_run), ("created", "updated", "last-run"))
                created = service.create_task(ScheduledTaskCreate(
                    name="new", cron_expression="*/5 * * * *", message="hello", enabled=False,
                ))
                self.assertIsNotNone(created)
                self.assertEqual(service.update_task(created.id, ScheduledTaskUpdate(message="updated")).message, "updated")
                self.assertTrue(service.delete_task(created.id))
            finally:
                service.close()

            service = ScheduledTaskService(path)
            try:
                self.assertEqual(len(service.get_all_tasks()), 3)
                with sqlite3.connect(path) as connection:
                    columns = {row[1] for row in connection.execute("PRAGMA table_info(scheduled_tasks)")}
                    self.assertTrue(columns.isdisjoint({"image_base64", "is_file"}))
                    self.assertIn("mention_targets", columns)
                    self.assertIsNotNone(connection.execute("SELECT name FROM sqlite_master WHERE name='task_enabled'").fetchone())
                    self.assertEqual(connection.execute("""
                        SELECT COUNT(*) FROM schema_migrations
                        WHERE version='20261024_001_scheduled_tasks_drop_legacy_payload_columns'
                    """).fetchone()[0], 1)
            finally:
                service.close()
