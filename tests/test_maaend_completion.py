import unittest
from unittest.mock import Mock, patch

from maagent.control.maaend_api import MaaEndApi
from maagent.core.maaend import MaaEndOrchestrator, _is_done_message


class MaaEndCompletionTests(unittest.TestCase):
    def wait(self, states, messages, timeout=25):
        runner = MaaEndOrchestrator({'adapters': {'maaend': {'auto_close': True}}})
        api = Mock()
        api.is_running.side_effect = states
        clock = [0.0]
        def sleep(seconds):
            clock[0] += seconds
        with patch('maagent.core.maaend.time.time', side_effect=lambda: clock[0]), \
             patch('maagent.core.maaend.time.sleep', side_effect=sleep), \
             patch.object(runner, '_new_messages', return_value=messages):
            result = runner._wait_done(api, 'instance', '', 10, timeout)
        return result, clock[0]

    def test_real_selling_completion_is_not_queue_completion(self):
        messages = ['任务已提交', '🏁 售卖任务已完成', '任务完成: 🛒据点交易', '任务开始: 📦自动囤货']
        self.assertFalse(_is_done_message(messages[1]))
        self.assertEqual(self.wait([True] * 8, messages)[0], 'timeout')

    def test_no_substring_or_kill_process_completion(self):
        for text in ('售卖任务已完成', '子任务结束', '任务完成: 结束进程', '未全部完成'):
            self.assertFalse(_is_done_message(text), text)

    def test_explicit_queue_completion(self):
        self.assertEqual(self.wait([False], ['任务已提交', '任务已全部完成！'])[0], 'complete')

    def test_active_state_overrides_completion_message(self):
        self.assertEqual(self.wait([True] * 8, ['任务已完成'])[0], 'timeout')

    def test_later_task_start_invalidates_completion(self):
        self.assertFalse(MaaEndOrchestrator._queue_done(['任务已完成', '任务开始: 自动囤货']))

    def test_missing_state_is_unknown(self):
        for state in ({}, {'running': None}, {'is_running': 'false'}):
            self.assertIsNone(MaaEndApi._is_running_flag(state))
        self.assertIs(MaaEndApi._is_running_flag({'isRunning': False}), False)
        self.assertIs(MaaEndApi._is_running_flag({'running': True}), True)

    def test_transient_idle_and_unknown_do_not_finish(self):
        self.assertEqual(self.wait([True, False, None, True, RuntimeError('offline'), False], ['任务已提交'])[0], 'timeout')

    def test_stable_idle_after_running_finishes(self):
        result, elapsed = self.wait([True, False, False, False], ['任务已提交'])
        self.assertEqual(result, 'complete')
        self.assertGreaterEqual(elapsed, 10)

    def test_running_seen_after_log_submission_still_tracked(self):
        self.assertEqual(self.wait([None, True, False, False, False], ['任务已提交'])[0], 'complete')

    def test_unconfirmed_run_cannot_auto_close(self):
        runner = MaaEndOrchestrator({'adapters': {'maaend': {'auto_close': True}}})
        self.assertFalse(runner._auto_close_enabled())
        runner._completion_confirmed = True
        self.assertTrue(runner._auto_close_enabled())


if __name__ == '__main__':
    unittest.main()
