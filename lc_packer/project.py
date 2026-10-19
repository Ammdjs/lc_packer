"""Read MQB resources without extracting or executing untrusted contents."""

from __future__ import annotations

from dataclasses import dataclass
import io
from pathlib import Path, PurePosixPath
import re
import struct
import zipfile


MAX_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_ENTRIES = 4096
_IMPORT = re.compile(r"^[ \t]*Import[ \t]+([\"'])(.*?)\1", re.IGNORECASE | re.MULTILINE)
_RESOURCES = {".prop": "properties", ".uip": "ui", ".atc": "attachments"}


class ProjectError(ValueError):
    """An MQB cannot be read safely or does not contain one valid project."""


@dataclass(frozen=True, slots=True)
class MqbProject:
    source_name: str
    source: bytes
    properties: bytes | None
    ui: bytes | None
    attachments: bytes | None
    dependencies: tuple[str, ...]


def _repair_legacy_filename_flags(
    data: bytes, error_type: type[ValueError], *, force_gb18030: bool = False
) -> bytes:
    """Clear false UTF-8 flags; explicit mode also resolves ambiguous names."""
    repaired = bytearray(data)
    end = data.rfind(b"PK\x05\x06")
    if end < 0 or end + 22 > len(data):
        raise error_type("Invalid ZIP directory")
    try:
        entry_count = struct.unpack_from("<H", data, end + 10)[0]
        directory_size = struct.unpack_from("<I", data, end + 12)[0]
        cursor = struct.unpack_from("<I", data, end + 16)[0]
        if entry_count == 0xFFFF or cursor == 0xFFFFFFFF or cursor + directory_size > end:
            raise error_type("Unsupported legacy ZIP directory")
        for _ in range(entry_count):
            if data[cursor:cursor + 4] != b"PK\x01\x02" or cursor + 46 > end:
                raise error_type("Invalid ZIP directory entry")
            flags = struct.unpack_from("<H", data, cursor + 8)[0]
            name_length, extra_length, comment_length = struct.unpack_from("<HHH", data, cursor + 28)
            name_start = cursor + 46
            next_cursor = name_start + name_length + extra_length + comment_length
            if next_cursor > end:
                raise error_type("Invalid ZIP directory entry")
            raw_name = data[name_start:name_start + name_length]
            if flags & 0x800:
                if not force_gb18030:
                    try:
                        raw_name.decode("utf-8")
                    except UnicodeDecodeError:
                        pass
                    else:
                        cursor = next_cursor
                        continue
                try:
                    raw_name.decode("gb18030")
                except UnicodeDecodeError as exc:
                    raise error_type("ZIP member name cannot be decoded as GB18030") from exc
                local_offset = struct.unpack_from("<I", data, cursor + 42)[0]
                if data[local_offset:local_offset + 4] != b"PK\x03\x04":
                    raise error_type("Invalid ZIP local header")
                local_flags = struct.unpack_from("<H", data, local_offset + 6)[0]
                struct.pack_into("<H", repaired, cursor + 8, flags & ~0x800)
                struct.pack_into("<H", repaired, local_offset + 6, local_flags & ~0x800)
            cursor = next_cursor
    except (IndexError, struct.error) as exc:
        raise error_type("Invalid ZIP directory") from exc
    return bytes(repaired)


def _open_archive(
    data: bytes, error_type: type[ValueError], *, filename_encoding: str | None = None
) -> zipfile.ZipFile:
    if filename_encoding not in (None, "gb18030"):
        raise error_type("Unsupported ZIP filename encoding")
    if filename_encoding == "gb18030":
        data = _repair_legacy_filename_flags(data, error_type, force_gb18030=True)
    try:
        # ZipFile still honors UTF-8 flags; this fallback applies to unflagged names.
        return zipfile.ZipFile(io.BytesIO(data), metadata_encoding="gb18030")
    except UnicodeDecodeError:
        if filename_encoding is not None:
            raise error_type("ZIP member name cannot be decoded as GB18030") from None
        repaired = _repair_legacy_filename_flags(data, error_type)
        try:
            return zipfile.ZipFile(io.BytesIO(repaired), metadata_encoding="gb18030")
        except (OSError, ValueError, zipfile.BadZipFile, UnicodeDecodeError) as exc:
            raise error_type("Invalid ZIP archive") from exc
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        raise error_type("Invalid ZIP archive") from exc


