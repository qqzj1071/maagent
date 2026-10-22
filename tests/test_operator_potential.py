import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PIL import Image
from maagent.agent.game import GameService


class PotentialTests(unittest.TestCase):
    def setUp(self):
        self.game = GameService.__new__(GameService)
        self.game.click = Mock()
        self.game.screenshot = Mock(return_value=Image.new('RGB', (1920, 1080)))
        self.game.ocr = Mock(side_effect=AssertionError('重复截图'))

    def test_max_reuses_detail(self):
        self.assertEqual(self.game._read_potential([SimpleNamespace(text='MAX', center=(1800, 420))]), '满潜')
        self.game.click.assert_not_called()
        self.game.screenshot.assert_not_called()

    @patch('maagent.agent.game.time.sleep')
    def test_single_capture_and_cancel(self, sleep):
        with patch('maagent.control.popup.recognize', return_value=[SimpleNamespace(text='潜能')]), patch('maagent.control.popup.recognize_line', return_value=('3', .99)):
            self.assertEqual(self.game._read_potential([]), '3')
        self.game.screenshot.assert_called_once_with(save=False)
        self.assertEqual(self.game.click.call_args_list[-1].args, (1398, 975))

    @patch('maagent.agent.game.time.sleep')
    def test_uncertain_digit_is_not_a_level(self, sleep):
        with patch('maagent.control.popup.recognize', return_value=[SimpleNamespace(text='潜能')]), patch('maagent.control.popup.recognize_line', return_value=('3', .6)):
            self.assertEqual(self.game._read_potential([]), '?')

    @patch('maagent.agent.game.time.sleep')
    def test_failure_still_cancels(self, sleep):
        self.game.screenshot.side_effect = RuntimeError('capture failed')
        with self.assertRaises(RuntimeError):
            self.game._read_potential([])
        self.assertEqual(self.game.click.call_args_list[-1].args, (1398, 975))
