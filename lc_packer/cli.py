"""Command line entry point for the same services used by the desktop form."""
import argparse
from dataclasses import asdict
import getpass
import json
import os
from pathlib import Path
import sys

from .app import CONFIG_PATH, build_from_settings, capture_from_settings, environment_config, load_settings
from .compiler import compile_source
from .diagnostics import check_environment
from .project import read_project
from .templates import inspect_template


def main(argv=None):
    parser = argparse.ArgumentParser(description="LC打包 — MQB 资源 + 显式 LC 打包")
    commands = parser.add_subparsers(dest="command")
    inspect = commands.add_parser("inspect", help="检查 MQB 或模板，不执行脚本")
    inspect.add_argument("path", type=Path)
    inspect.add_argument("--template", action="store_true")
    compile_cmd = commands.add_parser("compile", help="显示自动编译入口状态")
    compile_cmd.add_argument("path", type=Path)
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
        if args.command == "compile":
            compile_source(args.path)
        if args.command == "inspect":
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
