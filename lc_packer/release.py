"""Build the complete release from an explicit, private-data-free file list."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import zipfile


_PRIVATE_PARTS = {".git", ".superpowers", "__pycache__", "artifacts", "build", "dist", "tools", ".venv", "history"}
_PRIVATE_SUFFIXES = {".pyc", ".apk", ".apkm", ".mqb", ".lc", ".p12", ".jks", ".keystore", ".pfx", ".key", ".pem", ".log", ".bundle", ".exe", ".zip"}


def _project_file(root, name):
    relative = PurePosixPath(name)
    if (not name or "\\" in name or ":" in name or relative.is_absolute()
            or any(part in (".", "..") or part.startswith(".") or part.casefold() in _PRIVATE_PARTS for part in relative.parts)
            or relative.name.casefold() == "local-config.json"
            or relative.name.casefold().endswith(".credentials.json")
            or relative.suffix.casefold() in _PRIVATE_SUFFIXES):
        raise ValueError(f"发布清单包含非项目文件或不安全路径：{name}")
    source = root.joinpath(*relative.parts)
    if not source.resolve().is_relative_to(root):
        raise ValueError(f"发布文件指向项目目录以外：{name}")
    if not source.is_file():
        raise FileNotFoundError(f"缺少发布文件：{name}")
    return source, relative.as_posix()


def _verified_binary(root, name, record):
    source = root / name
    if not source.resolve().is_relative_to(root) or not source.is_file():
        raise ValueError(f"发布资源缺失或指向项目目录之外：{name}")
    digest, size = record.get('sha256'), record.get('size')
    if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest) or type(size) is not int or size < 0:
        raise ValueError(f"发布资源缺少有效的大小及SHA-256：{name}")
    with source.open('rb') as stream:
        actual = hashlib.file_digest(stream, 'sha256').hexdigest()
    if source.stat().st_size != size or actual != digest:
        raise ValueError(f"发布资源大小或SHA-256不匹配：{name}")
    return source, name


def _binary_files(root, manifest):
    files = []
    assets = manifest.get('assets', [])
    if not isinstance(assets, list):
        raise ValueError('发布清单assets必须是列表')
    templates = {'lc_packer/assets/templates/root-510.apk': None}
    template_catalog = root / 'lc_packer/data/templates_catalog.json'
    if template_catalog.is_file():
        if not template_catalog.resolve().is_relative_to(root):
            raise ValueError('模板清单不能指向项目目录之外')
        data = json.loads(template_catalog.read_text(encoding='utf-8'))
        templates = {}
        for entry in data.get('templates', []):
            name = entry.get('file') if isinstance(entry, dict) else None
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_\-]*\.apk', name):
                raise ValueError('模板清单包含不安全文件名')
            path = 'lc_packer/assets/templates/' + name
            if path.casefold() in {key.casefold() for key in templates}:
                raise ValueError('模板清单包含重复文件')
            templates[path] = entry
        if not templates:
            raise ValueError('模板清单不能为空')
    for asset in assets:
        if not isinstance(asset, dict) or asset.get('path') not in templates:
            raise ValueError('只允许模板清单中经审查的内置模板，不允许加入用户APK或其他私有文件')
        pin = templates[asset['path']]
        if pin is not None and (pin.get('sha256') != asset.get('sha256') or pin.get('size') != asset.get('size')):
            raise ValueError('发布模板与内置模板清单的大小或SHA-256不一致')
        files.append(_verified_binary(root, asset['path'], asset))
    if assets and set(templates) != {asset['path'] for asset in assets}:
        raise ValueError('发布清单没有包含全部内置模板')
    if manifest.get('tool_packages'):
        catalog_path = root / 'lc_packer/data/tools_catalog.json'
        if not catalog_path.resolve().is_relative_to(root):
            raise ValueError('工具清单不能指向项目目录之外')
        catalog = json.loads(catalog_path.read_text(encoding='utf-8'))
        packages = catalog.get('packages')
        sources = catalog.get('source_archives', [])
        if not isinstance(packages, list) or not packages or not isinstance(sources, list):
            raise ValueError('缺少完整的工具安装包发布清单')
        for package in [*packages, *sources]:
            name = package.get('filename') if isinstance(package, dict) else None
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._+\-]*\.(?:zip|jar|tar\.gz)', name):
                raise ValueError(f'工具安装包文件名不安全或类型不允许：{name}')
            files.append(_verified_binary(root, 'tool-packages/' + name, package))
    return files


def create_archive(root, manifest, executable, output):
    root = Path(root).resolve()
    names = manifest.get("files")
    if not isinstance(names, list) or not names or not all(isinstance(name, str) for name in names):
        raise ValueError("发布清单 files 必须是非空文件名列表")
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError("发布清单包含重复或仅大小写不同的文件名")
    files = [_project_file(root, name) for name in names] + _binary_files(root, manifest)
    expected = ['LC打包.exe', *(name for _, name in files)]
    if len({name.casefold() for name in expected}) != len(expected):
        raise ValueError('发布文件包含重复或仅大小写不同的路径')
    executable = Path(executable)
    if not executable.is_file():
        raise FileNotFoundError("请先构建 LC打包.exe")
    output = Path(output)
    created = False
    try:
        with output.open("xb") as handle:
            created = True
            with zipfile.ZipFile(handle, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                archive.write(executable, "LC打包.exe")
                for source, name in sorted(files, key=lambda item: item[1]):
                    archive.write(source, name)
        with zipfile.ZipFile(output) as archive:
            if archive.namelist() != expected[:1] + sorted(expected[1:]) or archive.testzip() is not None:
                raise RuntimeError("发布 ZIP 文件清单或完整性校验失败")
    except Exception:
        if created:
            output.unlink(missing_ok=True)
        raise
    return output


def main(argv=None):
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description="按清单生成纯净的 LC打包完整发布 ZIP")
    parser.add_argument("--exe", type=Path, default=root / "LC打包.exe")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    manifest = json.loads((root / "packaging/release_manifest.json").read_text(encoding="utf-8"))
    output = args.output or root / f"LC打包-v{manifest['version']}.zip"
    path = create_archive(root, manifest, args.exe, output)
    with zipfile.ZipFile(path) as archive:
        count = len(archive.namelist())
    print(json.dumps({"archive": str(path), "files": count, "bytes": path.stat().st_size}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
