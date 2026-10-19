"""Build the complete release from an explicit, private-data-free file list."""
import argparse
import json
from pathlib import Path, PurePosixPath
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


def create_archive(root, manifest, executable, output):
    root = Path(root).resolve()
    names = manifest.get("files")
    if not isinstance(names, list) or not names or not all(isinstance(name, str) for name in names):
        raise ValueError("发布清单 files 必须是非空文件名列表")
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError("发布清单包含重复或仅大小写不同的文件名")
    files = [_project_file(root, name) for name in names]
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
            if set(archive.namelist()) != {"LC打包.exe", *names} or archive.testzip() is not None:
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
    print(json.dumps({"archive": str(path), "files": len(manifest["files"]) + 1, "bytes": path.stat().st_size}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
