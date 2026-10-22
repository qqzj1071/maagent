"""Fetch named pages or bounded, resumable category batches from PRTS."""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup
from maagent.agent.budget import remaining

API = 'https://prts.wiki/api.php'
KINDS = {'operator': '干员', 'enemy': '敌人'}
PROFESSIONS = {'先锋', '近卫', '重装', '狙击', '术师', '医疗', '辅助', '特种'}


class PrtsUpdater:
    def __init__(self, knowledge, session=None):
        self.knowledge = knowledge
        self.session = session or requests.Session()

    def _api(self, **params):
        response = self.session.get(API, params={**params, 'format': 'json'}, timeout=remaining(40))
        response.raise_for_status()
        data = response.json()
        if data.get('error'):
            raise RuntimeError(data['error'].get('info', str(data['error'])))
        return data

    def fetch(self, kind, name):
        data = self._api(action='parse', page=name, prop='text|wikitext|revid|categories', redirects=1)
        parsed = data.get('parse', {})
        raw = parsed.get('wikitext', {}).get('*', '')
        categories = {c.get('*') for c in parsed.get('categories', [])}
        if KINDS[kind] not in categories:
            raise ValueError(f'页面不是{KINDS[kind]}资料，未覆盖旧数据：{name}')
        header = ''
        if kind == 'operator':
            match = re.search(r'\|\s*职业\s*=\s*([^\n|}]+)', raw)
            profession = match.group(1).strip() if match else ''
            if profession not in PROFESSIONS:
                raise ValueError(f'页面未包含有效职业：{name}')
            header = f'职业：{profession}\n'
        elif not re.search(r'\|\s*最大生命值\s*=', raw):
            raise ValueError(f'页面未包含敌人数值：{name}')
        soup = BeautifulSoup(parsed.get('text', {}).get('*', ''), 'html.parser')
        for element in soup.select('script, style, .mw-editsection, .navbox, .navigation-not-searchable, .mc-tooltips'):
            element.decompose()
        rendered = soup.get_text('\n', strip=True)
        if len(rendered) < 50 or len(raw) < 50:
            raise ValueError(f'页面内容为空或不完整：{name}')
        title = parsed.get('title', name)
        # Keep the source fields as well: rendered tables can omit level variants.
        text = header + f'名称：{title}\n==页面正文==\n{rendered}\n==原始数据字段==\n{raw}'
        return title, text, parsed.get('revid')

    def update(self, kind: str, names: list[str] | None = None, cursor: str = '') -> dict:
        if kind not in KINDS:
            raise ValueError('kind 必须为 operator 或 enemy')
        next_cursor = ''
        if names is not None:
            if not isinstance(names, list) or not 1 <= len(names) <= 10 or not all(isinstance(n, str) and n.strip() for n in names):
                raise ValueError('names 必须包含 1 至 10 个页面名称')
            if cursor:
                raise ValueError('指定名称时不能同时指定分类游标')
            names = list(dict.fromkeys(n.strip() for n in names))
        else:
            params = dict(action='query', list='categorymembers', cmtitle='Category:' + KINDS[kind], cmnamespace=0, cmlimit=10)
            if cursor:
                params['cmcontinue'] = cursor
            data = self._api(**params)
            if 'categorymembers' not in data.get('query', {}):
                raise RuntimeError('PRTS 分类列表响应不完整')
            names = [p['title'] for p in data['query']['categorymembers']]
            next_cursor = data.get('continue', {}).get('cmcontinue', '')
        updated, failed = [], []
        for index, name in enumerate(names):
            remaining()
            if index:
                time.sleep(0.3)
            try:
                title, text, revision = self.fetch(kind, name)
                source = 'https://prts.wiki/w/' + quote(title, safe='')
                count = self.knowledge.add_document(KINDS[kind] + '：' + name, text, source=source,
                    metadata={'fetched_at': datetime.now(timezone.utc).isoformat(), 'revision_id': revision,
                              'canonical_title': title, 'kind': kind})
                updated.append({'name': name, 'source': source, 'revision_id': revision, 'chunks': count})
            except TimeoutError:
                raise
            except Exception as exc:
                failed.append({'name': name, 'error': str(exc)})
        return {'kind': kind, 'updated': updated, 'failed': failed, 'cursor': next_cursor,
                'complete': not next_cursor and not failed, 'enumeration_complete': not next_cursor,
                'message': '失败页面保留旧资料；可用 names 重试。' if failed else '已保存，可立即检索。'}
