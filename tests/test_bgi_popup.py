import unittest
from unittest.mock import Mock, patch

from maagent.control.bgi_popup import BgiPopupMonitor, is_multiple_games
from maagent.core.bettergi import BetterGiOrchestrator

BODY = 'Multiple games are detected.Please exit the existing client.\nError Code:(3,0,0)'


class BgiPopupTests(unittest.TestCase):
    def test_exact_error_only(self):
        self.assertTrue(is_multiple_games(BODY))
        for text in (BODY.replace('(3,0,0)', '(1,0,0)'), 'Multiple games',
                     '日志：' + BODY, 'Access denied'):
            self.assertFalse(is_multiple_games(text))

    def scan(self, *, moved=False, remains=False, body=BODY, button='确定'):
        monitor = BgiPopupMonitor()
        with patch('maagent.control.bgi_popup.win32gui') as gui, \
             patch('maagent.control.bgi_popup.capture_window_screen', return_value=None), \
             patch('maagent.control.bgi_popup._click_screen') as click, \
             patch('maagent.control.bgi_popup.time.sleep'):
            gui.GetForegroundWindow.side_effect = [1, 9 if moved else 1]
            gui.GetClassName.side_effect = lambda h: {1:'#32770', 2:'Static', 3:'Button'}[h]
            gui.GetWindowText.side_effect = lambda h: {1:'错误', 2:body, 3:button}[h]
            gui.EnumChildWindows.side_effect = lambda h, cb, arg: [cb(c, arg) for c in (2, 3)]
            gui.GetWindowRect.side_effect = lambda h: (0, 0, 600, 250) if h == 1 else (450, 200, 550, 240)
            gui.IsWindowVisible.return_value = True
            gui.IsWindowEnabled.return_value = True
            gui.IsChild.return_value = True
            gui.IsWindow.return_value = remains
            result = monitor.scan_once()
            return result, monitor, click.call_count

    def test_acknowledges_and_verifies_gone(self):
        result, monitor, clicks = self.scan()
        self.assertTrue(result)
        self.assertEqual(clicks, 1)
        self.assertEqual(len(monitor.closed), 1)

    def test_unrelated_dialog_or_focus_change_not_clicked(self):
        for kwargs in ({'moved':True}, {'body':'其它错误'}, {'button':'立即退出'}):
            result, monitor, clicks = self.scan(**kwargs)
            self.assertFalse(result)
            self.assertEqual(clicks, 0)
            self.assertFalse(monitor.closed)

    def test_click_is_not_proof_of_closure(self):
        result, monitor, clicks = self.scan(remains=True)
        self.assertFalse(result)
        self.assertEqual(clicks, 1)
        self.assertFalse(monitor.closed)

    def test_attempt_limit(self):
        monitor = BgiPopupMonitor()
        monitor._attempts = 3
        with patch.object(monitor, '_scan') as scan:
            self.assertFalse(monitor.scan_once())
            scan.assert_not_called()

    def test_process_exit_needs_completion_log(self):
        flow = BetterGiOrchestrator({})
        flow._tailer = Mock()
        flow._tailer.read_new.return_value = ['一条龙任务执行: 1/6']
        with patch('maagent.core.bettergi.process_running', return_value=False), \
             patch('maagent.core.bettergi.time.sleep'), \
             patch('maagent.core.bettergi.time.monotonic', side_effect=[0, 31]):
            self.assertEqual(flow._wait_done(60), 'process_exited')

    def test_final_log_can_arrive_after_process_exit(self):
        flow = BetterGiOrchestrator({})
        flow._tailer = Mock()
        flow._tailer.read_new.side_effect = [[], ['一条龙和配置组任务结束']]
        with patch('maagent.core.bettergi.process_running', return_value=False), \
             patch('maagent.core.bettergi.time.sleep'):
            self.assertEqual(flow._wait_done(60), 'complete')


if __name__ == '__main__':
    unittest.main()
