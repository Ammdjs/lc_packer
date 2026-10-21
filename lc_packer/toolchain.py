"""Verified offline installation of the application's portable tool packages.

Only catalog paths are accepted. Archives are checked before creating a staging
directory, and each finished package is published with a directory rename.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import tempfile
from typing import BinaryIO, Callable
from uuid import uuid4
import zipfile
import zlib


CATALOG_PATH = Path(__file__).resolve().parent / "data" / "tools_catalog.json"
_MARKER = ".lc-tool.json"
_CHUNK_SIZE = 1024 * 1024
_MAX_FILES = 50_000
_MAX_FILE_SIZE = 2 * 1024**3
_MAX_TOTAL_SIZE = 4 * 1024**3
_MAX_RATIO = 1000
_DIGEST = re.compile(r"[0-9a-fA-F]{64}\Z")
_RESERVED = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
_RESERVED.update(f"{prefix}{number}" for prefix in ("COM", "LPT") for number in "123456789¹²³")


class ToolInstallError(RuntimeError):
    """A tool package cannot safely be installed or used."""


class ToolInstallCancelled(ToolInstallError):
    """The caller cancelled installation before the package was committed."""


@dataclass(frozen=True)
class ToolPackage:
    id: str
    label: str
    version: str
    filename: str
    sha256: str
    size: int
    kind: str
    target: str
    prefix: str
    required: tuple[str, ...]
    settings: dict[str, str]
    source_url: str
    license_url: str


@dataclass(frozen=True)
class ToolStatus:
    id: str
    label: str
    version: str
    state: str
    detail: str
    path: str


def _parts(value: str, description: str, *, allow_empty: bool = False,
           directory: bool = False) -> tuple[str, ...]:
    if not isinstance(value, str):
        raise ToolInstallError(f"{description}格式无效，请使用随程序发布的工具清单。")
    if directory and value.endswith("/"):
        value = value[:-1]
    if not value and allow_empty:
        return ()
    if not value or len(value) > 4096 or "\\" in value:
        raise ToolInstallError(f"{description}不是安全的相对路径：{value!r}。请重新获取官方工具包。")
    parts = tuple(value.split("/"))
    for part in parts:
        if (not part or part in (".", "..") or len(part) > 255 or part[-1:] in (".", " ")
                or any(ord(char) < 32 or char in '<>:"|?*' for char in part)
                or part.split(".", 1)[0].upper() in _RESERVED):
            raise ToolInstallError(f"{description}含有 Windows 不安全路径：{value!r}。请重新获取官方工具包。")
    return parts


def _validate_package(package: ToolPackage) -> None:
    if not isinstance(package, ToolPackage):
        raise ToolInstallError("工具清单项格式无效，请重新获取完整程序。")
    for field in ("id", "label", "version", "source_url", "license_url"):
        value = getattr(package, field)
        if not isinstance(value, str) or not value.strip():
            raise ToolInstallError(f"工具清单缺少 {field}，请重新获取完整程序。")
    for field in ("target", "filename"):
        if len(_parts(getattr(package, field), f"工具清单 {field}")) != 1:
            raise ToolInstallError(f"工具清单 {field} 必须是单个名称，请重新获取完整程序。")
    _parts(package.prefix, "工具包前缀", allow_empty=True, directory=True)
    if package.kind not in ("zip", "jar"):
        raise ToolInstallError(f"{package.label} 的包类型无效，仅支持 ZIP/JAR。")
    if not isinstance(package.size, int) or isinstance(package.size, bool) or package.size <= 0:
        raise ToolInstallError(f"{package.label} 的安装包大小无效，请重新获取完整清单。")
    if not isinstance(package.sha256, str) or not _DIGEST.fullmatch(package.sha256):
        raise ToolInstallError(f"{package.label} 的 SHA256 校验值无效，请重新获取完整清单。")
    if not isinstance(package.required, tuple) or not package.required:
        raise ToolInstallError(f"{package.label} 未指定必需文件，请重新获取完整清单。")
    required = set()
    for relative in package.required:
        parts = _parts(relative, "必需文件")
        key = "/".join(parts).casefold()
        if key == _MARKER or key in required:
            raise ToolInstallError(f"{package.label} 的必需文件重复或保留名称冲突。")
        required.add(key)
    if not isinstance(package.settings, dict) or not package.settings:
        raise ToolInstallError(f"{package.label} 未指定工具设置路径，请重新获取完整清单。")
    for setting, relative in package.settings.items():
        if not isinstance(setting, str) or not setting:
            raise ToolInstallError(f"{package.label} 的工具设置名称无效。")
        _parts(relative, "工具设置路径")
        if relative not in package.required:
            raise ToolInstallError(f"{package.label} 的工具设置路径不在必需文件中。")
    if package.kind == "jar" and (package.prefix or len(set(package.settings.values())) != 1):
        raise ToolInstallError(f"{package.label} 的 JAR 必须指定一个目标文件且不带解压前缀。")


def _packages(packages) -> tuple[ToolPackage, ...]:
    result = load_catalog() if packages is None else tuple(packages)
    identities, targets, settings = set(), set(), set()
    for package in result:
        _validate_package(package)
        if package.id in identities or package.target.casefold() in targets:
            raise ToolInstallError("工具清单有重复的包或安装目录，请重新获取完整程序。")
        if settings.intersection(package.settings):
            raise ToolInstallError("工具清单有重复的设置名称，请重新获取完整程序。")
        identities.add(package.id)
        targets.add(package.target.casefold())
        settings.update(package.settings)
    return result


def load_catalog() -> tuple[ToolPackage, ...]:
    """Read the bundled, pinned package catalog without downloading anything."""
    try:
        raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8-sig"))
        # Release provenance/source metadata is informational; package entries
        # still use the exact dataclass fields and restricted paths below.
        if not isinstance(raw, dict) or not isinstance(raw.get("packages"), list):
            raise ToolInstallError("工具清单格式无效，请重新获取完整程序。")
        packages = []
        for entry in raw["packages"]:
            if not isinstance(entry, dict) or not isinstance(entry.get("required"), list):
                raise ToolInstallError("工具清单的必需文件列表格式无效。")
            fields = dict(entry)
            fields["required"] = tuple(fields["required"])
            packages.append(ToolPackage(**fields))
        return _packages(tuple(packages))
    except ToolInstallError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise ToolInstallError(f"无法读取工具清单 {CATALOG_PATH}，请重新获取完整程序：{exc}") from exc


def _cancel(cancel_event) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ToolInstallCancelled("工具安装已取消；当前工具未提交，可再次点击安装重试。")


def _emit(log: Callable[[str], None] | None, message: str) -> None:
    if log is not None:
        log(message)


def _root(root) -> Path:
    return Path(root).resolve()


def _reject_reparse(path: Path) -> os.stat_result:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ToolInstallError(f"路径包含链接或重解析点：{path}。请改用程序目录内的普通文件夹。")
    return info


def _plain_chain(root: Path, path: Path) -> None:
    try:
        parts = path.relative_to(root).parts
    except ValueError as exc:
        raise ToolInstallError(f"工具路径越过程序目录：{path}。请检查工具清单。") from exc
    current = root
    for part in parts:
        current /= part
        if current.exists() or current.is_symlink():
            _reject_reparse(current)


def _hash_stream(stream: BinaryIO, cancel_event=None) -> str:
    digest = hashlib.sha256()
    while True:
        _cancel(cancel_event)
        chunk = stream.read(_CHUNK_SIZE)
        if not chunk:
            return digest.hexdigest()
        digest.update(chunk)


def _hash_file(path: Path, cancel_event=None) -> str:
    with path.open("rb") as stream:
        return _hash_stream(stream, cancel_event)


def _read_marker(target: Path, package: ToolPackage) -> dict | None:
    marker_path = target / _MARKER
    if not marker_path.exists():
        return None
    _reject_reparse(marker_path)
    try:
        if marker_path.stat().st_size > 4 * 1024 * 1024:
            return None
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
        if (not isinstance(marker, dict) or marker.get("schema") != 1
                or marker.get("id") != package.id or marker.get("target") != package.target
                or not isinstance(marker.get("version"), str)
                or not isinstance(marker.get("sha256"), str)
                or not _DIGEST.fullmatch(marker["sha256"])
                or not isinstance(marker.get("files"), list) or len(marker["files"]) > _MAX_FILES
                or not isinstance(marker.get("required_hashes"), dict)):
            return None
        names = set()
        for relative in marker["files"]:
            _parts(relative, "已安装工具清单路径")
            key = relative.casefold()
            if key == _MARKER or key in names:
                return None
            names.add(key)
        if not marker["files"] or not marker["required_hashes"]:
            return None
        for relative, digest in marker["required_hashes"].items():
            _parts(relative, "已安装必需文件路径")
            if relative not in marker["files"] or not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
                return None
        records = marker.get("file_records")
        # Older markers still establish ownership, but must be repaired before
        # they can report a healthy installation with the complete manifest.
        if records is not None:
            if not isinstance(records, dict) or set(records) != set(marker["files"]):
                return None
            for record in records.values():
                if (not isinstance(record, dict)
                        or not isinstance(record.get("size"), int)
                        or isinstance(record["size"], bool) or record["size"] < 0
                        or not isinstance(record.get("sha256"), str)
                        or not _DIGEST.fullmatch(record["sha256"])):
                    return None
        return marker
    except (OSError, ValueError, TypeError, ToolInstallError):
        return None


def _status(root: Path, package: ToolPackage) -> ToolStatus:
    target = root / "tools" / package.target

    def status(state: str, detail: str) -> ToolStatus:
        return ToolStatus(package.id, package.label, package.version, state, detail, str(target))

    _plain_chain(root, target)
    if target.exists():
        if not target.is_dir():
            return status("error", f"安装位置已有普通文件：{target}。请手动移开后重试。")
        marker = _read_marker(target, package)
        if marker is None:
            return status("error", f"安装位置已有非本程序管理的目录：{target}。请手动移开或更换程序目录，避免覆盖用户文件。")
        if marker["version"] != package.version or marker["sha256"].lower() != package.sha256.lower():
            return status("broken", "已安装版本或校验记录与随包清单不符，请重新安装修复。")
        records = marker.get("file_records")
        if records is None:
            return status("broken", "已安装工具缺少完整文件校验记录，请点击安装修复。")
        for relative in package.required:
            if relative not in records or records[relative]["size"] == 0:
                return status("broken", f"必需文件缺少或为空：{relative}。请点击安装修复。")
        if package.kind == "jar":
            record = records[next(iter(package.settings.values()))]
            if record["size"] != package.size or record["sha256"].lower() != package.sha256.lower():
                return status("broken", "已安装 JAR 校验记录与随包清单不符，请点击安装修复。")
        for relative, record in records.items():
            file = target.joinpath(*_parts(relative, "托管文件"))
            _plain_chain(root, file)
            if (not file.is_file() or file.stat().st_size != record["size"]
                    or record["sha256"].lower() != _hash_file(file)):
                return status("broken", f"托管文件缺少或已损坏：{file}。请点击安装修复。")
        return status("installed", f"已安装 {package.version}：{target}")
    archive = root / "tool-packages" / package.filename
    _plain_chain(root, archive)
    if not archive.is_file():
        return status("package_missing", f"缺少离线安装包：{archive}。请重新解压完整发行包后重试。")
    return status("missing", f"尚未安装，离线包已就绪；将安装到 {target}")


def package_status(root, package: ToolPackage) -> ToolStatus:
    """Verify every managed file while ignoring additional user-owned files."""
    try:
        _validate_package(package)
        return _status(_root(root), package)
    except (OSError, ValueError, TypeError, ToolInstallError) as exc:
        return ToolStatus(package.id, package.label, package.version, "error",
                          f"无法检查 {package.label}：{exc}。请检查目录权限及工具清单。",
                          str(Path(root) / "tools" / package.target))


def installed_settings(root, packages=None) -> dict[str, str]:
    """Return settings only for healthy, installer-managed portable tools."""
    result = {}
    root = _root(root)
    for package in _packages(packages):
        if package_status(root, package).state == "installed":
            result.update({key: str(root / "tools" / package.target / relative)
                           for key, relative in package.settings.items()})
    return result


def _check_link_extra(info: zipfile.ZipInfo) -> None:
    extra, offset = info.extra, 0
    while offset < len(extra):
        if len(extra) - offset < 4:
            raise ToolInstallError(f"ZIP 附加数据损坏：{info.filename}。请重新获取官方安装包。")
        kind, length = struct.unpack_from("<HH", extra, offset)
        offset += 4
        data = extra[offset:offset + length]
        if len(data) != length:
            raise ToolInstallError(f"ZIP 附加数据截断：{info.filename}。请重新获取官方安装包。")
        # PKWARE Unix may append a link target; ASi Unix stores link mode/target.
        if ((kind == 0x000D and length > 12)
                or (kind == 0x756E and (length < 14 or length > 14
                                      or stat.S_IFMT(struct.unpack_from("<H", data, 4)[0])
                                      not in (0, stat.S_IFREG, stat.S_IFDIR)))):
            raise ToolInstallError(f"ZIP 包含链接条目：{info.filename}。请使用官方普通文件安装包。")
        offset += length


def _zip_members(archive: zipfile.ZipFile, package: ToolPackage):
    infos = archive.infolist()
    if len(infos) > _MAX_FILES:
        raise ToolInstallError(f"{package.label} 的 ZIP 文件数量超过安全限制，请重新获取官方安装包。")
    prefix = _parts(package.prefix, "工具包前缀", allow_empty=True, directory=True)
    paths, explicit, original_names, members, total = {}, set(), set(), [], 0
    for info in infos:
        if "\x00" in info.orig_filename:
            raise ToolInstallError("ZIP 文件名包含空字符，请重新获取官方安装包。")
        is_directory = info.is_dir()
        original = _parts(info.filename, "ZIP 文件名", directory=is_directory)
        original_key = "/".join(original).casefold()
        if original_key in original_names:
            raise ToolInstallError(f"ZIP 存在重复或大小写冲突条目：{info.filename}。请重新获取官方安装包。")
        original_names.add(original_key)
        if info.flag_bits & 1:
            raise ToolInstallError(f"ZIP 包含加密文件：{info.filename}。请使用官方未加密安装包。")
        mode = stat.S_IFMT(info.external_attr >> 16)
        if mode not in (0, stat.S_IFREG, stat.S_IFDIR) or info.external_attr & 0x400:
            raise ToolInstallError(f"ZIP 包含链接、重解析点或特殊文件：{info.filename}。请重新获取官方安装包。")
        if (mode == stat.S_IFDIR or info.external_attr & 0x10) and not is_directory:
            raise ToolInstallError(f"ZIP 文件与目录属性冲突：{info.filename}。请重新获取官方安装包。")
        _check_link_extra(info)
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise ToolInstallError(f"ZIP 压缩方式不受支持：{info.filename}。请重新获取官方安装包。")
        total += info.file_size
        if (info.file_size > _MAX_FILE_SIZE or total > _MAX_TOTAL_SIZE
                or (info.file_size > _CHUNK_SIZE and info.file_size > max(1, info.compress_size) * _MAX_RATIO)):
            raise ToolInstallError(f"ZIP 解压大小或压缩比超过安全限制：{info.filename}。请重新获取官方安装包。")
        if prefix and original[:len(prefix)] != prefix:
            if is_directory and prefix[:len(original)] == original:
                continue
            raise ToolInstallError(f"ZIP 目录与清单前缀 {package.prefix!r} 不符：{info.filename}。请重新获取完整发行包。")
        parts = original[len(prefix):]
        if not parts:
            if is_directory:
                continue
            raise ToolInstallError(f"ZIP 前缀指向文件：{info.filename}。请检查工具清单。")
        relative = "/".join(parts)
        key = relative.casefold()
        if key == _MARKER:
            raise ToolInstallError("ZIP 包含安装器保留清单名称，请重新获取官方安装包。")
        if key in explicit:
            raise ToolInstallError(f"ZIP 存在重复或大小写冲突文件：{relative}。请重新获取官方安装包。")
        explicit.add(key)
        for index in range(1, len(parts) + 1):
            path = "/".join(parts[:index])
            key = path.casefold()
            directory = index < len(parts) or is_directory
            previous = paths.get(key)
            if previous is not None and (previous[0] != path or not previous[1] or not directory):
                raise ToolInstallError(f"ZIP 存在大小写或文件目录冲突：{relative}。请重新获取官方安装包。")
            paths[key] = (path, directory)
        members.append((info, relative, is_directory))
    return members


def _copy_stream(source: BinaryIO, destination: Path, *, cancel_event=None,
                 limit: int) -> None:
    count = 0
    with destination.open("xb") as output:
        while True:
            _cancel(cancel_event)
            chunk = source.read(_CHUNK_SIZE)
            if not chunk:
                break
            count += len(chunk)
            if count > limit:
                raise ToolInstallError(f"解压文件超出声明大小：{destination.name}。请重新获取官方安装包。")
            output.write(chunk)
    if count != limit:
        raise ToolInstallError(f"工具文件数据不完整：{destination.name}。请重新获取完整安装包。")


def _tree(target: Path):
    """Walk without following links, including otherwise harmless empty dirs."""
    for parent, directories, files in os.walk(target, followlinks=False):
        for name in sorted(directories + files):
            path = Path(parent) / name
            info = _reject_reparse(path)
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                raise ToolInstallError(f"安装目录包含特殊文件：{path}。请手动移开后重试。")
            yield path, stat.S_ISDIR(info.st_mode)


def _preserve_unmanaged(target: Path, stage: Path, marker: dict, cancel_event) -> None:
    managed = {name.casefold() for name in marker["files"]} | {_MARKER}
    # Validate the whole old tree before copying any file or renaming the target.
    entries = list(_tree(target))
    for path, directory in entries:
        _cancel(cancel_event)
        relative = path.relative_to(target)
        if not directory and relative.as_posix().casefold() in managed:
            continue
        destination = stage / relative
        if directory:
            if destination.exists() and not destination.is_dir():
                raise ToolInstallError(f"新工具文件与用户目录冲突：{path}。请手动移开后重试。")
            destination.mkdir(parents=True, exist_ok=True)
        else:
            if destination.exists():
                raise ToolInstallError(f"新工具文件与用户文件冲突：{path}。请手动移开后重试。")
            destination.parent.mkdir(parents=True, exist_ok=True)
            with path.open("rb") as source:
                _copy_stream(source, destination, cancel_event=cancel_event, limit=path.stat().st_size)
            shutil.copystat(path, destination, follow_symlinks=False)


def _remove_owned_temporary(path: Path, tools: Path) -> None:
    # Only staging directories created by this invocation may be deleted.
    if not path.exists():
        return
    if (path.parent != tools or not path.name.startswith(".lc-stage-")
            or path.resolve().parent != tools.resolve()):
        raise ToolInstallError(f"暂存目录位置异常，已保留文件供检查：{path}")
    _reject_reparse(path)
    list(_tree(path))
    shutil.rmtree(path)


def _commit(stage: Path, target: Path, package: ToolPackage, tools: Path) -> Path | None:
    backup = None
    if target.exists():
        _plain_chain(tools, target)
        if _read_marker(target, package) is None:
            raise ToolInstallError(f"安装目录在安装期间发生变化，已停止覆盖：{target}。请检查后重试。")
        backup = tools / f".lc-backup-{uuid4().hex}"
        target.rename(backup)
    try:
        stage.rename(target)
    except OSError:
        if backup is not None:
            backup.rename(target)
        raise
    # Keep the complete old tree: users may have written to it after the
    # preservation snapshot. It is never owned temporary data to delete.
    return backup


def _install_one(root: Path, package: ToolPackage, log, cancel_event) -> None:
    _cancel(cancel_event)
    status = _status(root, package)
    if status.state == "installed":
        _emit(log, f"{package.label} 已安装且校验通过，跳过安装。")
        return
    if status.state in ("error", "package_missing"):
        raise ToolInstallError(status.detail)
    target = root / "tools" / package.target
    old_marker = _read_marker(target, package) if target.exists() else None
    archive_path = root / "tool-packages" / package.filename
    _plain_chain(root, archive_path)
    _emit(log, f"正在校验 {package.label} 离线包大小和 SHA256：{archive_path}")
    with archive_path.open("rb") as source:
        if os.fstat(source.fileno()).st_size != package.size:
            raise ToolInstallError(f"{package.label} 安装包大小校验失败，请重新解压完整发行包：{archive_path}")
        if _hash_stream(source, cancel_event).lower() != package.sha256.lower():
            raise ToolInstallError(f"{package.label} 安装包 SHA256 校验失败，请重新获取官方安装包：{archive_path}")
        source.seek(0)
        tools = root / "tools"
        _plain_chain(root, tools)
        tools.mkdir(exist_ok=True)
        # A shared source handle does not prevent another process from writing
        # to the original file. Only consume this private, verified snapshot.
        with tempfile.TemporaryFile(mode="w+b", prefix=".lc-source-", dir=tools) as verified:
            digest, count = hashlib.sha256(), 0
            while True:
                _cancel(cancel_event)
                chunk = source.read(_CHUNK_SIZE)
                if not chunk:
                    break
                count += len(chunk)
                if count > package.size:
                    raise ToolInstallError(f"{package.label} 安装包大小校验失败：{archive_path}")
                verified.write(chunk)
                digest.update(chunk)
            if count != package.size or digest.hexdigest().lower() != package.sha256.lower():
                raise ToolInstallError(f"{package.label} 安装包快照大小或 SHA256 校验失败，请重新获取官方安装包：{archive_path}")
            verified.seek(0)
            archive = zipfile.ZipFile(verified) if package.kind == "zip" else None
            try:
                members = _zip_members(archive, package) if archive is not None else None
                _cancel(cancel_event)
                stage = Path(tempfile.mkdtemp(prefix=".lc-stage-", dir=tools))
                try:
                    owned_files = []
                    _emit(log, f"正在{'解压' if archive is not None else '复制'} {package.label} 到程序目录：{target}")
                    if archive is not None:
                        for info, relative, directory in members:
                            _cancel(cancel_event)
                            destination = stage.joinpath(*relative.split("/"))
                            if directory:
                                destination.mkdir(parents=True, exist_ok=True)
                            else:
                                destination.parent.mkdir(parents=True, exist_ok=True)
                                with archive.open(info) as contents:
                                    _copy_stream(contents, destination, cancel_event=cancel_event, limit=info.file_size)
                                owned_files.append(relative)
                    else:
                        relative = next(iter(package.settings.values()))
                        destination = stage.joinpath(*relative.split("/"))
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        _copy_stream(verified, destination, cancel_event=cancel_event, limit=package.size)
                        owned_files.append(relative)
                    file_records = {}
                    for relative in owned_files:
                        path = stage.joinpath(*relative.split("/"))
                        file_records[relative] = {"size": path.stat().st_size,
                                                  "sha256": _hash_file(path, cancel_event)}
                    required_hashes = {}
                    for relative in package.required:
                        if relative not in file_records or file_records[relative]["size"] == 0:
                            raise ToolInstallError(f"{package.label} 安装包缺少必需文件：{relative}。请重新获取完整官方安装包。")
                        required_hashes[relative] = file_records[relative]["sha256"]
                    if package.kind == "jar":
                        record = file_records[next(iter(package.settings.values()))]
                        if record["size"] != package.size or record["sha256"].lower() != package.sha256.lower():
                            raise ToolInstallError(f"{package.label} 安装目标 SHA256 校验失败，请重新获取官方安装包。")
                    if old_marker is not None:
                        _preserve_unmanaged(target, stage, old_marker, cancel_event)
                    marker = {"schema": 1, "id": package.id, "version": package.version,
                              "sha256": package.sha256, "target": package.target,
                              "files": sorted(owned_files), "required_hashes": required_hashes,
                              "file_records": file_records}
                    (stage / _MARKER).write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
                    _cancel(cancel_event)
                    backup = _commit(stage, target, package, tools)
                    if backup is not None:
                        _emit(log, f"旧工具目录已完整保留，包含修复期间的用户文件：{backup}")
                    _emit(log, f"{package.label} {package.version} 安装完成：{target}")
                finally:
                    _remove_owned_temporary(stage, tools)
            finally:
                if archive is not None:
                    archive.close()


def install_packages(root, *, packages=None, log=None, cancel_event=None) -> dict[str, str]:
    """Verify and install packages offline, retaining earlier successful commits.

    Cancellation is checked during hashing/copying and immediately before each
    package commit. No environment variable, registry entry or service is changed.
    """
    try:
        packages = _packages(packages)
        root = _root(root)
        _cancel(cancel_event)
        for package in packages:
            _install_one(root, package, log, cancel_event)
        return installed_settings(root, packages)
    except ToolInstallError:
        raise
    except (OSError, ValueError, TypeError, RuntimeError, zipfile.BadZipFile, zlib.error, EOFError) as exc:
        raise ToolInstallError(f"工具安装失败：{exc}。请检查目录写入权限、磁盘空间和离线包完整性后重试。") from exc
