import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PIL import Image
from maagent.agent.game import GameService, PACKAGE_BILIBILI


class IconLaunchTests(unittest.TestCase):
    def make_game(self, answer):
        game = GameService.__new__(GameService)
        game.screenshot = Mock(return_value=Image.new('RGB', (1920, 1080)))
        game._vision_ask = Mock(return_value=answer)
        game.click = Mock()
        game._wait_foreground = Mock(return_value=True)
        game._go_home = Mock()
        game._finish_open = Mock(return_value='已进入主界面')
        return game

    def test_selects_only_visual_choice_and_verifies_package(self):
        game = self.make_game('{"index":2,"reason":"粉色电视角标"}')
        items = [SimpleNamespace(text='明日方舟', center=(300, 300), box=[[250,280]]),
                 SimpleNamespace(text='明日方舟', center=(900, 300), box=[[850,280]])]
        with patch('maagent.control.popup.recognize', return_value=items):
            self.assertEqual(game._open_from_icon(PACKAGE_BILIBILI, 'B服', True), '已进入主界面')
        self.assertEqual(game.click.call_args.args[0], 900)
        game.click.assert_called_once()
        game._wait_foreground.assert_called_once_with(25, PACKAGE_BILIBILI)

    def test_uncertain_or_wrong_icon_never_tries_another(self):
        items = [SimpleNamespace(text='明日方舟', center=(300, 300), box=[[250,280]])]
        for answer in ['{"index":0}', 'bad json', '{"index":99}', '{"index":true}']:
            game = self.make_game(answer)
            with patch('maagent.control.popup.recognize', return_value=items):
                game._open_from_icon(PACKAGE_BILIBILI, 'B服', True)
            game.click.assert_not_called()
        game = self.make_game('{"index":1}')
        game._wait_foreground.return_value = False
        with patch('maagent.control.popup.recognize', return_value=items):
            self.assertIn('未确认', game._open_from_icon(PACKAGE_BILIBILI, 'B服', True))
        game.click.assert_called_once()
        game._go_home.assert_called_once()
        game._finish_open.assert_not_called()

    def test_explicit_bilibili_also_uses_icon_path(self):
        game = self.make_game('')
        game.start_emulator = Mock(return_value=True)
        game._ensure_game = Mock()
        game.foreground_package = Mock(return_value='launcher')
        game._open_from_icon = Mock(return_value='ok')
        game._start_app_pkg = Mock()
        with patch('maagent.agent.game.time.sleep'):
            self.assertEqual(game.open_arknights('B服'), 'ok')
        game._open_from_icon.assert_called_once_with(PACKAGE_BILIBILI, 'B服', True)
        game._start_app_pkg.assert_not_called()
