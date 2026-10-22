"""Shared agent data, one-time legacy migration and crash-safe JSON writes."""
from __future__ import annotations

import copy
import json
import os
import shutil
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path) + '.lock', 'a+b') as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b'0')
            stream.flush()
        deadline = time.monotonic() + 15
        while True:
            stream.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f'数据文件忙碌：{path}')
                time.sleep(0.05)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def read_json(path: Path, default):
    if not path.exists():
        return copy.deepcopy(default)
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


POLICIES = {
    'default_server': '打开明日方舟默认使用 B服：启动MuMu，识别桌面带B站渠道角标的明日方舟图标并模拟点击，再点击START进入主界面复查；禁止按包名直接启动，包名仅作版本核验；无法区分则停止，只有明确指定官服才选择官服图标。',
    'operator_workflow': '查找干员并读取练度：先查知识库文档确定职业，再按职业筛选寻找干员，打开详情读取练度；缺少职业资料时先更新 PRTS 文档，不猜职业。',
    'prts_update': '博士可以指令我访问 prts.wiki 更新干员与敌人知识库；调用 update_prts_knowledge，记录来源和更新时间，失败时保留旧资料并如实报告。',
}


def prepare_agent_storage(config: dict) -> Path:
    cfg = config.setdefault('agent', {})
    root = Path(os.environ.get('MAAGENT_DATA_DIR') or cfg.get('data_dir') or
                Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) / 'Maagent').expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    project = Path(__file__).resolve().parents[2]
    bases = [project / 'config', Path.cwd() / 'config']
    if getattr(sys, 'frozen', False):
        bases += [Path(sys.executable).parent / 'config', Path(getattr(sys, '_MEIPASS', project)) / 'config']
    names = {'memory_file': 'agent_memory.json', 'knowledge_file': 'agent_knowledge.json', 'catalog_file': 'agent_item_catalog.json'}
    marker = root / 'storage_migration_v1.json'
    with file_lock(marker):
        if not marker.exists():
            sources = {}
            for key, filename in names.items():
                paths = [root / filename]
                if cfg.get(key):
                    paths.append(Path(cfg[key]).resolve())
                paths += [base / filename for base in bases]
                sources[key] = list(dict.fromkeys(p.resolve() for p in paths if p.is_file()))
            backup = root / 'backups' / ('migration-' + time.strftime('%Y%m%d-%H%M%S'))
            backup.mkdir(parents=True, exist_ok=True)
            for key, paths in sources.items():
                for i, path in enumerate(paths):
                    shutil.copy2(path, backup / f'{key}-{i}.json')
            memory = {'profile': {}, 'facts': [], 'skills': [], 'archived': []}
            for path in sources['memory_file']:
                data = read_json(path, {})
                for k, v in data.get('profile', {}).items():
                    memory['profile'].setdefault(k, v)
                for field, identity in [('facts', 'content'), ('skills', 'name'), ('archived', 'id')]:
                    seen = {x.get(identity) for x in memory[field]}
                    for item in data.get(field, []):
                        if item.get(identity) not in seen:
                            memory[field].append(item)
                            seen.add(item.get(identity))
            active = []
            for item in memory['facts']:
                content = item.get('content', '')
                if ('默认' in content and ('官服' in content or 'B服' in content)) or ('职业' in content and ('排列顺序' in content or '从上到下' in content)):
                    memory['archived'].append(dict(item, reason='2026-10-17 统一规则，旧记录仅供追溯'))
                else:
                    active.append(item)
            memory['facts'] = active
            for key, content in POLICIES.items():
                memory['facts'].append({'id': 'policy_' + key, 'category': 'rule', 'content': content,
                                        'source': 'user', 'created_at': '2026-10-17', 'pinned': True})
            write_json(root / names['memory_file'], memory)
            for key, field, identity in [('knowledge_file', 'chunks', 'doc'), ('catalog_file', None, None)]:
                merged = {} if field is None else {'chunks': []}
                seen = set()
                for path in sources[key]:
                    data = read_json(path, {})
                    if field:
                        docs = {x.get(identity) for x in data.get(field, [])}
                        merged[field].extend(x for x in data.get(field, []) if x.get(identity) not in seen)
                        seen.update(docs)
                    else:
                        for name, item in data.items():
                            if name in merged:
                                continue
                            entry = copy.deepcopy(item)
                            if isinstance(entry, dict) and entry.get('icon'):
                                src = (path.parent / entry['icon']).resolve()
                                if src.is_file():
                                    dest = root / 'item_icons' / src.name
                                    dest.parent.mkdir(exist_ok=True)
                                    if src != dest.resolve():
                                        shutil.copy2(src, dest)
                                    entry['icon'] = 'item_icons/' + src.name
                            merged[name] = entry
                write_json(root / names[key], merged)
            write_json(marker, {'completed_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'backup': str(backup),
                                'sources': {k: [str(p) for p in v] for k, v in sources.items()}})
    for key, filename in names.items():
        cfg[key] = str(root / filename)
    cfg['data_dir'] = str(root)
    cfg.setdefault('game', {})['screenshot_dir'] = str(root / 'screenshots')
    return root
