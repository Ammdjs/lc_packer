"""Windowed EXE launcher, with explicit file output for automated verification."""
import argparse
from contextlib import ExitStack, redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import sys


def self_test(report_path):
    from PySide6.QtCore import qVersion
    from PySide6.QtWidgets import QApplication, QLabel
    from .app import CONFIG_PATH, ROOT, default_settings
    from . import __version__
    from .gui import PackerWindow, UI_DIR

    application = QApplication.instance() or QApplication([])
    application.setApplicationName("LC打包")
    application.setApplicationVersion(__version__)
    window = PackerWindow(default_settings(), CONFIG_PATH)
    try:
        window.edits["output"].setText("C:/Users/Example/Desktop/旧名称.apk")
        window.edits["app_name"].setText("测试1.2")
        application.processEvents()
        brand_name = window.required(QLabel, "brandName").text()
        output_name_sync = window.edits["output"].text() == "C:/Users/Example/Desktop/测试1.2.apk"
        result = {"ok": (window.pages.count() == 5 and len(window.edits) == len(default_settings())
                          and application.applicationName() == "LC打包" and window.windowTitle() == "LC打包"
                          and brand_name == "LC打包" and output_name_sync),
                  "frozen": bool(getattr(sys, "frozen", False)), "qt_version": qVersion(),
                  "qt_platform": application.platformName(),
                  "application_name": application.applicationName(),
                  "application_version": application.applicationVersion(),
                  "window_title": window.windowTitle(), "brand_name": brand_name,
                  "app_name": window.edits["app_name"].text(),
                  "output": window.edits["output"].text(), "output_name_sync": output_name_sync,
                  "application_root": str(ROOT), "config_path": str(CONFIG_PATH),
                  "pages": window.pages.count(), "fields": len(window.edits),
                  "window_visible": window.isVisible(),
                  "ui_exists": (UI_DIR / "main_window.ui").is_file(),
                  "stylesheet_exists": (UI_DIR / "style.qss").is_file()}
        with Path(report_path).open("x", encoding="utf-8") as output:
            json.dump(result, output, ensure_ascii=False, indent=2)
        return 0 if result["ok"] else 2
    finally:
        window.close()


def main(argv=None):
    # Windowed bootloaders set these streams to None. Keep the normal Python
    # entry point unchanged, and offer --log-file for this EXE's CLI commands.
    argv = sys.argv[1:] if argv is None else argv
    errors = io.StringIO()
    with ExitStack() as stack:
        if sys.stdin is None:
            original = sys.stdin
            sys.stdin = io.StringIO("")
            stack.callback(setattr, sys, "stdin", original)
        if sys.stdout is None:
            stack.enter_context(redirect_stdout(io.StringIO()))
        if sys.stderr is None:
            stack.enter_context(redirect_stderr(errors))
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--log-file", type=Path)
        parser.add_argument("--self-test", type=Path, metavar="REPORT")
        options, command = parser.parse_known_args(argv)
        gui_mode = not options.self_test and (not command or command[0] == "gui")
        try:
            if options.log_file:
                output = stack.enter_context(options.log_file.open("x", encoding="utf-8"))
                stack.enter_context(redirect_stdout(output))
                stack.enter_context(redirect_stderr(output))
            # Load Qt's native dependencies before restoring normal DLL search
            # paths for Java / SDK programs. The application is retained here.
            if getattr(sys, "frozen", False):
                from PySide6.QtWidgets import QApplication
                from PySide6.QtGui import QImageReader
                from . import gui
                from .runtime import prepare_external_tools
                application = QApplication.instance() or QApplication(sys.argv[:1])
                application.setApplicationName("LC打包")
                QImageReader.supportedImageFormats()
                prepare_external_tools()
            if options.self_test:
                return self_test(options.self_test)
            from .cli import main as cli_main
            result = cli_main(command)
            if result and gui_mode:
                _show_startup_error(errors.getvalue() or "界面启动失败，请检查配置文件和工具路径。")
            return result
        except Exception as exc:
            if gui_mode:
                _show_startup_error(str(exc))
            else:
                print(f"错误：{exc}", file=sys.stderr)
            return 2


def _show_startup_error(message):
    from PySide6.QtWidgets import QApplication, QMessageBox
    application = QApplication.instance() or QApplication([])
    application.setApplicationName("LC打包")
    QMessageBox.critical(None, "LC打包 启动失败", message[-6000:])


if __name__ == "__main__":
    raise SystemExit(main())
