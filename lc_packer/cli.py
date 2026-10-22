"""Command line entry point for the same services used by the desktop form."""
import argparse
from dataclasses import asdict
import getpass
import json
import os
from pathlib import Path
import sys

from .app import CONFIG_PATH, ROOT, build_from_settings, capture_from_settings, environment_config, load_settings, save_settings
from .compiler import compile_source
from .diagnostics import check_environment
from .project import read_project
from .templates import inspect_template
from .toolchain import install_packages, load_catalog, package_status


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="LC打包 — MQ 脚本（.mq）/兼容 MQB 工程包（.mqb）+ 显式 LC 打包",
        epilog="MQ 同目录同名 .prop/.uip/.atc 自动读取；先选择工程，再捕获对应的新 LC。")
    commands = parser.add_subparsers(dest="command")
    inspect = commands.add_parser(
        "inspect", help="检查 MQ 脚本（.mq）/兼容 MQB 工程包或模板，不执行脚本",
        description="检查 MQ 脚本（.mq）/兼容 MQB 工程包（.mqb）或 APK 模板，不执行脚本。MQ 同目录同名 .prop/.uip/.atc 自动读取。")
    inspect.add_argument("path", type=Path, help="MQ 脚本（.mq）、兼容 MQB 工程包（.mqb）或 APK 模板路径")
    inspect.add_argument("--template", action="store_true", help="按 APK 模板检查")
    compile_cmd = commands.add_parser("compile", help="显示自动编译入口状态")
    compile_cmd.add_argument("path", type=Path, help="MQ 脚本（.mq）或兼容 MQB 工程包（.mqb）路径")
    for name in ('install-tools', 'tools-status'):
        sub = commands.add_parser(name, help='离线安装随包工具' if name == 'install-tools' else '检查随包工具安装状态')
        sub.add_argument('--root', type=Path, default=ROOT)
        if name == 'install-tools':
            sub.add_argument('--config', type=Path, help='安装后将工具路径保存到指定配置；省略时不写配置')
    for name in ("build", "doctor", "capture", "gui"):
        sub = commands.add_parser(name)
        sub.add_argument("--config", type=Path, default=CONFIG_PATH)
        if name in ("build", "doctor"):
            sub.add_argument("--store-pass-env", default="LC_PACKER_STORE_PASSWORD")
            sub.add_argument("--key-pass-env", default="LC_PACKER_KEY_PASSWORD")
        if name == "build":
            sub.add_argument("--allow-unverified-resources", action="store_true")
        if name == "doctor":
            sub.add_argument("--report", type=Path)
        if name == "capture":
            sub.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args(argv)
    try:
        if not args.command or args.command == "gui":
            from .gui import run_gui
            run_gui(getattr(args, "config", CONFIG_PATH))
            return 0
        if args.command in ('install-tools', 'tools-status'):
            packages = load_catalog()
            if args.command == 'install-tools':
                result = install_packages(args.root, packages=packages, log=lambda text: print(text, file=sys.stderr))
                if args.config:
                    settings = load_settings(args.config)
                    settings.update(result)
                    save_settings(args.config, settings)
            else:
                result = {'packages': [asdict(package_status(args.root, package)) for package in packages]}
        elif args.command == "compile":
            compile_source(args.path)
        elif args.command == "inspect":
            if args.template:
                result = asdict(inspect_template(args.path))
                result["payload_header"] = result["payload_header"].hex()
            else:
                project = read_project(args.path)
                result = {"source_name": project.source_name, "source_bytes": len(project.source),
                          "prop_bytes": len(project.properties or b""), "ui_bytes": len(project.ui or b""),
                          "attachment_bytes": len(project.attachments or b""), "dependencies": project.dependencies}
        else:
            settings = load_settings(args.config)
            password = os.environ.get(getattr(args, "store_pass_env", ""), "")
            key_password = os.environ.get(getattr(args, "key_pass_env", ""), "")
            if args.command == "doctor":
                report = check_environment(environment_config(settings, password, key_password))
                result = report.to_dict()
                if args.report:
                    with args.report.open("x", encoding="utf-8") as output:
                        json.dump(result, output, ensure_ascii=False, indent=2)
            elif args.command == "capture":
                result = asdict(capture_from_settings(settings, timeout=args.timeout, log=lambda text: print(text, file=sys.stderr)))
            else:
                if not password and sys.stdin.isatty():
                    password = getpass.getpass("密钥库密码: ")
                if not password:
                    raise ValueError("请通过 LC_PACKER_STORE_PASSWORD 环境变量提供密钥库密码")
                result = asdict(build_from_settings(settings, password, key_password, log=lambda text: print(text, file=sys.stderr), allow_unverified_resources=args.allow_unverified_resources))
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 2 if args.command == "doctor" and not result["build_ready"] else 0
    except ImportError as exc:
        print(f"Qt 界面组件加载失败：{exc}。请用同一 Python 运行 py -3 -m pip install -r requirements.txt", file=sys.stderr)
        return 2
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
