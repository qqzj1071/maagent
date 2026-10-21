import unittest
from unittest.mock import Mock, patch

from maagent.control.maaend_popup import MaaEndPopupMonitor, update_acknowledgement
from maagent.control.popup import OcrItem
from maagent.core.maaend import MaaEndOrchestrator
from maagent.report.generator import RunReport


def item(text, x=500, y=200, score=.99):
    return OcrItem(text, score, (x, y), [])


class UpdatePopupTests(unittest.TestCase):
    def test_title_and_ack_required(self):
        button = item("知道了", 650, 500)
        self.assertIs(update_acknowledgement([item("更新完成"), button], 1000, 700), button)
        for items in (
            [item("任务日志：更新完成"), button],
            [item("更新完成"), item("立即安装", 650, 500)],
            [item("正在更新"), button],
            [item("更新完成")],
            [item("更新完成", score=.5), button],
            [item("更新完成"), button, item("知道了", 500, 450)],
            [item("更新完成", 950, 200), button],
        ):
            self.assertIsNone(update_acknowledgement(items, 1000, 700))

    @patch("maagent.control.maaend_popup.win32gui")
    @patch("maagent.control.maaend_popup.capture_window_screen")
    def test_background_game_is_not_interrupted(self, capture, gui):
        monitor = MaaEndPopupMonitor()
        monitor.ui = Mock()
        monitor.ui.hwnd.return_value = 1
        gui.GetForegroundWindow.return_value = 2
        self.assertFalse(monitor.scan_once())
        monitor.ui.foreground.assert_not_called()
        capture.assert_not_called()

    def scan(self, after, foreground_values=None):
        monitor = MaaEndPopupMonitor()
        monitor.ui = Mock()
        monitor.ui.hwnd.return_value = 1
        img = Mock(size=(1000, 700))
        with patch("maagent.control.maaend_popup.win32gui") as gui, \
             patch("maagent.control.maaend_popup.capture_window_screen", return_value=img), \
             patch("maagent.control.maaend_popup.recognize", side_effect=[
                 [item("更新完成"), item("知道了", 650, 500)], after]), \
             patch("maagent.control.maaend_popup._click_screen") as click, \
             patch("maagent.control.maaend_popup.time.sleep"):
            gui.GetForegroundWindow.side_effect = foreground_values or [1, 1, 1]
            gui.GetWindowRect.return_value = (100, 100, 1100, 800)
            gui.IsWindow.return_value = True
            result = monitor.scan_once()
            gui.PostMessage.assert_not_called()
            return result, monitor.closed, click.call_count

    def test_success_requires_post_click_verification(self):
        result, closed, clicks = self.scan([item("开始任务")])
        self.assertTrue(result)
        self.assertEqual(len(closed), 1)
        self.assertEqual(clicks, 1)

    def test_remaining_modal_or_blank_capture_not_success(self):
        for after in ([item("更新完成")], []):
            result, closed, clicks = self.scan(after)
            self.assertFalse(result)
            self.assertEqual(closed, [])
            self.assertEqual(clicks, 1)

    def test_focus_change_during_ocr_cancels_click(self):
        result, closed, clicks = self.scan([], [1, 2])
        self.assertFalse(result)
        self.assertEqual(clicks, 0)

    def test_cleanup_only_after_completion_and_before_log_fetch(self):
        for result in ("complete", "timeout", "not_started"):
            runner = MaaEndOrchestrator({})
            order = []
            api = Mock()
            api.instance_id.return_value = "test"
            runner._api = Mock(return_value=api)
            runner._wait_api = Mock(return_value=True)
            runner._ensure_global_hotkeys = Mock()
            runner._last_log_timestamp = Mock(return_value="")
            runner._trigger_start = Mock(return_value=True)
            runner._wait_done = Mock(side_effect=lambda *a: order.append("wait") or result)
            runner._dismiss_popups = Mock(side_effect=lambda **kw: order.append("popup"))
            runner._fetch_entries = Mock(side_effect=lambda *a: order.append("logs") or [])
            runner._populate = Mock()
            with patch("maagent.core.maaend.process_running", return_value=True), \
                 patch("maagent.core.maaend.time.sleep"):
                runner._run(RunReport(game="终末地"))
            self.assertEqual(order, ["wait", "popup", "logs"] if result == "complete"
                             else ["wait", "logs"])


if __name__ == "__main__":
    unittest.main()
