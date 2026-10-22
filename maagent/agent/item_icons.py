"""Named icon references imported from MAA or an explicitly requested PRTS page."""
import hashlib
import io
import shutil
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from PIL import Image
from bs4 import BeautifulSoup
from maagent.agent.storage import file_lock, read_json, write_json
from maagent.agent.budget import remaining


def add_reference(catalog, name, relative, source, **metadata):
    entry = catalog.setdefault(name, {})
    refs = entry.setdefault('references', [])
    refs[:] = [r for r in refs if r.get('icon') != relative]
    refs.append({'icon': relative, 'source': source, 'imported_at': datetime.now(timezone.utc).isoformat(), **metadata})
    entry.setdefault('icon', relative)
    if metadata.get('category'):
        entry['category'] = metadata['category']


def import_maa(resource, catalog_path):
    resource, catalog_path = Path(resource), Path(catalog_path)
    index = read_json(resource / 'item_index.json', {})
    dest = catalog_path.parent / 'item_icons' / 'maa'
    dest.mkdir(parents=True, exist_ok=True)
    imported, missing = [], []
    with file_lock(catalog_path):
        catalog = read_json(catalog_path, {})
        for item_id, item in index.items():
            filename = Path(item.get('icon', '')).name
            src = resource / 'template/items' / filename
            name = item.get('name')
            if not name or not filename or not src.is_file():
                missing.append(item_id)
                continue
            with Image.open(src) as im:
                im.verify()
            shutil.copy2(src, dest / filename)
            relative = 'item_icons/maa/' + filename
            add_reference(catalog, name, relative, str(src), item_id=item_id,
                          category={'MATERIAL': '养成材料', 'CONSUME': '消耗物品', 'NORMAL': '基础物品'}.get(item.get('classifyType')),
                          style='maa')
            imported.append(name)
        write_json(catalog_path, catalog)
    return {'imported': len(imported), 'names': len(catalog), 'missing': missing}


def import_prts(name, catalog_path, session=None):
    from urllib.parse import quote
    session = session or requests.Session()
    page = 'https://prts.wiki/w/' + quote(name, safe='')
    response = session.get(page, timeout=remaining(25))
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    candidates = [im for im in soup.select('img[src]') if im.get('alt', '').strip() == name]
    if not candidates:
        raise ValueError(f'未找到名称明确为{name}的图片，未导入')
    url = urljoin(page, candidates[0]['src'])
    host = urlparse(url).hostname or ''
    if not (host == 'prts.wiki' or host.endswith('.prts.wiki')):
        raise ValueError('图片不是PRTS资源')
    response = session.get(url, timeout=remaining(25))
    response.raise_for_status()
    im = Image.open(io.BytesIO(response.content)).convert('RGBA')
    catalog_path = Path(catalog_path)
    relative = 'item_icons/prts/' + hashlib.sha256(name.encode()).hexdigest()[:16] + '.png'
    target = catalog_path.parent / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    im.save(target)
    with file_lock(catalog_path):
        catalog = read_json(catalog_path, {})
        add_reference(catalog, name, relative, page, image_url=url, style='prts')
        write_json(catalog_path, catalog)
    return {'name': name, 'source': page, 'image_url': url}
