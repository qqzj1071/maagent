import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from PIL import Image
import numpy as np
from maagent.agent.game import GameService
from maagent.agent.tools import build_game_tools
from maagent.agent.item_icons import import_maa


class IconPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.game = GameService.__new__(GameService)
        self.game.catalog_file = self.root / 'catalog.json'

    def test_unknown_or_similar_name_never_opens_game(self):
        self.game.catalog_file.write_text(json.dumps({'聚合凝胶': {'icon': 'gel.png'}}))
        self.game._texts = Mock(side_effect=AssertionError('must not inspect game'))
        self.assertIn('不认识', self.game.get_item_quantity('聚合剂'))
        self.assertIsNone(self.game._resolve_item({'聚合凝胶': {}}, '聚合剂'))
        self.assertFalse(hasattr(self.game, '_traverse_find'))
        self.assertFalse(hasattr(self.game, 'get_warehouse_inventory'))
        tools = build_game_tools(self.game).names()
        self.assertNotIn('get_warehouse_inventory', tools)
        self.assertNotIn('build_item_catalog', tools)

    def test_import_preserves_multiple_named_references(self):
        resource = self.root / 'resource'
        (resource / 'template/items').mkdir(parents=True)
        data = {}
        for number in ('a', 'b'):
            Image.new('RGBA', (20, 20), 'red').save(resource / f'template/items/{number}.png')
            data[number] = {'name': '物品', 'icon': number+'.png', 'classifyType': 'MATERIAL'}
        (resource / 'item_index.json').write_text(json.dumps(data))
        result = import_maa(resource, self.game.catalog_file)
        self.assertEqual(result['imported'], 2)
        self.assertEqual(len(self.game.check_item_icon('物品')['references']), 2)
        import_maa(resource, self.game.catalog_file)
        self.assertEqual(len(self.game.check_item_icon('物品')['references']), 2)

    def test_wrong_detail_name_never_returns_quantity(self):
        icon = self.root / 'icon.png'
        Image.new('RGB', (20, 20), 'red').save(icon)
        self.game.catalog_file.write_text(json.dumps({'聚合剂': {'icon': 'icon.png'}}))
        self.game._texts = Mock(return_value=['全部', '保险库'])
        self.game.screenshot = Mock(return_value=Image.new('RGB', (200, 150), 'white'))
        self.game.find_and_click = Mock(return_value=True)
        self.game.click = Mock()
        self.game.press_key = Mock()
        self.game.ocr = Mock(return_value=[])
        self.game._read_item_panel = Mock(return_value=('聚合凝胶', '999', None))
        self.game._scan_warehouse_icon = Mock(return_value=((100,75), ''))
        with patch('cv2.matchTemplate', return_value=np.array([[0.99]], dtype=np.float32)), patch('maagent.agent.game.time.sleep'):
            result = self.game.get_item_quantity('聚合剂')
        self.assertIn('不一致', result)
        self.assertNotIn('999', result)
        self.game.click.assert_called_once()
        self.game.find_and_click.assert_called_once_with('全部', wait=0.8)

    def test_drag_search_requires_two_stationary_drags_at_each_boundary(self):
        shot = Image.new('RGB', (1280,720))
        self.game.screenshot = Mock(return_value=shot)
        self.game.swipe = Mock()
        self.game._match_item_icon = Mock(return_value=(0,None))
        # Two at left, movement rightward through list, then two at right.
        motions = [(0,0),(0,0),(0,0),(0,0),(0,0),(-400,0),
                   (0,0),(0,0),(0,0),(0,0)]
        self.game._warehouse_motion = Mock(side_effect=motions)
        with patch('maagent.agent.game.time.sleep'):
            pos, reason = self.game._scan_warehouse_icon({})
        self.assertIsNone(pos)
        self.assertEqual(reason,'complete_no_match')
        self.assertEqual(self.game.swipe.call_count,5)

    def test_unverified_overlap_stops_without_claiming_completion(self):
        self.game.screenshot = Mock(return_value=Image.new('RGB',(1280,720)))
        self.game.swipe = Mock()
        self.game._match_item_icon = Mock(return_value=(0,None))
        self.game._warehouse_motion = Mock(side_effect=[(0,0),None])
        with patch('maagent.agent.game.time.sleep'):
            pos, reason = self.game._scan_warehouse_icon({})
        self.assertIn('重叠',reason)
        self.game.swipe.assert_called_once()

    def test_feature_motion_measures_overlap_and_rejects_blank(self):
        rng = np.random.default_rng(42)
        texture = rng.integers(0,256,(720,1800,3),dtype=np.uint8)
        a = Image.fromarray(texture[:,:1280])
        b = Image.fromarray(texture[:,350:1630])
        motion = self.game._warehouse_motion(a,b)
        self.assertIsNotNone(motion)
        self.assertAlmostEqual(motion[0],-350,delta=2)
        self.assertAlmostEqual(motion[1],0,delta=2)
        self.assertEqual(self.game._warehouse_motion(a,a),(0,0))
        self.assertIsNone(self.game._warehouse_motion(Image.new('RGB',(1280,720)),b))

    def test_quantity_overlay_does_not_hide_known_icon(self):
        rng = np.random.default_rng(17)
        raw = rng.integers(30,240,(105,109,3),dtype=np.uint8)
        import cv2
        icon = self.root / 'pattern.png'
        Image.fromarray(raw).save(icon)
        scene = np.full((720,1280,3),240,dtype=np.uint8)
        resized = cv2.resize(raw,None,fx=1.2,fy=1.2)
        scene[200:200+resized.shape[0],300:300+resized.shape[1]] = resized
        scene[200+int(resized.shape[0]*.72):200+resized.shape[0],300+int(resized.shape[1]*.65):300+resized.shape[1]]=255
        info={'references':[{'path':str(icon),'style':'maa'}]}
        score, pos=self.game._match_item_icon(Image.fromarray(scene),info)
        self.assertGreaterEqual(score,.96)
        self.assertLess(abs(pos[0]-365),4)
        blank=Image.new('RGB',(1280,720),'white')
        self.assertLess(self.game._match_item_icon(blank,info)[0],.96)

    def test_only_completed_search_reports_zero(self):
        self.game.check_item_icon = Mock(return_value={'known':True,'name':'晶体电子单元'})
        self.game._texts = Mock(return_value=['全部','保险库'])
        self.game.find_and_click = Mock(return_value=True)
        self.game._scan_warehouse_icon = Mock(return_value=(None,'complete_no_match'))
        self.assertIn('当前数量：0', self.game.get_item_quantity('晶体电子单元'))
        self.game._scan_warehouse_icon.return_value=(None,'达到上限，尚未完成遍历')
        self.assertNotIn('当前数量：0', self.game.get_item_quantity('晶体电子单元'))

    def test_quantity_disagreement_is_not_reported_as_verified(self):
        from types import SimpleNamespace
        self.game.screenshot=Mock(return_value=Image.new('RGB',(1920,1080),'white'))
        self.game.click=Mock();self.game.press_key=Mock();self.game.ocr=Mock(return_value=[])
        self.game._read_item_panel=Mock(return_value=('物品','5','10/26 03:59'))
        with patch('maagent.control.popup.recognize',return_value=[SimpleNamespace(text='2')]), patch('maagent.agent.game.time.sleep'):
            with self.assertRaisesRegex(ValueError,'不一致'):
                self.game._read_target_at((500,280),'物品')
        self.game.press_key.assert_called_once_with(4)

    def test_latest_expiry_requires_complete_search(self):
        self.game.check_item_icon=Mock(return_value={'known':True,'name':'物品','references':[]})
        self.game._texts=Mock(return_value=['全部','保险库'])
        self.game.find_and_click=Mock(return_value=True)
        def scan(info,visit,**kwargs):
            visit((1,1));visit((2,2))
            return None,'complete_no_match'
        self.game._scan_warehouse_icon=scan
        self.game._read_target_at=Mock(side_effect=[
            {'quantity':2,'expiry':'10/19 03:59','verified':True},
            {'quantity':5,'expiry':'10/26 03:59','verified':True}])
        self.assertIn('10/26 03:59',self.game.get_item_latest_expiry('物品'))
        self.game._scan_warehouse_icon=Mock(return_value=(None,'达到上限'))
        self.assertIn('不能确认',self.game.get_item_latest_expiry('物品'))

    def test_user_aliases_and_ambiguous_groups(self):
        from maagent.agent.game import ITEM_ALIASES
        for alias, official in ITEM_ALIASES.items():
            self.assertEqual(self.game._resolve_item({},alias),official)
        self.game._texts=Mock(side_effect=AssertionError('ambiguous name must not operate game'))
        for alias in ['理智药','体力药','芯片']:
            self.assertIn('博士',self.game.get_item_quantity(alias))
            self.assertIn('博士',self.game.get_item_latest_expiry(alias))
        self.assertEqual(self.game.check_item_icon('理智药')['candidates'],['应急理智浓缩液','应急理智加强剂'])

    def test_earliest_group_reports_all_tied_medicines(self):
        self.game.check_item_icon=Mock(side_effect=lambda name:{'known':True,'name':name,'references':[]})
        self.game._texts=Mock(return_value=['全部','保险库'])
        self.game.find_and_click=Mock(return_value=True)
        def scan(info,visit,**kwargs):
            for n in range(3): visit((n,n))
            return None,'complete_no_match'
        self.game._scan_warehouse_icon=scan
        self.game._read_target_at=Mock(side_effect=[
            {'name':'应急理智浓缩液','quantity':2,'expiry':'10/26 03:59','verified':True},
            {'name':'应急理智加强剂','quantity':1,'expiry':'10/26 03:59','verified':False},
            {'name':'应急理智浓缩液','quantity':8,'expiry':'11/02 03:59','verified':True}])
        result=self.game.get_item_earliest_expiry('理智药')
        self.assertIn('应急理智浓缩液',result)
        self.assertIn('应急理智加强剂',result)
        self.assertNotIn('11/02',result)
        self.assertIn('仅由详情确认',result)

    def test_overlapping_pages_do_not_reopen_verified_icon(self):
        shot=Image.new('RGB',(1280,720))
        self.game.screenshot=Mock(return_value=shot)
        self.game.swipe=Mock()
        # Five pages: left boundary twice, then right movement and boundary twice.
        self.game._match_item_icon=Mock(side_effect=[value for x in (700,700,700,300,300) for value in [(1,(x,300)),(0,None)]])
        self.game._warehouse_motion=Mock(side_effect=[(0,0),(0,0),(0,0),(0,0),(0,0),(-400,0),(0,0),(0,0),(0,0),(0,0)])
        visit=Mock()
        with patch('maagent.agent.game.time.sleep'):
            _,reason=self.game._scan_warehouse_icon({},visit=visit)
        self.assertEqual(reason,'complete_no_match')
        visit.assert_called_once_with((700,300))
