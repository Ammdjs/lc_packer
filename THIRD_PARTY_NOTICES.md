# 第三方组件

LC打包的 Windows EXE 内置 Python、PySide6 / Shiboken6 和所需 Qt 动态库。源码和构建脚本随完整发布包提供，可自行修改依赖并重新构建。

| 组件 | 本版使用版本 | 许可及源码 |
| --- | --- | --- |
| Python | 3.13.0 | Python Software Foundation License；[Python 源码](https://www.python.org/downloads/source/) |
| PySide6 / Shiboken6 | 6.9.1 | LGPL-3.0-only / GPL-2.0-only / GPL-3.0-only；[Qt for Python 许可](https://doc.qt.io/qtforpython-6/licenses.html)、[对应源码](https://code.qt.io/cgit/pyside/pyside-setup.git/tag/?h=v6.9.1) |
| Qt | 6.9.1 | 所用 Qt 模块采用其开源许可；[Qt 源码](https://download.qt.io/archive/qt/6.9/6.9.1/)、[Qt 第三方组件说明](https://doc.qt.io/qt-6/licenses-used-in-qt.html) |
| PyInstaller | 6.19.0 | GPL-2.0-or-later，包含生成应用的例外条款；[许可说明](https://pyinstaller.org/en/stable/license.html) |

相关许可正文位于 `licenses/`，构建时也会嵌入 EXE。Python 许可文件包含其附带组件声明；Qt 附带组件的具体声明和源码见上述 Qt 官方链接。

JDK、Android SDK、apktool、手机助手、APK 模板和用户脚本由使用者另行提供，不包含在发布包内，分别适用其原有许可。
