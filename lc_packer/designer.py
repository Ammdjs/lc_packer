"""Open our editable form in the Designer bundled with the selected PySide6."""
from pathlib import Path
import os
import subprocess
import sys


def main():
    try:
        import PySide6
        executable = Path(PySide6.__file__).parent / ("designer.exe" if os.name == "nt" else "designer")
        if not executable.is_file():
            raise OSError(f"未找到 Qt Designer：{executable}")
        form = Path(__file__).with_name("ui") / "main_window.ui"
        if not form.is_file():
            raise OSError(f"未找到界面源文件：{form}")
        # Visible Designer is intentional when the user runs edit_ui.cmd.
        subprocess.Popen([str(executable), str(form)],
                         creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        return 0
    except (ImportError, OSError) as exc:
        print(f"无法启动 Qt Designer：{exc}。请安装 requirements.txt 中的 PySide6。", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
