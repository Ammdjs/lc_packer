"""Read-only preflight checks for the explicitly selected packaging environment.

This module never discovers account settings, installs tools, writes test files,
starts an assistant, or starts an ADB server. Directory and LC header checks are
estimates; they cannot prove that a later build or script execution will succeed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import sys
import zipfile

from .templates import TemplateError, TemplateInfo, inspect_template, supported_format_pair


PROBE_TIMEOUT_SECONDS = 10
MIN_FREE_BYTES = 512 * 1024 * 1024
MAX_COMPILED_BYTES = 128 * 1024 * 1024
_STORE_PASSWORD_ENV = "LC_PACKER_DIAGNOSTIC_STORE_PASSWORD"


@dataclass(frozen=True, slots=True)
class EnvironmentConfig:
    java: Path | None = None
    apktool: Path | None = None
    aapt: Path | None = None
    zipalign: Path | None = None
    apksigner: Path | None = None
    adb: Path | None = None
    assistant: Path | None = None
    template: Path | None = None
    compiled: Path | None = None
    keystore: Path | None = None
    workspace: Path | None = None
    output: Path | None = None
    alias: str | None = None
    serial: str | None = None
    store_password: str | None = field(default=None, repr=False)
    key_password: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class CheckResult:
    key: str
    status: str
    title: str
    detail: str
    remedy: str
    blocking: bool
    path: str | None = None
    version: str | None = None


@dataclass(frozen=True, slots=True)
class EnvironmentReport:
    items: tuple[CheckResult, ...]

    @property
    def build_ready(self) -> bool:
        return not any(item.blocking and item.status != "pass" for item in self.items)

    @property
    def device_ready(self) -> bool:
        by_key = {item.key: item for item in self.items}
        return all(key in by_key and by_key[key].status == "pass" for key in ("adb", "device", "device_abi"))

    def to_dict(self) -> dict:
        return {"items": [asdict(item) for item in self.items],
                "build_ready": self.build_ready, "device_ready": self.device_ready}


def _assistant_version(path: Path) -> str | None:
    """Read the selected executable's fixed Windows version resource in-process."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    try:
        version = ctypes.WinDLL("version", use_last_error=True)
        version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
        version.GetFileVersionInfoSizeW.restype = wintypes.DWORD
        version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        version.GetFileVersionInfoW.restype = wintypes.BOOL
        version.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
        version.VerQueryValueW.restype = wintypes.BOOL
        size = version.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return None
        data = ctypes.create_string_buffer(size)
        if not version.GetFileVersionInfoW(str(path), 0, size, data):
            return None
        pointer, length = ctypes.c_void_p(), wintypes.UINT()
        # Some assistants put the displayed version only in StringFileInfo;
        # their fixed numeric resource is the unrelated default 1.0.0.0.
        if version.VerQueryValueW(data, "\\VarFileInfo\\Translation", ctypes.byref(pointer), ctypes.byref(length)):
            pairs = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_ushort))
            translations = [(pairs[i], pairs[i + 1]) for i in range(0, length.value // 2 - 1, 2)]
            for language, codepage in translations:
                for key in ("FileVersion", "ProductVersion"):
                    query = f"\\StringFileInfo\\{language:04x}{codepage:04x}\\{key}"
                    if version.VerQueryValueW(data, query, ctypes.byref(pointer), ctypes.byref(length)) and length.value:
                        value = ctypes.wstring_at(pointer, length.value).rstrip("\x00").strip()
                        if re.fullmatch(r"\d+(?:\.\d+){1,3}", value):
                            return value
        if not version.VerQueryValueW(data, "\\", ctypes.byref(pointer), ctypes.byref(length)) or length.value < 52:
            return None
        words = ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD))
        if words[0] != 0xFEEF04BD:
            return None
        major, minor = words[2] >> 16, words[2] & 0xFFFF
        patch, build = words[3] >> 16, words[3] & 0xFFFF
        return f"{major}.{minor}.{patch}.{build}"
    except (OSError, ValueError, AttributeError):
        return None


