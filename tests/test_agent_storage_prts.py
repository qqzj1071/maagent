"""Offline verification; no model calls, credentials or emulator needed."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from maagent.agent.storage import prepare_agent_storage, write_json
from maagent.agent.memory import AgentMemory
from maagent.agent.knowledge import KnowledgeBase
from maagent.agent.prts import PrtsUpdater
from maagent.agent.tools import build_knowledge_tools
from maagent.agent.game import GameService


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_shared_paths_migration_archive_icons_and_idempotence(self):
        legacy = self.root / 'legacy'
        shared = self.root / 'shared'
        write_json(legacy / 'memory.json', {'facts': [{'id': 'old', 'content': '默认打开官服'},
                                                    {'id': 'keep', 'content': '自定义偏好'}]})
        (legacy / 'icon.png').write_bytes(b'icon')
        write_json(legacy / 'catalog.json', {'物品': {'icon': 'icon.png'}})
        cfg = {'agent': {'memory_file': str(legacy / 'memory.json'), 'catalog_file': str(legacy / 'catalog.json')}}
        with patch.dict(os.environ, {'MAAGENT_DATA_DIR': str(shared)}):
            prepare_agent_storage(cfg)
            memory = AgentMemory(cfg['agent']['memory_file'])
            self.assertTrue(any(x['id'] == 'old' for x in memory.data['archived']))
            self.assertFalse(any(x['id'] == 'old' for x in memory.data['facts']))
            self.assertTrue(any(x['id'] == 'keep' for x in memory.data['facts']))
            self.assertEqual((shared / 'item_icons/icon.png').read_bytes(), b'icon')
            self.assertTrue(list((shared / 'backups').rglob('*.json')))
            memory.remove('keep')
            other = {}
            prepare_agent_storage(other)
            self.assertEqual(cfg['agent']['memory_file'], other['agent']['memory_file'])
            self.assertNotIn('自定义偏好', AgentMemory(other['agent']['memory_file']).pretty())

    def test_stale_instances_keep_each_others_updates_and_rules(self):
        path = self.root / 'memory.json'
        a, b = AgentMemory(path), AgentMemory(path)
        a.add_fact('默认 B服', 'rule')
        b.add_fact('偏好')
        for i in range(20):
            a.add_fact(f'知识{i}')
        self.assertIn('默认 B服', b.digest())
        self.assertIn('偏好', b.pretty())
        a.add_fact('偏好')
        self.assertEqual(sum(x['content'] == '偏好' for x in a.all_items()), 1)
        path.write_text('{broken', encoding='utf-8')
        with self.assertRaises(RuntimeError):
            b.add_fact('不得覆盖')
        self.assertEqual(path.read_text(), '{broken')

    def test_prts_update_metadata_replacement_and_failed_preservation(self):
        kb = KnowledgeBase(self.root / 'kb.json')
        kb.add_document('干员：芬', '职业：先锋\n旧数据')
        updater = PrtsUpdater(kb)
        with patch.object(updater, 'fetch', return_value=('芬', '职业：先锋\n新数据', 123)):
            result = updater.update('operator', ['芬'])
        self.assertTrue(result['complete'])
        reloaded = KnowledgeBase(kb.path)
        self.assertNotIn('旧数据', str(reloaded.chunks))
        self.assertEqual(reloaded.chunks[0]['revision_id'], 123)
        self.assertTrue(reloaded.chunks[0]['fetched_at'])
        before = kb.path.read_bytes()
        with patch.object(updater, 'fetch', side_effect=RuntimeError('offline')):
            result = updater.update('operator', ['芬'])
        self.assertFalse(result['complete'])
        self.assertEqual(kb.path.read_bytes(), before)

    def test_category_cursor_and_tools_on_empty_library(self):
        kb = KnowledgeBase(self.root / 'kb.json')
        self.assertIn('update_prts_knowledge', build_knowledge_tools(kb).names())
        updater = PrtsUpdater(kb)
        with patch.object(updater, '_api', return_value={'query': {'categorymembers': [{'title': '源石虫'}]}, 'continue': {'cmcontinue': 'next'}}), patch.object(updater, 'fetch', return_value=('源石虫', '生命值：550', 1)):
            result = updater.update('enemy')
        self.assertFalse(result['complete'])
        self.assertEqual(result['cursor'], 'next')
        self.assertIn('敌人：源石虫', kb.docs())

    def test_page_kind_and_profession_validation(self):
        updater = PrtsUpdater(KnowledgeBase(self.root / 'kb.json'))
        payload = {'parse': {'title': '芬', 'categories': [{'*': '干员'}], 'revid': 10,
                  'wikitext': {'*': '|职业=先锋\n' + '|攻击力=100\n' * 10},
                  'text': {'*': '<p>' + '有效属性资料' * 20 + '</p>'}}}
        with patch.object(updater, '_api', return_value=payload):
            self.assertIn('职业：先锋', updater.fetch('operator', '芬')[1])
            with self.assertRaises(ValueError):
                updater.fetch('enemy', '芬')

    def test_profession_required_before_game_and_default_bilibili(self):
        game = GameService.__new__(GameService)
        game.knowledge = KnowledgeBase(self.root / 'kb.json')
        game.start_emulator = Mock()
        self.assertIn('缺少', game.open_operator('芬'))
        game.start_emulator.assert_not_called()
        game.knowledge.add_document('干员：芬', '其他章节\n' + 'x' * 1000 + '\n职业：先锋')
        self.assertEqual(game.lookup_profession('芬'), '先锋')
        self.assertEqual(game._resolve_package(None)[1], 'B服')
        self.assertEqual(game._resolve_package('官服')[1], '官服')
        game._ensure_game = Mock()
        game.focus_before_capture = False
        game.goto_operator_list = Mock(return_value=True)
        game.filter_by_profession = Mock(return_value=False)
        game.swipe = Mock()
        self.assertIn('筛选成功', game.open_operator('芬'))
        game.filter_by_profession.assert_called_once_with('先锋')
        game.swipe.assert_not_called()


if __name__ == '__main__':
    unittest.main()
