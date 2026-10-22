import sqlite3
import unittest
from unittest.mock import Mock

from chatlog.models import ChatlogBatchMessagesRequest
from chatlog.wechat_db_v2 import WeChatDBReaderV2, _talker_to_table_name


class BatchOffsetTest(unittest.TestCase):
    def test_offset_is_applied_after_merging_databases(self):
        talker = "room@chatroom"
        table = _talker_to_table_name(talker)
        databases = {}
        for name, ids in [("first", [1, 3, 5]), ("second", [2, 4, 6])]:
            connection = sqlite3.connect(":memory:")
            self.addCleanup(connection.close)
            connection.execute("CREATE TABLE Name2Id(user_name TEXT)")
            connection.execute(f"CREATE TABLE {table}(sort_seq, server_id, local_type, real_sender_id, "
                               "local_id, create_time, message_content, status, source)")
            connection.executemany(f"INSERT INTO {table} VALUES(101, ?, 1, 1, ?, 1, 'body', 0, '')", [(i, i) for i in ids])
            databases[name] = connection
        reader = object.__new__(WeChatDBReaderV2)
        reader._get_dbs_for_time_range = Mock(return_value=list(databases))
        reader._tables_exist = Mock(return_value={table})
        reader._query = lambda path, sql: ([], databases[path].execute(sql).fetchall())
        reader._process_rows = lambda group, is_chatroom, rows: [
            (row[0], group, "", row[2], row[6], row[5], False, row[1], row[4], []) for row in rows
        ]
        reader._get_chatroom_display_names = Mock(return_value={})
        reader._batch_get_contact_names = Mock(return_value={})
        reader._finalize_messages = lambda rows, *_: rows
        result = reader.get_messages_since([(talker, 101)], limit=2, offset=2)
        self.assertEqual([row[8] for row in result[talker]], [3, 4])

    def test_existing_requests_default_to_zero_offset(self):
        request = ChatlogBatchMessagesRequest(items=[{"group": "room@chatroom", "since_seq": 101}])
        self.assertEqual(request.offset, 0)

    def test_one_failed_database_rejects_partial_batch(self):
        reader = object.__new__(WeChatDBReaderV2)
        reader._get_dbs_for_time_range = Mock(return_value=["good", "locked"])
        reader._tables_exist = Mock(side_effect=lambda path, tables: set(tables))
        reader._query = Mock(side_effect=[([], []), RuntimeError("database locked")])
        with self.assertRaisesRegex(RuntimeError, "database locked"):
            reader.get_messages_since([("room@chatroom", 101)])
        self.assertTrue(reader._get_dbs_for_time_range.call_args.kwargs["strict"])


if __name__ == "__main__":
    unittest.main()
