# 第三方组件

LC打包 2.0.0 的 Windows EXE 内置 Python、PySide6 / Shiboken6、所需 Qt 动态库和 16 个旧 `0a14` APK 模板。完整发行 ZIP 还提供四个通用工具安装包及 JDK 对应源码归档。源码和构建脚本随完整发行包提供，可自行修改依赖并重新构建。

## 程序运行库与构建工具

| 组件 | 本版使用版本 | 许可及源码 |
| --- | --- | --- |
| Python | 3.13.0 | Python Software Foundation License；[Python 源码](https://www.python.org/downloads/source/) |
| PySide6 / Shiboken6 | 6.9.1 | LGPL-3.0-only / GPL-2.0-only / GPL-3.0-only；[Qt for Python 许可](https://doc.qt.io/qtforpython-6/licenses.html)、[对应源码](https://code.qt.io/cgit/pyside/pyside-setup.git/tag/?h=v6.9.1) |
| Qt | 6.9.1 | 所用 Qt 模块采用其开源许可；[Qt 源码](https://download.qt.io/archive/qt/6.9/6.9.1/)、[Qt 第三方组件说明](https://doc.qt.io/qt-6/licenses-used-in-qt.html) |
| PyInstaller | 6.19.0 | GPL-2.0-or-later，包含生成应用的例外条款；[许可说明](https://pyinstaller.org/en/stable/license.html) |

相关许可正文位于 `licenses/`，构建时也会嵌入 EXE。Python 许可文件包含其附带组件声明；Qt 附带组件的具体声明和源码见上述 Qt 官方链接。

## 随完整 ZIP 提供的通用工具

| 组件 | 版本 / 离线包 | 许可、来源与声明位置 |
| --- | --- | --- |
| Eclipse Temurin JDK 17 / HotSpot，Windows x64 | 17.0.20.1+1；`OpenJDK17U-jdk_x64_windows_hotspot_17.0.20.1_1.zip` | GPL-2.0 with Classpath Exception；[官方发布](https://github.com/adoptium/temurin17-binaries/releases/tag/jdk-17.0.20.1%2B1)、[许可说明](https://adoptium.net/docs/faq/)。安装后保留 `tools/jdk17/NOTICE`、`tools/jdk17/legal/` 及 `lib/src.zip` |
| Apktool | 3.0.3；`apktool_3.0.3.jar` | Apache-2.0 及其依赖许可；[官方发布](https://github.com/iBotPeaches/Apktool/releases/tag/v3.0.3)、[官方分发说明](https://apktool.org/docs/faq/)。JAR 内保留 `LICENSE`、`META-INF/LICENSE*`、`META-INF/NOTICE.txt` |
| Android SDK Build-Tools 必需工具 | 36.1.0；`android-build-tools-36.1.0-lc.zip` | aapt、zipalign、apksig 采用 Apache-2.0，另含第三方依赖声明；[Google 官方原包](https://dl.google.com/android/repository/build-tools_r36.1_windows.zip)、[aapt 许可](https://android.googlesource.com/platform/frameworks/base/+/refs/heads/main/tools/aapt/Android.bp)、[zipalign 许可](https://android.googlesource.com/platform/build/+/refs/heads/main/tools/zipalign/Android.bp)、[apksig 许可](https://android.googlesource.com/platform/tools/apksig/+/refs/heads/main/LICENSE)。完整 `NOTICE.txt` 与 `source.properties` 随包保留 |
| Android SDK Platform-Tools 的 ADB 工具 | 37.0.1；`android-platform-tools-37.0.1-lc.zip` | ADB 采用 Apache-2.0，另含第三方依赖声明；[Google 官方原包](https://dl.google.com/android/repository/platform-tools_r37.0.1-win.zip)、[ADB 许可](https://android.googlesource.com/platform/packages/modules/adb/+/refs/heads/main/NOTICE)、[版本说明](https://developer.android.com/tools/releases/platform-tools)。完整 `NOTICE.txt` 与 `source.properties` 随包保留 |

两个 SDK 包由经过官方校验的原 ZIP 提取所需文件重新归档，二进制内容不修改。Build-Tools 包仅含 `aapt.exe`、`zipalign.exe`、`lib/apksigner.jar` 及声明文件；ADB 包仅含 `adb.exe`、`AdbWinApi.dll`、`AdbWinUsbApi.dll` 及声明文件。安装后的许可分别位于 `tools/android-build-tools/NOTICE.txt` 与 `tools/platform-tools/NOTICE.txt`。Google 原包的版本、大小和校验值来自 [官方仓库目录](https://dl.google.com/android/repository/repository2-3.xml)；[SDK 协议](https://developer.android.com/studio/terms)第 3.5 条说明开源组件的使用、复制与分发适用其自身开源许可。

JDK 同版本对应源码随完整发行包放在 **`tool-packages/OpenJDK17U-jdk-sources_17.0.20.1_1.tar.gz`**，无需安装。其 [官方源码归档](https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.20.1%2B1/OpenJDK17U-jdk-sources_17.0.20.1_1.tar.gz)大小为 109,272,338 字节，SHA-256 为 `21e2a065d244ab048e737f21af5d1fc74daaeb6707de36477ead8db1dca71214`。此归档与 JDK 二进制一并提供，`lib/src.zip` 也原样保留。

工具包名称、版本、来源和 SHA-256 记录在 [工具清单](lc_packer/data/tools_catalog.json)。单独 EXE 不内嵌这些外置工具包，源码仓库也不保存工具原包；完整发行 ZIP 才提供全部离线安装文件。GitHub Release 的完整包为 `LC-Packer-v2.0.0-full.zip`，单独程序为 `LC-Packer-v2.0.0.exe`；本地发布命令默认生成 `LC打包-v2.0.0.zip`。源码构建时的工具取回脚本从官方来源联网下载，最终用户安装随包工具无需联网。

## 第三方模板与用户资源

16 个内置模板的名称、文件及校验值见 [模板清单](lc_packer/data/templates_catalog.json)，均为旧 `0a14` 格式。原始包名、provider、图标及 DEX / native 运行时来自第三方模板，相关权利归原权利人。本版清理样例工程数据、示例更新 URL 和注入任务配置，原运行时二进制、授权逻辑和第三方授权组件配置保持原样（包括运行时已有的 RSA 配置常量）；嵌套的 `assets/inject/test.apk` 作为必要运行组件保留。模板不包含用户签名私钥文件。构建阶段按用户配置替换应用信息及相关身份字段。模板作为构建资源提供，不作为独立应用运行，外部模板仍可自行选择。

默认 `root-510` 沿用原 510（支持雷电9）模板与手机助手 3.7.2 旧 LC、雷电 9 / Android 9 Root 模拟器组合的设备验证记录；清理后的默认模板本次仅完成构建验证。其余 15 个模板未实测，免 ROOT、企业版、热更、自启等名称来自原文件名，不代表对应功能已通过设备验证。

手机助手不随包提供。用户工程、脚本、图标、插件、外部模板及签名密钥由使用者提供，分别适用其原有许可。
