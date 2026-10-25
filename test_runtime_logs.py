"""Recent-history retention through the real filesystem, never game access."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from runtime_logs import (BACKUP_COUNT, MAX_FILE_BYTES, MAX_RECORD_BYTES,
                          TOTAL_LOG_BYTES, BoundedRuntimeLog)


def retained(directory):
    paths = [directory / "mirealmbot-session.2.jsonl",
             directory / "mirealmbot-session.1.jsonl",
             directory / "mirealmbot-session.jsonl"]
    return [json.loads(line) for path in paths if path.exists()
            for line in path.read_bytes().splitlines()]


class RuntimeLogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def logger(self):
        logger = BoundedRuntimeLog(self.directory)
        self.addCleanup(logger.close)
        return logger

    def test_rotation_keeps_newest_complete_utf8_records_within_shared_quota(self):
        logger = self.logger()
        for sequence in range(100):
            if sequence == 50:
                logger.close()
                logger = self.logger()
            logger.record({"event": "sample", "sequence": sequence, "text": "á\n" * 9000})
        logger.close()
        entries = retained(self.directory)
        sequences = [entry["sequence"] for entry in entries]
        self.assertEqual(sequences[-1], 99)
        self.assertGreater(sequences[0], 0)
        self.assertEqual(sequences, list(range(sequences[0], 100)))
        paths = list(self.directory.glob("*.jsonl"))
        self.assertEqual(len(paths), BACKUP_COUNT + 1)
        self.assertLessEqual(sum(path.stat().st_size for path in paths), TOTAL_LOG_BYTES)
        for path in paths:
            self.assertLessEqual(path.stat().st_size, MAX_FILE_BYTES)
            self.assertTrue(path.read_bytes().endswith(b"\n"))
        self.assertEqual((self.directory / "mirealmbot-session.lock").stat().st_size, 1)

    def test_session_reopen_appends_recent_history_without_more_files(self):
        first = self.logger()
        first.record({"event": "start", "session_id": "one"})
        first.close()
        second = self.logger()
        second.record({"event": "start", "session_id": "two"})
        self.assertEqual([entry["session_id"] for entry in retained(self.directory)], ["one", "two"])
        self.assertEqual(len(list(self.directory.glob("*.jsonl"))), 1)

    def test_old_bounded_store_is_retired_without_removing_similarly_named_evidence(self):
        old = [self.directory / name for name in (
            "live-autonexus.jsonl", "live-autonexus.1.jsonl", "live-autonexus.2.jsonl")]
        for path in old:
            path.write_bytes(b'{"event":"old session"}\n')
        unrelated = self.directory / "live-autonexus.research.jsonl"
        unrelated.write_bytes(b"keep this evidence")
        logger = self.logger()
        self.assertEqual(logger.path.name, "mirealmbot-session.jsonl")
        self.assertEqual(logger.legacy_files_removed, 3)
        self.assertTrue(all(not path.exists() for path in old))
        self.assertEqual(unrelated.read_bytes(), b"keep this evidence")

    def test_oversized_payload_is_explicitly_omitted_without_blocking_next_record(self):
        logger = self.logger()
        logger.record({"event": "prediction_transport", "pid": 42,
                       "captured_at_utc": "2000-01-01T00:00:00Z",
                       "events": ["x" * (MAX_RECORD_BYTES * 10)]})
        logger.record({"event": "telemetry", "sequence": 2})
        entries = retained(self.directory)
        self.assertEqual(entries[0]["event"], "log_entry_omitted")
        self.assertEqual(entries[0]["original_event"], "prediction_transport")
        self.assertEqual(entries[0]["max_record_bytes"], MAX_RECORD_BYTES)
        self.assertEqual(entries[0]["pid"], 42)
        self.assertEqual(entries[1]["sequence"], 2)
        self.assertLess(logger.path.stat().st_size, MAX_RECORD_BYTES)

    def test_encoded_utf8_and_escaping_count_toward_the_record_limit(self):
        logger = self.logger()
        for text in ("😀" * 20000, "\x00" * 20000):
            logger.record({"event": "sample", "text": text})
        self.assertEqual([entry["event"] for entry in retained(self.directory)],
                         ["log_entry_omitted", "log_entry_omitted"])

    def test_partial_write_is_rolled_back_before_later_session_appends(self):
        logger = self.logger()
        logger.record({"event": "before"})
        underlying = logger._handle

        class FailingWrite:
            def write(self, data):
                underlying.write(data[:7])
                raise OSError("disk full after a partial write")

            def __getattr__(self, name):
                return getattr(underlying, name)

        logger._handle = FailingWrite()
        with self.assertRaisesRegex(OSError, "disk full"):
            logger.record({"event": "partial"})
        logger.close()
        reopened = self.logger()
        reopened.record({"event": "after"})
        self.assertEqual([entry["event"] for entry in retained(self.directory)], ["before", "after"])

    def test_startup_repairs_incomplete_last_line_without_reinterpreting_evidence(self):
        current = self.directory / "mirealmbot-session.jsonl"
        current.write_bytes(b'{"event":"complete"}\n{"event":"incomplete')
        logger = self.logger()
        logger.record({"event": "new"})
        self.assertEqual([entry["event"] for entry in retained(self.directory)], ["complete", "new"])

    def test_startup_enforces_existing_owned_file_bound_and_keeps_complete_recent_lines(self):
        current = self.directory / "mirealmbot-session.jsonl"
        line = (json.dumps({"event": "old", "text": "x" * 1000}) + "\n").encode()
        current.write_bytes(line * (MAX_FILE_BYTES // len(line) + 30))
        logger = self.logger()
        logger.record({"event": "newest"})
        self.assertLessEqual(sum(path.stat().st_size for path in self.directory.glob("*.jsonl")),
                             TOTAL_LOG_BYTES)
        self.assertEqual(retained(self.directory)[-1]["event"], "newest")

    def test_only_exact_legacy_runtime_names_are_removed_even_with_live_game_pid(self):
        legacy = self.directory / f"live-autonexus-20261024T235821.542704Z-pid{os.getpid()}-{'a' * 32}.jsonl"
        legacy.write_bytes(b"closed old runtime history\n")
        unrelated = ["damage-capture.jsonl", "rea-evidence.jsonl", "demo-combat.jsonl",
                     "live-autonexus-20261024T235821Z-pid42-research.jsonl",
                     "live-autonexus-20261024T235821.542704Z-pid42-a.jsonl"]
        for name in unrelated:
            (self.directory / name).write_bytes(b"private evidence remains\n")
        child = self.directory / "research"
        child.mkdir()
        (child / legacy.name).write_bytes(b"nested evidence remains\n")
        logger = self.logger()
        self.assertFalse(legacy.exists())
        self.assertEqual(logger.legacy_files_removed, 1)
        for name in unrelated:
            self.assertEqual((self.directory / name).read_bytes(), b"private evidence remains\n")
        self.assertEqual((child / legacy.name).read_bytes(), b"nested evidence remains\n")

    @unittest.skipUnless(os.name == "nt", "Windows CRT sharing controls legacy active writers")
    def test_busy_legacy_writer_is_preserved_then_removed_on_next_start(self):
        legacy = self.directory / f"live-autonexus-20261024T235821.542704Z-pid42-{'b' * 32}.jsonl"
        with legacy.open("w", encoding="utf-8") as old_writer:
            old_writer.write('{"event":"old session"}\n')
            old_writer.flush()
            logger = self.logger()
            self.assertTrue(legacy.exists())
            self.assertEqual(logger.legacy_files_retained, 1)
            logger.close()
        next_session = self.logger()
        self.assertFalse(legacy.exists())
        self.assertEqual(next_session.legacy_files_removed, 1)

    def test_second_writer_cannot_rotate_or_mutate_first_history(self):
        first = self.logger()
        first.record({"event": "owner"})
        before = first.path.read_bytes()
        with self.assertRaises(OSError):
            BoundedRuntimeLog(self.directory)
        self.assertEqual(first.path.read_bytes(), before)
        first.record({"event": "still owner"})
        self.assertEqual(retained(self.directory)[-1]["event"], "still owner")

    def test_rotation_failure_keeps_oldest_boundary_and_releases_writer_on_close(self):
        logger = self.logger()
        logger.record({"event": "first"})
        logger._size = MAX_FILE_BYTES
        with mock.patch.object(Path, "replace", side_effect=OSError("file busy")):
            with self.assertRaisesRegex(OSError, "file busy"):
                logger.record({"event": "next"})
        logger.close()
        reopened = self.logger()
        reopened.record({"event": "recovered"})
        self.assertEqual([entry["event"] for entry in retained(self.directory)], ["first", "recovered"])


if __name__ == "__main__":
    unittest.main()
