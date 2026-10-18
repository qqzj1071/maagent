"""离线回归测试：不启动游戏、不读取个人配置、不发送邮件。"""
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from maagent.core.bettergi import LogTailer
from maagent.core.controller import LOG_SEP, MAX_REPORTS, WorkflowController
from maagent.core.scheduler import Scheduler


class TemporaryFiles(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)


class LogTailerTests(TemporaryFiles):
    def setUp(self):
        super().setUp()
        self.path = self.root / "better-genshin-impact-test.log"
        self.path.write_bytes(b"old log\n")
        self.tailer = LogTailer(str(self.root))

    def append(self, data):
        with self.path.open("ab") as stream:
            stream.write(data)

    def test_skips_history_and_does_not_repeat_lines(self):
        self.assertEqual(self.tailer.read_new(), [])
        self.append("任务启动\r\n\n".encode())
        self.assertEqual(self.tailer.read_new(), ["任务启动"])
        self.assertEqual(self.tailer.read_new(), [])

    def test_completion_marker_split_inside_utf8_character(self):
        marker = "一条龙和配置组任务结束".encode()
        self.append(marker[:4])
        self.assertEqual(self.tailer.read_new(), [])
        self.append(marker[4:] + b"\r")
        self.assertEqual(self.tailer.read_new(), [])
        self.append(b"\n")
        self.assertEqual(self.tailer.read_new(), [marker.decode()])

    def test_rotation_discards_old_partial_line(self):
        self.append(b"unfinished")
        self.tailer.read_new()
        rotated = self.root / "better-genshin-impact-next.log"
        rotated.write_bytes("任务启动\n".encode())
        stamp = self.path.stat().st_mtime_ns + 2_000_000_000
        os.utime(rotated, ns=(stamp, stamp))
        self.assertEqual(self.tailer.read_new(), ["任务启动"])

    def test_truncation_discards_old_partial_line(self):
        self.append(b"unfinished long line")
        self.tailer.read_new()
        self.path.write_bytes(b"new\n")
        self.assertEqual(self.tailer.read_new(), ["new"])

    def test_file_created_after_monitor_starts(self):
        empty = self.root / "empty"
        empty.mkdir()
        tailer = LogTailer(str(empty))
        self.assertEqual(tailer.read_new(), [])
        (empty / self.path.name).write_bytes(b"start\n")
        self.assertEqual(tailer.read_new(), ["start"])

    def test_disappearing_candidate_does_not_abort_scan(self):
        stat = Path.stat

        def disappearing(path, *args, **kwargs):
            if path == self.path:
                raise FileNotFoundError(path)
            return stat(path, *args, **kwargs)

        with patch.object(Path, "stat", disappearing):
            self.assertEqual(self.tailer.read_new(), [])


class ControllerTests(TemporaryFiles):
    def controller(self, config=None):
        return WorkflowController(config or {}, self.root / "reports.jsonl")

    def test_history_streams_and_keeps_last_valid_objects(self):
        path = self.root / "reports.jsonl"
        with path.open("w", encoding="utf-8") as stream:
            for index in range(MAX_REPORTS + 10):
                stream.write(json.dumps({"id": str(index)}) + "\n")
            stream.write('invalid\n[]\nnull\n42\n"text"\n')
        with patch.object(Path, "read_text", side_effect=AssertionError("whole-file read")):
            controller = self.controller()
        self.assertEqual(controller.snapshot()["report_count"], MAX_REPORTS)
        self.assertIsNone(controller.get_report("9"))
        self.assertEqual(controller.get_report("10"), {"id": "10"})
        self.assertEqual(controller.latest_report()["id"], str(MAX_REPORTS + 9))

    def test_unknown_adapter_never_falls_back_to_maa(self):
        controller = self.controller({"adapters": {"unknown": {"enabled": True}}})
        with self.assertRaisesRegex(RuntimeError, "没有可执行"):
            controller.start(["unknown"])
        self.assertFalse(controller.is_running())

    def test_message_preserves_embedded_separator(self):
        controller = self.controller()
        controller.on_log(f"12:00:00{LOG_SEP}INFO{LOG_SEP}one{LOG_SEP}two")
        self.assertEqual(controller.logs()[0]["message"], f"one{LOG_SEP}two")


class SchedulerTests(TemporaryFiles):
    def scheduler(self, **chain):
        return Scheduler({"workflow": {"chain": {"enabled": True, **chain}}},
                         self.root / "schedule.json")

    def test_legacy_migration_retains_scheduled_duplicates(self):
        tasks = [{"software": "maa", "id": "a"},
                 {"software": "maa", "id": "b"},
                 {"software": "unknown"}, None]
        scheduler = self.scheduler(tasks=tasks)
        seq, scheduled = scheduler._task_lists()
        self.assertEqual([task["id"] for task in seq], ["a"])
        self.assertEqual([task["id"] for task in scheduled], ["a", "b"])

    def test_explicit_empty_list_is_not_replaced_by_legacy(self):
        scheduler = self.scheduler(sequential_tasks=[], tasks=[{"software": "maa"}])
        self.assertEqual(scheduler.tasks(), [])

    def test_partial_migration_preserves_existing_list(self):
        scheduler = self.scheduler(sequential_tasks=[{"software": "bgi"}],
                                   tasks=[{"software": "maa"}])
        seq, scheduled = scheduler._task_lists()
        self.assertEqual(seq, [{"software": "bgi"}])
        self.assertEqual(scheduled, [{"software": "maa"}])

    def test_four_am_boundary_and_persisted_deduplication(self):
        scheduler = self.scheduler(sequential_tasks=[{"software": "maa"}],
                                   start_slots=[{"time": "03:30", "days": [4]}])
        # 周六凌晨属于周五游戏日。
        now = datetime(2026, 10, 17, 3, 30)
        event, = scheduler.poll(now)
        scheduler.mark(event, now)
        reloaded = Scheduler(scheduler.config, scheduler.state_file)
        self.assertEqual(reloaded.poll(now), [])
        self.assertEqual(reloaded.state["chain"]["date"], "2026-10-16")

    def test_empty_days_never_runs(self):
        scheduler = self.scheduler(sequential_tasks=[{"software": "bgi"}],
                                   start_slots=[{"time": "08:00", "days": []}])
        self.assertEqual(scheduler.poll(datetime(2026, 10, 17, 8)), [])
        self.assertIsNone(scheduler.next_run(datetime(2026, 10, 17, 8)))


if __name__ == "__main__":
    unittest.main()
