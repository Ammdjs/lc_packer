"""Validated metadata and resource lookup for the packaged runtime templates."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from .templates import TemplateError


DEFAULT_TEMPLATE_ID = 'root-510'
_PACKAGE_DIR = Path(__file__).resolve().parent
_CATALOG_PATH = _PACKAGE_DIR / 'data' / 'templates_catalog.json'
_TEMPLATES_DIR = _PACKAGE_DIR / 'assets' / 'templates'
_ID = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*')
_FILE = re.compile(r'[a-z0-9][a-z0-9._-]*\.apk')
_SHA256 = re.compile(r'[0-9a-f]{64}')
_ABI = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]*')


def load_templates() -> tuple[dict, ...]:
    """Read fresh catalog metadata and reject invalid or ambiguous entries."""
    try:
        catalog = json.loads(_CATALOG_PATH.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError) as exc:
        raise TemplateError('无法读取内置模板目录') from exc
    rows = catalog.get('templates') if isinstance(catalog, dict) else None
    if not isinstance(rows, list) or not rows:
        raise TemplateError('内置模板目录必须包含模板列表')
    ids, files = set(), set()
    for row in rows:
        if not isinstance(row, dict):
            raise TemplateError('内置模板目录条目无效')
        template_id, filename, digest = row.get('id'), row.get('file'), row.get('sha256')
        if not isinstance(template_id, str) or not _ID.fullmatch(template_id):
            raise TemplateError('内置模板标识无效')
        if not isinstance(filename, str) or not _FILE.fullmatch(filename):
            raise TemplateError('内置模板文件名必须是安全的 APK 文件名')
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise TemplateError('内置模板 SHA-256 无效')
        if template_id in ids or filename.casefold() in files:
            raise TemplateError('内置模板目录存在重复标识或文件名')
        if type(row.get('size')) is not int or row['size'] <= 0 or type(row.get('verified')) is not bool:
            raise TemplateError('内置模板大小或验证状态无效')
        if any(not isinstance(row.get(field), str) or not row[field].strip()
               for field in ('label', 'description', 'format')):
            raise TemplateError('内置模板说明或格式无效')
        abis = row.get('abis')
        if not isinstance(abis, list) or any(not isinstance(abi, str) or not _ABI.fullmatch(abi) for abi in abis):
            raise TemplateError('内置模板架构列表无效')
        ids.add(template_id)
        files.add(filename.casefold())
    return tuple(rows)


def template_path(template_id: str) -> Path:
    """Resolve one packaged APK and verify that its bytes match the catalog."""
    row = next((item for item in load_templates() if item['id'] == template_id), None)
    if row is None:
        raise TemplateError(f'未知内置模板：{template_id}')
    try:
        root = _TEMPLATES_DIR.resolve(strict=True)
        path = (root / row['file']).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise TemplateError('内置模板路径越界或不是文件')
        if path.stat().st_size != row['size']:
            raise TemplateError('内置模板大小与目录不匹配')
        with path.open('rb') as handle:
            digest = hashlib.file_digest(handle, 'sha256').hexdigest()
        if digest != row['sha256']:
            raise TemplateError('内置模板 SHA-256 与目录不匹配')
    except OSError as exc:
        raise TemplateError('内置模板文件缺失或无法读取') from exc
    return path