def _adb_server_available(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _template_error_detail(error) -> str:
    message = str(error)
    reasons = {
        "Invalid ZIP archive": "模板不是有效的 ZIP/APK 文件，或压缩文件已损坏。",
        "Invalid ZIP directory": "模板压缩目录已损坏，无法读取。",
        "Invalid ZIP directory entry": "模板压缩目录条目已损坏。",
        "Invalid ZIP local header": "模板压缩文件头已损坏。",
        "Unsupported legacy ZIP directory": "模板使用了当前不支持的旧版压缩目录结构。",
        "Unsupported ZIP filename encoding": "模板内文件名使用了不受支持的编码。",
        "ZIP member name cannot be decoded as GB18030": "模板内的文件名无法按 GB18030 编码读取。",
        "Template requires AndroidManifest.xml and classes.dex": "模板缺少必需的 AndroidManifest.xml 或 classes.dex 文件。",
        "Cannot read template payload header": "无法读取模板内的脚本载荷文件头，内容可能损坏。",
        "Archive contains too many entries": "模板内文件数量超过支持的限制。",
        "Unsafe archive member path": "模板包含不安全的文件路径，已拒绝读取。",
        "Duplicate archive member name": "模板包含重复或仅大小写不同的文件名，无法安全读取。",
        "Encrypted archive member is unsupported": "模板包含加密文件，当前不支持读取。",
        "Archive expanded size exceeds limit": "模板解压后的内容大小超过支持的限制。",
        "尚未选择 APK 模板。": "尚未选择 APK 模板。",
    }
    reason = reasons.get(message)
    if reason is None:
        reason = ("无法打开模板文件，请检查路径及读取权限。" if message.startswith("Cannot open template:") else
                  "模板检查失败：文件无法读取或内容结构不受支持。")
    return f"{reason}\n原始错误：{message}"


def _signing_error_reason(output: str) -> str:
    """Explain familiar keytool errors; raw output still drives no decisions."""
    value = output.lower()
    if any(text in value for text in ("password was incorrect", "password is incorrect", "password verification failed", "keystore was tampered")):
        return "密钥库密码不正确，或密钥库文件已损坏；请核对密码及所选文件。"
    if "alias" in value and "does not exist" in value:
        return "密钥库中不存在所填写的别名，请核对签名密钥别名。"
    if "trustedcertentry" in value:
        return "所选别名只有证书，没有可用于签名的私钥；请选择包含私钥的条目。"
    if "invalid keystore format" in value or "unrecognized keystore format" in value:
        return "密钥库格式无效或无法识别，请选择完整的密钥库文件。"
    return "签名配置检查失败，未确认别名下存在私钥条目（PrivateKeyEntry）。"


class _Checks:
    def __init__(self, config: EnvironmentConfig):
        self.config = config
        self.items: list[CheckResult] = []
        self._raw_outputs = {}
        self.secrets = tuple(secret for secret in (config.store_password, config.key_password) if secret)

    def clean(self, value: str | None) -> str | None:
        if value is None:
            return None
        for secret in sorted(self.secrets, key=len, reverse=True):
            value = value.replace(secret, "[REDACTED]")
        return value

    def add(self, key, status, title, detail, remedy="", blocking=True, path=None, version=None):
        # Clean every report field, including exception text and tool output.
        remedy = remedy or ("无需处理。" if status == "pass" else "请核对相关配置，修正后重新检测。")
        item = CheckResult(key, status, title, self.clean(detail)[:12000], self.clean(remedy), blocking,
                           self.clean(str(path)) if path is not None else None, self.clean(version))
        self.items.append(item)
        return item

    def file_error(self, path: Path | None) -> str | None:
        if path is None:
            return "尚未选择文件路径。"
        try:
            if not Path(path).is_file():
                return f"所选路径不存在或不是普通文件：{path}"
            if not os.access(path, os.R_OK):
                return f"没有读取所选文件的权限：{path}"
        except (OSError, ValueError) as exc:
            return f"无法检查所选文件 {path}。系统原始错误：{exc}"
        return None

    def run(self, args: list[str], *, env: dict[str, str] | None = None):
        """One process seam; output is redacted before it enters a result."""
        options = dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, encoding="utf-8", errors="replace", timeout=PROBE_TIMEOUT_SECONDS,
                       shell=False)
        if env is not None:
            options["env"] = env
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW
        try:
            result = subprocess.run(args, **options)
        except subprocess.TimeoutExpired:
            return None, f"工具检测超时，已等待 {PROBE_TIMEOUT_SECONDS} 秒。"
        except (OSError, ValueError) as exc:
            return None, self.clean(f"无法启动所选工具。系统原始错误：{exc}")
        # Bound report size, without disclosing any captured password in truncation.
        output = (result.stdout or "").strip()
        return result.returncode, output

    def tool(self, key, path, args, *, jar=False, blocking=True, usage_ok=False):
        title = {"java": "Java / JDK", "apktool": "apktool", "aapt": "Android aapt",
                 "zipalign": "Android zipalign", "apksigner": "Android apksigner", "adb": "Android Debug Bridge"}[key]
        remedy = f"请选择可正常运行的 {title} {'JAR 文件' if jar else '可执行文件'}，然后重新检测。"
        problem = self.file_error(path)
        if problem is None and jar:
            try:
                if not zipfile.is_zipfile(path):
                    problem = "所选 JAR 文件不是有效的 ZIP/JAR 压缩文件，可能已经损坏或选错了文件。"
            except OSError as exc:
                problem = f"无法读取所选 JAR 文件。系统原始错误：{exc}"
        if problem:
            return self.add(key, "error" if blocking else "warning", title, problem, remedy, blocking, path)
        code, output = self.run(args)
        self._raw_outputs[key] = output
        # zipalign intentionally returns a nonzero usage exit when called without files.
        expected_usage = usage_ok and code in (0, 1, 2) and "usage" in output.lower() and "zipalign" in output.lower()
        if code is None or (code != 0 and not expected_usage):
            return self.add(key, "error" if blocking else "warning", title,
                            f"工具检测失败。退出码：{code if code is not None else '无法获取'}。\n工具路径：{path}\n原始输出：\n{output}",
                            remedy, blocking, path)
        if not output or (usage_ok and not expected_usage):
            return self.add(key, "error" if blocking else "warning", title,
                            f"工具未返回预期的版本或用法信息。退出码：{code}。\n工具路径：{path}\n原始输出：\n{output}",
                            remedy, blocking, path)
        version = output.splitlines()[0]
        return self.add(key, "pass", title, f"工具检测通过。退出码：{code}。\n原始输出：\n{output}", blocking=blocking, path=path, version=version)

    def runtime(self):
        self.add("python", "pass" if sys.version_info >= (3, 11) else "error", "Python 运行时",
                 f"当前 Python 版本：{platform.python_version()}（{64 if sys.maxsize > 2**32 else 32} 位）。程序需要 Python 3.11 或更新版本。",
                 "EXE 版已内置 Python，无需额外安装；源码版请使用 Python 3.11 或更新版本启动。", path=sys.executable, version=platform.python_version())
        try:
            import PySide6
            # Import the desktop binding to expose missing native dependencies,
            # without constructing an application or testing platform plugins.
            from PySide6 import QtCore, QtWidgets

            version = f"PySide6 {PySide6.__version__}; Qt {QtCore.qVersion()}"
            self.add("qt", "pass", "Qt / PySide6",
                     f"{version} 加载成功。本项仅检查组件导入，未创建窗口，也未验证平台插件能否正常启动。",
                     blocking=False, version=version)
        except Exception as exc:
            self.add("qt", "warning", "Qt / PySide6", f"Qt / PySide6 界面组件无法加载。原始错误：{exc}",
                     "EXE 版请重新获取完整程序；源码版请用启动本程序的 Python 执行 python -m pip install PySide6。"
                     "如果提示缺少 DLL 或依赖，请重新安装匹配的 PySide6 和运行库。命令行（CLI）离线构建仍可使用。", False)

    def tools(self):
        config = self.config
        java = str(config.java or "")
        java_item = self.tool("java", config.java, [java, "-version"])
        if java_item.status == "pass":
            match = re.search(r'(?:openjdk|java) version\s+"([\d.]+)', self._raw_outputs.get("java", ""), re.I)
            major = int(match[1].split(".")[0]) if match else 0
            if major == 1 and match:
                major = int(match[1].split(".")[1])
            if major < 17:
                self.items[-1] = replace(java_item, status="error", detail=java_item.detail + "\n未能确认 Java 版本达到 17 或更高。",
                                         remedy="请选择 JDK 17 或更新版本；当前已验证的 apktool 3 工具链需要 Java 17 或更高版本。")
        for key, path, verb in (("apktool", config.apktool, "--version"),
                                ("apksigner", config.apksigner, "version")):
            jar = key == "apksigner" or (path is not None and Path(path).suffix.lower() == ".jar")
            if jar and any(item.key == "java" and item.status != "pass" for item in self.items):
                self.add(key, "error", key, f"暂时无法检测 {path}，需要先配置可用的 Java 17 或更新版本。",
                         "请先修正 Java 路径或版本，然后重新检测。", path=path)
            else:
                args = [java, "-jar", str(path), verb] if jar else [str(path), verb]
                self.tool(key, path, args, jar=jar)
        self.tool("aapt", config.aapt, [str(config.aapt), "version"])
        self.tool("zipalign", config.zipalign, [str(config.zipalign)], usage_ok=True)

    def assistant(self):
        path = self.config.assistant
        problem = self.file_error(path)
        if problem:
            self.add("assistant", "warning", "所选手机助手", problem,
                     "需要手动编译或捕获 LC 时，请选择手机助手可执行文件；已有匹配 LC 时仍可直接打包。", False, path)
            return
        version = _assistant_version(Path(path))
        known = version is not None and (version == "3.7.2" or version.startswith("3.7.2."))
        self.add("assistant", "pass" if known else "warning", "所选手机助手",
                 f"所选程序文件存在。Windows 文件版本：{version or '无法获取'}。"
                 + ("3.7.2 版本的手动调试及 LC 捕获流程已经验证。" if known else
                    "此版本生成的 LC 与模板的兼容性尚未验证。"),
                 "请在所选手机助手中手动调试同一工程，再选择或捕获其 LC；运行效果需要在设备上验证。",
                 False, path, version)

    def artifacts(self) -> TemplateInfo | None:
        config = self.config
        template = None
        try:
            if config.template is None:
                raise TemplateError("尚未选择 APK 模板。")
            template = inspect_template(config.template)
            self.add("template", "pass", "所选模板内容",
                     f"原生架构（ABI）：{', '.join(template.abis) or '未检测到'}。"
                     f"脚本资源：{', '.join(template.script_assets) or '未检测到'}。"
                     f"脚本载荷文件头：{template.payload_header.hex() or '为空或不存在'}。", path=config.template)
        except (TemplateError, OSError, ValueError) as exc:
            self.add("template", "error", "所选模板内容", _template_error_detail(exc),
                     "请选择完整的 APK/APKM ZIP 模板，需包含 AndroidManifest.xml、classes.dex 和受支持的脚本载荷。", path=config.template)
        compiled_header = b""
        problem = self.file_error(config.compiled)
        if problem is None:
            try:
                size = Path(config.compiled).stat().st_size
                with Path(config.compiled).open("rb") as source:
                    compiled_header = source.read(4)
                if size <= 4:
                    problem = "所选 LC 为空，或仅包含文件头／占位内容；请使用完整的编译产物。"
                elif size > MAX_COMPILED_BYTES:
                    problem = "所选 LC 超过当前支持的 128 MiB 大小限制。"
            except (OSError, ValueError) as exc:
                problem = f"无法读取所选 LC。系统原始错误：{exc}"
        known = compiled_header == b"eflc" or compiled_header.startswith(b"\x0a\x14")
        if problem:
            self.add("compiled", "error", "所选编译文件 LC", problem,
                     "请在手机助手中手动编译，并选择完整的 LC 文件；MQ/MQB 源码文件不能代替编译产物。", path=config.compiled)
        else:
            self.add("compiled", "pass" if known else "warning", "所选编译文件 LC",
                     f"所选 LC 文件头：{compiled_header.hex()}。"
                     + ("已识别文件头；这仅确认格式特征，尚不能证明文件有效或脚本可以运行。" if known else
                        "LC 格式未知，无法确认兼容性。"),
                     "请选择兼容版本手机助手生成的 LC，并在设备上验证脚本运行。", path=config.compiled)
        if template is not None and not problem:
            template_header = template.payload_header
            old_template = template_header.startswith(b"\x0a\x14")
            if old_template and compiled_header == b"eflc":
                self.add("compatibility", "error", "LC 与模板兼容性",
                         "已知不兼容：模板使用旧 0a14 文件头，而所选 LC 使用 eflc 文件头。",
                         "请使用与旧模板匹配的手机助手生成 LC，或选择已验证支持 eflc 的模板。")
            elif supported_format_pair(template_header, compiled_header):
                self.add("compatibility", "pass", "LC 与模板兼容性",
                         "文件头符合当前支持的同一格式系列。文件头匹配不能证明脚本可以运行，仍需在设备上测试。")
            else:
                self.add("compatibility", "warning", "LC 与模板兼容性",
                         "当前构建流程不支持此模板与 LC 的格式组合（包括双方都使用 eflc 文件头的情况），兼容性尚未验证。",
                         "请使用已验证的旧 0a14 模板与 LC 组合。其他组合需要单独验证构建方案，不能仅凭文件头相同就打包。")
        else:
            self.add("compatibility", "error", "LC 与模板兼容性",
                     "所选模板或 LC 无法读取，暂时不能检查两者的兼容性。",
                     "请先修正模板和 LC 文件选择，再重新检测。")
        self.add("automatic_compilation", "warning", "自动源码编译入口",
                 "暂不支持直接自动编译 MQ/MQB 源码：尚未验证可稳定调用的编译器命令行或接口。"
                 "当前支持使用手机助手生成的 LC 进行打包；本项提示不会阻止已有匹配 LC 的打包。",
                 "请先在新工具中点击“开始捕获新 LC”，再到所选手机助手手动调试同一工程；"
                 "也可以直接选择该工程已生成的完整 LC 文件。", False)
        return template

    def signing(self):
        config = self.config
        problem = self.file_error(config.keystore)
        if not problem and not config.alias:
            problem = "尚未填写签名密钥别名。"
        if not problem and not config.store_password:
            problem = "尚未输入密钥库密码。"
        keytool = Path(config.java).with_name("keytool.exe" if os.name == "nt" else "keytool") if config.java else None
        if not problem:
            problem = self.file_error(keytool)
        remedy = "请选择包含 keytool 的 JDK，并核对密钥库文件、私钥别名和仓库密码；私钥密码将在实际签名时验证。"
        if problem:
            self.add("signing", "error", "签名配置", problem, remedy, path=config.keystore)
            return
        env = os.environ.copy()
        env[_STORE_PASSWORD_ENV] = config.store_password
        args = [str(keytool), "-J-Duser.language=en", "-J-Duser.country=US", "-list", "-v",
                "-keystore", str(config.keystore), "-alias", config.alias, "-storepass:env", _STORE_PASSWORD_ENV]
        code, output = self.run(args, env=env)
        if code != 0 or "privatekeyentry" not in output.lower():
            self.add("signing", "error", "签名配置",
                     _signing_error_reason(output) + "\n"
                     f"keytool 退出码：{code if code is not None else '无法获取'}。\n原始输出：\n{output}",
                     remedy, path=config.keystore)
        else:
            self.add("signing", "pass", "签名配置",
                     f"keytool 已确认仓库密码正确，且别名 {config.alias} 对应私钥。"
                     "本项尚未验证私钥密码；keytool -list 无法确认该密码，最终以实际签名结果为准。", path=config.keystore)

    def directory(self, key: str, path: Path | None, *, output: bool = False):
        title = "APK 输出路径" if output else "构建工作目录"
        remedy = "请选择已存在、可写且剩余空间至少为 512 MiB 的工作目录；完整路径请使用英文字母、数字等 ASCII 字符。"
        if output:
            remedy = "请在已存在、可写且剩余空间至少为 512 MiB 的目录中，选择尚不存在的 APK 输出文件名。"
        try:
            if path is None:
                raise ValueError("尚未选择路径。")
            path = Path(path)
            if output:
                if path.exists():
                    raise ValueError("输出路径已存在，程序不会覆盖已有文件；请选择新的 APK 文件名。")
                if path.exists() and not path.is_file():
                    raise ValueError("输出路径不是普通文件。")
                if path.is_file() and not os.access(path, os.W_OK):
                    raise ValueError("没有写入已有输出文件的权限。")
                parent = path.parent
            else:
                parent = path
                if not str(parent.resolve()).isascii():
                    raise ValueError("工作目录的完整路径必须仅包含 ASCII 字符；当前 aapt 工具在部分中文路径下会失败。")
            if not parent.is_dir():
                raise ValueError(f"目录不存在：{parent}")
            if not os.access(parent, os.W_OK | os.X_OK):
                raise ValueError(f"目录权限预检查显示无法写入或访问：{parent}")
            free = shutil.disk_usage(parent).free
            self.add(key, "pass" if free >= MIN_FREE_BYTES else "warning", title,
                     f"权限预检查：可以写入和访问；剩余空间 {free:,} 字节。"
                     "建议至少保留 512 MiB。此处为只读预检查，实际写入仍以最终构建结果为准。",
                     remedy, path=path)
        except (OSError, ValueError) as exc:
            self.add(key, "error", title, f"目录或输出路径检查失败：{path}\n错误原因：{exc}", remedy, path=path)

    def devices(self, template: TemplateInfo | None):
        config = self.config
        adb = self.tool("adb", config.adb, [str(config.adb), "version"], blocking=False)
        if adb.status != "pass":
            self.add("device", "warning", "所选设备", "ADB 不可用，暂时无法检查所选设备。",
                     "请选择可用的 adb 可执行文件，并在设备上开启 USB 调试。", False)
            return
        # Explicit local server selection prevents inherited remote/socket settings
        # from accidentally addressing another machine or auto-starting a server.
        host, port = "127.0.0.1", 5037
        server_socket = os.environ.get("ADB_SERVER_SOCKET")
        try:
            port = int(os.environ.get("ANDROID_ADB_SERVER_PORT", "5037"))
            if server_socket:
                match = re.fullmatch(r"tcp:(localhost|127\.0\.0\.1):(\d+)", server_socket)
                if not match:
                    raise ValueError("ADB_SERVER_SOCKET 不是受支持的本机 TCP 地址")
                port = int(match[2])
            if not 1 <= port <= 65535:
                raise ValueError("ADB 服务端口必须介于 1 和 65535 之间")
        except ValueError as exc:
            self.add("device", "warning", "所选设备", f"无法检查配置的 ADB 服务：{exc}。",
                     "请使用已经运行的本机 TCP ADB 服务，或清除不受支持的 ADB 服务环境变量配置。", False)
            return
        endpoint = f"{host}:{port}"
        if not _adb_server_available(host, port):
            self.add("device", "warning", "所选设备", f"本机 {endpoint} 没有正在监听的 ADB 服务，本次检测未启动服务。",
                     f"请手动使用所选 adb 执行 adb -P {port} start-server，然后重新检测环境。", False)
            return
        if not config.serial:
            self.add("device", "warning", "所选设备", f"尚未选择设备序列号（ADB 服务：{endpoint}）。",
                     "请填写目标设备的准确序列号，并在设备上确认 USB 调试授权。", False)
            return
        base = [str(config.adb), "-H", host, "-P", str(port)]
        code, output = self.run(base + ["devices", "-l"])
        if code != 0:
            self.add("device", "warning", "所选设备", f"无法获取设备列表。ADB 服务：{endpoint}；退出码：{code}。\n原始输出：\n{output}",
                     "请检查所选 adb 是否完整，以及本机已经运行的 ADB 服务是否正常。", False)
            return
        devices = {}
        for line in output.splitlines():
            parts = line.split()
            if len(parts) >= 2 and not line.startswith(("List ", "*")):
                devices[parts[0]] = parts[1]
        state = devices.get(config.serial)
        if state != "device":
            repair = ("请解锁设备，并确认设备上的 USB 调试授权提示。" if state == "unauthorized" else
                      "请重新连接目标设备，开启 USB 调试，并填写准确的设备序列号。")
            state_text = {"unauthorized": "尚未授权 USB 调试", "offline": "离线", None: "未找到设备"}.get(state, "设备状态异常")
            self.add("device", "warning", "所选设备",
                     f"设备 {config.serial}：{state_text}（原始状态：{state or '无'}；ADB 服务：{endpoint}）。", repair, False)
            return
        self.add("device", "pass", "所选设备", f"设备 {config.serial} 已在线（ADB 服务：{endpoint}）。", blocking=False)
        code, abis = self.run(base + ["-s", config.serial, "shell", "getprop", "ro.product.cpu.abilist"])
        if code == 0 and not abis:
            code, abis = self.run(base + ["-s", config.serial, "shell", "getprop", "ro.product.cpu.abi"])
        device_abis = {abi.strip() for abi in abis.split(",") if abi.strip()} if code == 0 else set()
        template_abis = set(template.abis) if template else set()
        compatible = bool(device_abis and template_abis and device_abis.intersection(template_abis))
        self.add("device_abi", "pass" if compatible else "warning", "设备与模板架构",
                 f"设备架构（ABI）：{', '.join(sorted(device_abis)) or '无法获取'}。"
                 f"模板架构（ABI）：{', '.join(sorted(template_abis)) or '无法获取'}。"
                 + ("至少有一种原生架构匹配。" if compatible else f"尚未确认架构兼容；getprop 退出码：{code}。\n原始输出：{abis if code != 0 else '无'}"),
                 "请选择包含目标设备所需原生架构库的模板，然后重新检测。", False)


def check_environment(config: EnvironmentConfig) -> EnvironmentReport:
    """Inspect only selected paths, without modifying tools, files or devices."""
    checks = _Checks(config)
    checks.runtime()
    checks.tools()
    checks.assistant()
    template = checks.artifacts()
    checks.signing()
    checks.directory("workspace", config.workspace)
    checks.directory("output", config.output, output=True)
    checks.devices(template)
    return EnvironmentReport(tuple(checks.items))