def _validated_members(
    archive: zipfile.ZipFile,
    error_type: type[ValueError],
    *,
    max_expanded_bytes: int | None,
) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > MAX_ENTRIES:
        raise error_type("Archive contains too many entries")
    members: dict[str, zipfile.ZipInfo] = {}
    expanded = 0
    for info in infos:
        name = info.filename
        path = name[:-1] if info.is_dir() else name
        if (
            not path
            or name.startswith(("/", "\\"))
            or "\\" in name
            or ":" in name
            or "\x00" in name
            or any(part in ("", ".", "..") for part in path.split("/"))
        ):
            raise error_type("Unsafe archive member path")
        key = path.casefold()
        if key in members:
            raise error_type("Duplicate archive member name")
        members[key] = info
        if info.flag_bits & 1:
            raise error_type("Encrypted archive member is unsupported")
        expanded += info.file_size
        if max_expanded_bytes is not None and expanded > max_expanded_bytes:
            raise error_type("Archive expanded size exceeds limit")
    return members


def _read_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    error_type: type[ValueError],
    limit: int,
) -> bytes:
    try:
        with archive.open(info) as member:
            content = member.read(limit + 1)
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile, NotImplementedError) as exc:
        raise error_type(f"Cannot read archive member: {info.filename}") from exc
    if len(content) > limit or len(content) != info.file_size:
        raise error_type("Archive member size is invalid")
    return content


def _dependencies(source: bytes) -> tuple[str, ...]:
    try:
        decoded = source.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            decoded = source.decode("gb18030")
        except UnicodeDecodeError as exc:
            raise ProjectError("MQ source encoding is unsupported") from exc
    dependencies: dict[str, str] = {}
    for match in _IMPORT.finditer(decoded):
        name = match.group(2)
        if not name or "/" in name or "\\" in name or ":" in name or "\x00" in name:
            raise ProjectError("Import name must be a file name without path separators")
        dependencies.setdefault(name.casefold(), name)
    return tuple(dependencies.values())


def read_project(path: Path, *, filename_encoding: str | None = None) -> MqbProject:
    """Read one MQ source and matching resources from an MQB ZIP.

    Ambiguous legacy GB18030 names require ``filename_encoding="gb18030"``.
    Default mode preserves valid UTF-8 ZIP names, decodes unflagged names as
    GB18030, and repairs malformed flags.
    """
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ProjectError(f"Cannot open MQB: {path}") from exc
    with _open_archive(data, ProjectError, filename_encoding=filename_encoding) as archive:
        members = _validated_members(archive, ProjectError, max_expanded_bytes=MAX_EXPANDED_BYTES)
        sources = [info for info in members.values() if not info.is_dir() and PurePosixPath(info.filename).suffix.casefold() == ".mq"]
        if len(sources) != 1:
            raise ProjectError("MQB must contain exactly one MQ source")
        source_info = sources[0]
        stem = str(PurePosixPath(source_info.filename).with_suffix("")).casefold()
        resources: dict[str, zipfile.ZipInfo] = {}
        for info in members.values():
            if info.is_dir():
                continue
            suffix = PurePosixPath(info.filename).suffix.casefold()
            if suffix in _RESOURCES:
                if str(PurePosixPath(info.filename).with_suffix("")).casefold() != stem:
                    raise ProjectError("MQB resource does not match MQ source name")
                resources[_RESOURCES[suffix]] = info
        source = _read_member(archive, source_info, ProjectError, MAX_EXPANDED_BYTES)
        values = {
            field: _read_member(archive, info, ProjectError, MAX_EXPANDED_BYTES)
            for field, info in resources.items()
        }
    return MqbProject(
        source_name=source_info.filename,
        source=source,
        properties=values.get("properties"),
        ui=values.get("ui"),
        attachments=values.get("attachments"),
        dependencies=_dependencies(source),
    )
