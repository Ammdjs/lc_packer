"""Isolated APK construction from explicit compiled input and MQB resources."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
import zipfile

from .compiler import MAX_ARTIFACT_BYTES
from .process import ProcessError, run_process
from .signing import sign_apk
from .templates import inspect_template, supported_format_pair

ANDROID = "{http://schemas.android.com/apk/res/android}"
ET.register_namespace("android", ANDROID[1:-1])


class BuildError(RuntimeError):
    pass


@dataclass(frozen=True)
class BuildTools:
    java: Path
    apktool_jar: Path
    aapt: Path
    zipalign: Path
    apksigner_jar: Path


@dataclass(frozen=True)
class BuildMetadata:
    package_name: str
    app_name: str
    version_code: int
    version_name: str
    icon_png: Path | None = None


@dataclass(frozen=True)
class BuildResult:
    path: Path
    package_name: str
    launch_activity: str
    abis: tuple[str, ...]
    compilation_mode: str
    signature_verified: bool = True
    device_execution: bool = False
    warnings: tuple[str, ...] = ()


def _payload(compiled):
    with Path(compiled.path).open("rb") as stream:
        data = stream.read(MAX_ARTIFACT_BYTES + 1)
    if not data or len(data) > MAX_ARTIFACT_BYTES or hashlib.sha256(data).hexdigest() != compiled.sha256:
        raise BuildError("编译文件为空、超限或已变更；请重新选择/捕获 LC")
    return data


def _validate_metadata(metadata):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+", metadata.package_name):
        raise BuildError("包名应类似 com.example.app，只能使用字母、数字和下划线")
    if not metadata.app_name.strip() or not metadata.version_name.strip() or not 1 <= metadata.version_code <= 2147483647:
        raise BuildError("应用名称、版本名称不能为空，版本号必须大于 0")


def prepare_decoded(root, project, compiled, metadata):
    """Edit only an owned decoded tree; leave opaque runtime metadata untouched."""
    _validate_metadata(metadata)
    data = _payload(compiled)
    root = Path(root)
    assets = root / "assets"
    assets.mkdir(exist_ok=True)
    for name, value in {"script.lc": data, "script.prop": project.properties or b"",
                        "script.uip": project.ui or b"", "script.atc": project.attachments or b"",
                        "script.ui": b"", "script.uis": b"", "script.rtd": b"", "out_script.lc": b""}.items():
        (assets / name).write_bytes(value)
    for directory in (root / "META-INF", root / "original" / "META-INF"):
        if directory.exists():
            for file in directory.iterdir():
                if file.is_file() and (file.suffix.upper() in (".SF", ".RSA", ".DSA", ".EC") or file.name.upper() == "MANIFEST.MF"):
                    file.unlink()
    manifest_path = root / "AndroidManifest.xml"
    tree = ET.parse(manifest_path)
    manifest = tree.getroot()
    old_package = manifest.attrib["package"]
    app = manifest.find("application")
    if app is None:
        raise BuildError("模板缺少 application 节点")
    referenced_identity_strings = set()
    class_attributes = {"application": ("name", "backupAgent", "appComponentFactory"),
                        "activity": ("name", "parentActivityName"), "activity-alias": ("name", "targetActivity"),
                        "service": ("name",), "receiver": ("name",), "provider": ("name",),
                        "instrumentation": ("name",)}
    for node in manifest.iter():
        for attribute in class_attributes.get(node.tag, ()):
            key = ANDROID + attribute
            value = node.get(key)
            if value and not value.startswith("@") and (value.startswith(".") or "." not in value):
                node.set(key, old_package + ("" if value.startswith(".") else ".") + value)
        for key, value in list(node.attrib.items()):
            local = key.removeprefix(ANDROID)
            identity = local in ("authorities", "permission", "readPermission", "writePermission") or (node.tag in ("permission", "uses-permission", "permission-tree", "permission-group") and local == "name")
            if identity:
                if value.startswith("@string/"):
                    referenced_identity_strings.add(value.split("/", 1)[1])
                else:
                    node.set(key, ";".join(metadata.package_name + part[len(old_package):] if part == old_package or part.startswith(old_package + ".") else part for part in value.split(";")))
    manifest.set("package", metadata.package_name)
    app.set(ANDROID + "label", "@string/lc_packer_app_name")
    (root / "res" / "values").mkdir(parents=True, exist_ok=True)
    label_resources = ET.Element("resources")
    ET.SubElement(label_resources, "string", {"name": "lc_packer_app_name"}).text = json.dumps(metadata.app_name, ensure_ascii=False)
    ET.ElementTree(label_resources).write(root / "res" / "values" / "lc_packer.xml", encoding="utf-8", xml_declaration=True)
    for node in app:
        if node.tag in ("activity", "activity-alias") and any(
            category.get(ANDROID + "name") == "android.intent.category.LAUNCHER"
            for category in node.findall("intent-filter/category")
        ):
            node.set(ANDROID + "label", "@string/lc_packer_app_name")
    for strings in (root / "res").glob("values*/strings.xml"):
        resources = ET.parse(strings)
        for node in resources.getroot().findall("string"):
            value = node.text or ""
            if node.get("name") == "app_name":
                # A literal label works for Android and needs no resource escaping.
                node.text = metadata.app_name.replace("\\", "\\\\").replace("'", "\\'").replace('"', '\\"')
            elif node.get("name") in referenced_identity_strings and (value == old_package or value.startswith(old_package + ".")):
                node.text = metadata.package_name + value[len(old_package):]
        resources.write(strings, encoding="utf-8", xml_declaration=True)
    if metadata.icon_png:
        icon = Path(metadata.icon_png).read_bytes()
        if not icon.startswith(b"\x89PNG\r\n\x1a\n"):
            raise BuildError("图标仅支持 PNG 文件")
        (root / "res" / "drawable").mkdir(exist_ok=True)
        (root / "res" / "drawable" / "lc_packer_icon.png").write_bytes(icon)
        app.set(ANDROID + "icon", "@drawable/lc_packer_icon")
        app.attrib.pop(ANDROID + "roundIcon", None)
        for node in app:
            if node.tag in ("activity", "activity-alias") and node.get(ANDROID + "icon"):
                node.set(ANDROID + "icon", "@drawable/lc_packer_icon")
    tree.write(manifest_path, encoding="utf-8", xml_declaration=True)
    config_path = root / "apktool.yml"
    config = config_path.read_text(encoding="utf-8")
    for key, value in (("versionCode", str(metadata.version_code)), ("versionName", json.dumps(metadata.version_name, ensure_ascii=False))):
        config, count = re.subn(rf"(?m)^(\s+{key}:)[^\r\n]*", lambda match: match[1] + " " + value, config)
        if count != 1:
            raise BuildError(f"模板 apktool.yml 中 {key} 配置不唯一或缺失")
    config_path.write_text(config, encoding="utf-8")


class BuildService:
    def __init__(self, tools, *, workspace, timeout_seconds=180, runner=run_process, log=None):
        self.tools, self.workspace = tools, Path(workspace)
        self.timeout, self.runner, self.log = timeout_seconds, runner, log

    def build(self, project, template, compiled, output_path, metadata, signing, *, dependency_files=(),
              allow_unverified_resources=False, acknowledge_unknown_format=False):
        output = Path(output_path).absolute()
        if output.exists():
            raise BuildError(f"输出文件已存在，拒绝覆盖：{output}")
        if not output.parent.is_dir():
            raise BuildError(f"输出目录不存在：{output.parent}")
        _validate_metadata(metadata)
        payload = _payload(compiled)
        warnings = []
        if template.payload_header.startswith(b"\x0a\x14") and payload.startswith(b"eflc"):
            raise BuildError("编译版本不匹配：旧模板不能使用 4.6 eflc 文件，请使用匹配的 3.7.2 编译产物")
        if not supported_format_pair(template.payload_header, payload):
            if not acknowledge_unknown_format:
                raise BuildError("该 LC/模板格式组合尚未验证，请选择已验证组合")
            warnings.append("LC/模板组合未验证；构建成功不代表脚本能够运行")
        if project.ui or project.attachments:
            if not allow_unverified_resources:
                raise BuildError("此工程含 UIP/附件，当前尚未验证其运行；需明确允许未验证资源后再构建")
            warnings.append("已原样嵌入 UIP/ATC；UI及附件执行尚未验证")
        dependencies = {Path(path).name.casefold() for path in dependency_files if Path(path).is_file()}
        missing = [name for name in project.dependencies if name.casefold() not in dependencies]
        if missing:
            raise BuildError("缺少插件/命令库依赖：" + ", ".join(missing))
        if not self.workspace.is_dir() or not str(self.workspace.resolve()).isascii():
            raise BuildError("请选择已存在且完整路径仅含英文字符的构建工作目录（aapt 不支持部分中文路径）")
        for tool in (self.tools.java, self.tools.apktool_jar, self.tools.aapt, self.tools.zipalign, self.tools.apksigner_jar):
            if not Path(tool).is_file():
                raise BuildError(f"构建工具不存在：{tool}")
        try:
            with tempfile.TemporaryDirectory(prefix="build-", dir=self.workspace) as temporary:
                stage = Path(temporary)
                staged_template = stage / "template.apk"
                shutil.copyfile(template.path, staged_template)
                current_template = inspect_template(staged_template)
                if current_template.payload_header != template.payload_header or current_template.abis != template.abis:
                    raise BuildError("模板在检查后已变化；请重新选择并检查模板")
                # Inventory validation happened before this point; independently cap
                # expansion before handing a template to the external extractor.
                with zipfile.ZipFile(staged_template) as archive:
                    if sum(i.file_size for i in archive.infolist()) > 1024 * 1024 * 1024:
                        raise BuildError("模板展开后超过 1 GiB")
                    if any((i.external_attr >> 16) & 0o170000 == 0o120000 for i in archive.infolist()):
                        raise BuildError("模板不允许包含符号链接")
                decoded, frameworks = stage / "decoded", stage / "frameworks"
                run = lambda args, name: self.runner(args, stage=name, timeout=self.timeout, log=self.log)
                run([self.tools.java, "-jar", self.tools.apktool_jar, "d", "-s", "-p", frameworks, "-o", decoded, staged_template], "decode-template")
                prepare_decoded(decoded, project, compiled, metadata)
                unsigned, signed = stage / "unsigned.apk", stage / "signed.apk"
                run([self.tools.java, "-jar", self.tools.apktool_jar, "b", "-p", frameworks, "-o", unsigned, decoded], "build-apk")
                sign_apk(unsigned, signed, self.tools, signing, runner=self.runner, log=self.log, timeout=self.timeout)
                badging = run([self.tools.aapt, "dump", "badging", signed], "verify-metadata")
                package = re.search(r"^package: name='([^']+)' versionCode='([^']+)' versionName='([^']+)'", badging, re.M)
                activity = re.search(r"^launchable-activity: name='([^']+)'", badging, re.M)
                if not package or package.groups() != (metadata.package_name, str(metadata.version_code), metadata.version_name) or not activity:
                    raise BuildError("最终 APK 包名、版本或启动入口校验失败")
                with zipfile.ZipFile(signed) as archive:
                    if hashlib.sha256(archive.read("assets/script.lc")).hexdigest() != compiled.sha256:
                        raise BuildError("最终 APK 内脚本指纹校验失败")
                created = False
                try:
                    with output.open("xb") as target, signed.open("rb") as source:
                        created = True
                        shutil.copyfileobj(source, target)
                except Exception:
                    if created:
                        output.unlink(missing_ok=True)
                    raise
                return BuildResult(output, metadata.package_name, activity[1], template.abis, compiled.mode, warnings=tuple(warnings))
        except (OSError, ValueError, ProcessError, zipfile.BadZipFile) as exc:
            raise BuildError(str(exc)) from exc
