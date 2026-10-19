"""Inventory APK-like ZIP templates without extracting their contents."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import zipfile

from .project import _open_archive, _validated_members


class TemplateError(ValueError):
    """The selected file is not a usable or safely inspectable APK template."""


def supported_format_pair(template_header: bytes, compiled_header: bytes) -> bool:
    """Current packer acceptance policy; matching headers do not prove execution."""
    return template_header.startswith(b"\x0a\x14") and compiled_header.startswith(b"\x0a\x14")


@dataclass(frozen=True, slots=True)
class TemplateInfo:
    path: Path
    abis: tuple[str, ...]
    script_assets: tuple[str, ...]
    payload_header: bytes


def inspect_template(path: Path) -> TemplateInfo:
    """Report the architecture and script placeholders of an APK or APKM ZIP."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise TemplateError(f"Cannot open template: {path}") from exc
    with _open_archive(data, TemplateError) as archive:
        members = _validated_members(archive, TemplateError, max_expanded_bytes=None)
        if "androidmanifest.xml" not in members or "classes.dex" not in members:
            raise TemplateError("Template requires AndroidManifest.xml and classes.dex")
        abis = {
            parts[1]
            for info in members.values()
            if not info.is_dir()
            for parts in [info.filename.split("/")]
            if len(parts) == 3 and parts[0].casefold() == "lib" and parts[2].casefold().endswith(".so")
        }
        script_assets = sorted(
            (
                info.filename
                for info in members.values()
                if not info.is_dir()
                and info.filename.casefold().startswith("assets/")
                and info.filename.count("/") == 1
                and (
                    info.filename.split("/", 1)[1].casefold().startswith("script.")
                    or info.filename.split("/", 1)[1].casefold() == "out_script.lc"
                )
            ),
            key=str.casefold,
        )
        payload = members.get("assets/script.lc") or members.get("assets/out_script.lc")
        if payload is None:
            header = b""
        else:
            try:
                with archive.open(payload) as member:
                    header = member.read(4)
            except (OSError, RuntimeError, ValueError, zipfile.BadZipFile, NotImplementedError) as exc:
                raise TemplateError("Cannot read template payload header") from exc
    return TemplateInfo(path, tuple(sorted(abis, key=str.casefold)), tuple(script_assets), header)
