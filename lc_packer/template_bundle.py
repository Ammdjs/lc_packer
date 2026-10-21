"""Prepare runtime templates without distributing their sample projects."""
import json
from pathlib import Path
import zipfile

from .project import _open_archive, _validated_members
from .templates import TemplateError


def create_clean_template(source, output):
    source, output = Path(source), Path(output)
    replacements = {'assets/script.lc': b'\x0a\x14\0\0', 'assets/script.prop': b'{}',
                    'assets/script.info': b'{"ScriptVersion":"0"}',
                    'assets/config.json': json.dumps({'cloudAppId': None, 'cloudHost': None,
                        'runScriptUpdate': False, 'scriptUpdateUrl': '', 'scriptVersion': '0',
                        'stopScriptUpdate': True}, separators=(',', ':')).encode(),
                    'assets/inject/config.txt': json.dumps({'ClientTimeStamp': 0, 'ServerTimeStamp': 0,
                        'TaskId': 0, 'SaveDir': '', 'TaskScriptUrl': '',
                        'ScriptConfig': '{"TaskId":0,"SaveDir":""}', 'ScriptUip': ''},
                        separators=(',', ':')).encode()}
    for suffix in ('atc', 'uip', 'ui', 'uis', 'rtd'):
        replacements[f'assets/script.{suffix}'] = b''
    replacements['assets/out_script.lc'] = b''
    created = False
    try:
        with _open_archive(source.read_bytes(), TemplateError) as original:
            members = _validated_members(original, TemplateError, max_expanded_bytes=1024**3)
            if not {'androidmanifest.xml', 'classes.dex', 'assets/script.lc'} <= members.keys():
                raise TemplateError('内置模板缺少运行时或脚本占位文件')
            with original.open(members['assets/script.lc']) as payload:
                header = payload.read(4)
            if len(header) != 4:
                raise TemplateError('内置模板的脚本格式标记不完整')
            # A placeholder identifies its runtime family; it does not claim
            # that an unknown runtime can execute the currently supported LC.
            replacements['assets/script.lc'] = b'\x0a\x14\0\0' if header.startswith(b'\x0a\x14') else header
            replacements = {name: data for name, data in replacements.items() if name in members}
            for name in ('assets/config.json', 'assets/inject/config.txt'):
                if name not in replacements:
                    continue
                try:
                    config = json.loads(original.read(members[name]))
                except (ValueError, UnicodeError) as exc:
                    raise TemplateError('内置模板的运行配置不是有效 JSON') from exc
                if not isinstance(config, dict):
                    raise TemplateError('内置模板的运行配置必须是 JSON 对象')
                cleared = json.loads(replacements[name])
                # Only the sample project fields are reset. Runtime-specific
                # options such as auto-start and execution mode survive.
                cleared.update({key: value for key, value in config.items() if key not in cleared})
                replacements[name] = json.dumps(cleared, separators=(',', ':')).encode()
            with output.open('xb') as handle:
                created = True
                with zipfile.ZipFile(handle, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as cleaned:
                    for key, entry in members.items():
                        if entry.is_dir():
                            continue
                        if key.startswith('meta-inf/') and (Path(key).suffix in ('.sf', '.rsa', '.dsa', '.ec') or key == 'meta-inf/manifest.mf'):
                            continue
                        if key not in replacements:
                            info = zipfile.ZipInfo(entry.filename, (2026, 10, 22, 0, 0, 0))
                            info.compress_type = zipfile.ZIP_DEFLATED
                            cleaned.writestr(info, original.read(entry))
                    for name, data in replacements.items():
                        info = zipfile.ZipInfo(name, (2026, 10, 22, 0, 0, 0))
                        info.compress_type = zipfile.ZIP_DEFLATED
                        cleaned.writestr(info, data)
        with zipfile.ZipFile(output) as result:
            if result.testzip() is not None:
                raise TemplateError('内置模板完整性检查失败')
    except Exception:
        if created:
            output.unlink(missing_ok=True)
        raise
    return output
