"""Shared settings and actions consumed by the CLI and desktop form."""
from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import shutil

from .build import BuildMetadata, BuildService, BuildTools
from .compiler import ManualCaptureSession, load_precompiled
from .diagnostics import EnvironmentConfig
from .process import run_process
from .project import read_project
from .runtime import application_root
from .signing import SigningConfig
from .templates import inspect_template

ROOT = application_root()
CONFIG_PATH = ROOT / "local-config.json"
FIELDS = ("source", "template", "assistant", "compiled", "output", "workspace", "java", "apktool",
          "aapt", "zipalign", "apksigner", "adb", "keystore", "alias", "serial", "icon",
          "dependency_dir", "app_name", "package_name", "version_name", "version_code")


def _first(paths):
    return str(next((Path(path) for path in paths if path and Path(path).is_file()), ""))


def default_settings():
    settings = dict.fromkeys(FIELDS, "")
    java_candidates = []
    if os.environ.get("JAVA_HOME"):
        java_candidates.append(Path(os.environ["JAVA_HOME"]) / "bin/java.exe")
    java_candidates.extend(sorted(Path("C:/Program Files/Eclipse Adoptium").glob("jdk-17*/bin/java.exe"), reverse=True))
    java_candidates.append(shutil.which("java"))
    sdk_roots = [os.environ.get("ANDROID_SDK_ROOT"), os.environ.get("ANDROID_HOME"),
                 str(Path(os.environ.get("LOCALAPPDATA", "")) / "Android/Sdk")]
    tool_dirs = [folder for sdk in sdk_roots if sdk for folder in (Path(sdk) / "build-tools").glob("*") if (folder / "aapt.exe").is_file()]
    tool_dirs.sort(key=lambda path: (path.name == "36.1.0", tuple(int(part) for part in path.name.split(".") if part.isdigit())), reverse=True)
    tools = tool_dirs[0] if tool_dirs else Path("missing-sdk")
    assistant_candidates = [Path("E:/按键精灵/按键精灵手机助手/按键精灵手机助手.exe"), Path("C:/按键精灵/按键精灵手机助手/按键精灵手机助手.exe")]
    assistant = _first(assistant_candidates)
    settings.update(java=_first(java_candidates), apktool=_first([base / "tools/apktool_3.0.3.jar" for base in (ROOT, ROOT.parent)]),
                    aapt=_first([tools / "aapt.exe"]), zipalign=_first([tools / "zipalign.exe"]),
                    apksigner=_first([tools / "lib/apksigner.jar"]), assistant=assistant,
                    adb=_first([Path(assistant).parent / "android/adb.exe" if assistant else None, shutil.which("adb")]),
                    template=_first([base / "res/pack/510-暂停-热更-不自启(支持雷电9).apk" for base in (ROOT, ROOT.parent, ROOT.parent.parent)]),
                    workspace=str(ROOT / "artifacts/work"), output=str(ROOT / "artifacts/output/app.apk"),
                    app_name="我的脚本", package_name="com.example.myscript", version_name="1.0.0", version_code="1", alias="lcpacker")
    return settings


def load_settings(path=CONFIG_PATH):
    settings = default_settings()
    if Path(path).is_file():
        values = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if not isinstance(values, dict):
            raise ValueError("配置文件必须是 JSON 对象")
        settings.update({key: str(value) for key, value in values.items() if key in FIELDS and value is not None})
    return settings


def save_settings(path, settings):
    Path(path).write_text(json.dumps({key: str(settings.get(key, "")) for key in FIELDS}, ensure_ascii=False, indent=2), encoding="utf-8")


def output_path_for_name(output, app_name):
    """Keep the selected directory while making a Windows-safe APK filename."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", app_name.strip()).rstrip(" .")
    if not name or not output.strip():
        return output
    if re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9]", name.split(".")[0], re.IGNORECASE):
        name = "_" + name
    path = Path(output.strip())
    filename = name + ".apk"
    result = path / filename if output.rstrip().endswith(("/", "\\")) or not path.name else path.with_name(filename)
    return result.as_posix()


def environment_config(settings, password="", key_password=""):
    paths = ("java", "apktool", "aapt", "zipalign", "apksigner", "adb", "assistant", "template", "compiled", "keystore", "workspace", "output")
    return EnvironmentConfig(**{name: Path(settings[name]) if settings.get(name) else None for name in paths},
                             alias=settings.get("alias"), serial=settings.get("serial") or None,
                             store_password=password or None, key_password=key_password or None)


def build_from_settings(settings, password, key_password="", *, log=None, allow_unverified_resources=False):
    required = ("source", "template", "compiled", "output", "java", "apktool", "aapt", "zipalign", "apksigner", "keystore", "workspace")
    missing = [name for name in required if not settings.get(name)]
    if missing:
        raise ValueError("缺少设置：" + ", ".join(missing))
    workspace = Path(settings["workspace"])
    workspace.mkdir(parents=True, exist_ok=True)
    output = Path(settings["output"])
    if not output.parent.is_dir():
        raise ValueError(f"请先选择存在的输出目录：{output.parent}")
    tools = BuildTools(*(Path(settings[name]) for name in ("java", "apktool", "aapt", "zipalign", "apksigner")))
    dependencies = tuple(Path(settings["dependency_dir"]).iterdir()) if settings.get("dependency_dir") else ()
    return BuildService(tools, workspace=workspace, log=log).build(
        read_project(Path(settings["source"])), inspect_template(Path(settings["template"])),
        load_precompiled(Path(settings["compiled"])), output,
        BuildMetadata(settings["package_name"], settings["app_name"], int(settings["version_code"]), settings["version_name"], Path(settings["icon"]) if settings.get("icon") else None),
        SigningConfig(Path(settings["keystore"]), settings["alias"], password, key_password or None),
        dependency_files=dependencies, allow_unverified_resources=allow_unverified_resources)


def capture_from_settings(settings, *, timeout=120, cancel_event=None, log=None):
    if not settings.get("assistant"):
        raise ValueError("请先选择手机助手 EXE")
    output = Path(settings["workspace"]) / "captures"
    output.mkdir(parents=True, exist_ok=True)
    session = ManualCaptureSession(Path(settings["assistant"]).parent / "compile.lc", output)
    if log:
        log("捕获已开始：现在请在所选手机助手中手动调试当前工程。旧文件不会被复用。")
    return session.wait(timeout_seconds=timeout, cancel_event=cancel_event)


def create_keystore(settings, password, *, log=None):
    path = Path(settings["keystore"])
    if not settings.get("keystore") or path.exists():
        raise ValueError("请选择尚不存在的 .p12 密钥库路径")
    if len(password) < 6 or not settings.get("alias"):
        raise ValueError("密钥库密码至少 6 位，别名不能为空")
    keytool = Path(settings["java"]).with_name("keytool.exe" if os.name == "nt" else "keytool")
    env = dict(os.environ, LC_PACKER_NEW_KEY_PASSWORD=password)
    run_process([keytool, "-genkeypair", "-keystore", path, "-storetype", "PKCS12", "-alias", settings["alias"],
                 "-storepass:env", "LC_PACKER_NEW_KEY_PASSWORD", "-keypass:env", "LC_PACKER_NEW_KEY_PASSWORD",
                 "-dname", "CN=LCPacker Owner", "-keyalg", "RSA", "-keysize", "3072", "-validity", "3650"],
                stage="create-keystore", env=env, secrets=(password,), log=log)
    return path
