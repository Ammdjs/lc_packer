# LC打包 1.0.0

LC打包是面向 Windows 64 位的安卓脚本打包工具，使用 Qt 6 / PySide6 图形界面，提供工程配置、LC 捕获、签名密钥管理、环境检测和构建日志。

当前打包链路为：**MQB 工程资源 + 手机助手生成的 LC + 匹配的 APK 模板 → 重建、对齐、签名并验证 APK**。

程序尚未提供经过验证的独立源码自动编译入口。使用 MQB 时，仍需在手机助手中手动调试同一工程，再捕获生成的 LC；不能承诺无人值守完成源码编译。

## 下载与启动

从 [Releases](https://github.com/Ammdjs/lc_packer/releases) 下载 1.0.0 完整发行 ZIP，解压后直接双击根目录的 **`LC打包.exe`**。发行包已包含 Python 和 Qt 运行库，无需另行安装 Python 或 PySide6。

GitHub 仓库及其自动生成的源码 ZIP 提供源码；完整发行 ZIP 另外包含可直接运行的 EXE。Python 源码包为 [lc_packer/](lc_packer/)。

请将完整发行包解压到可写目录。EXE 将 `local-config.json`、默认工作目录和输出目录保存在自身目录中。构建工作目录请使用英文路径；输出 APK 可以使用中文文件名。`start.cmd` 也可用于启动，源码环境的运行方式见下文。

手机助手、APK 模板、JDK、Android SDK、apktool、私有工程和签名密钥均需自行准备，不随源码或完整发行 ZIP 分发。

## 使用前准备

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Windows 64 位 |
| 工程 | 自己的 MQB，以及由同一工程生成的 LC |
| 手机助手与模板 | 与 LC 格式、运行时版本相匹配的手机助手和 APK 模板 |
| Java | JDK 17，包含 `java` 和 `keytool` |
| APK 工具 | apktool 3；Android SDK build-tools 中的 `aapt`、`zipalign`、`apksigner` |
| 签名 | 自己的密钥库、实际别名及密码；也可在界面中新建 `.p12` 密钥库 |
| 可选设备工具 | ADB，仅用于设备检查；离线构建不要求连接设备 |

工具会尝试发现本机工具，请在“工具路径”页核对实际路径。已验证的工具组合包括 JDK 17、apktool 3.0.3 和 SDK build-tools 36.1.0；其他版本请先运行环境检测。

## 打包步骤

1. 在“打包配置”页选择 MQB、匹配的 APK 模板和一个尚不存在的输出文件，填写应用名称、包名、版本及图标。修改应用名称时，输出 APK 文件名会同步更新，并保留原输出目录。
2. 配置手机助手路径，点击“开始捕获新 LC”，再到手机助手中手动调试**同一工程**。也可显式选择已有 LC，但须自行确认与 MQB 对应。切换 MQB 会清空旧 LC 选择。
3. 在“编译与签名”页选择自己的密钥库，填写实际别名并输入密码。新配置的默认别名为 `lcpacker`；新建密钥时，仓库密码至少为 6 位。
4. 点击“环境检测”，逐项查看实际路径、版本、错误原因和修复建议，修复所有构建必需项。
5. 点击“构建签名 APK”，查看构建日志和输出结果。已有 APK 不会被覆盖。

请备份签名密钥、别名和密码，后续更新同一应用需要使用同一密钥。“保存配置”和环境报告导出均不保存密码。环境报告对应一次配置快照，修改配置后需重新检测。

APK 生成、签名验证与设备运行是不同的验证阶段；构建成功不代表脚本已经在设备上运行成功。环境检测为只读检查，不会安装工具或修改系统设置。

## 兼容性与已知限制

- 已完成的设备运行验证使用手机助手 **3.7.2** 生成的旧 **`0a14` LC**、**510 模板**及雷电 9 / Android 9 **Root 模拟器**。该结果只适用于这一组合，不能推广到所有手机助手或模板。
- 已知手机助手 4.6 生成的 `eflc` LC 与上述旧模板不兼容。工具会拒绝不匹配的组合；文件头相似也不能证明运行兼容。
- **Root 真机尚未实测**。非 Root 设备及其他 Android 版本的运行兼容性需自行验证。
- 模板原有 ABI 为 `armeabi-v7a`、`x86`，不能添加模板没有的架构。
- **自定义 UI、附件执行和插件运行尚未完成设备验证**。非空 UIP / ATC 默认阻止打包；可在工具页明确允许原样嵌入，但构建结果会保留警告。`Import` 依赖须在所选插件 / 命令库目录中存在。
- 模板的运行时、授权逻辑及不透明元数据保持原样。模板中的固定 provider authority 可能限制同模板多包并装；安装冲突需结合系统错误单独处理。
- Windows `aapt` 对部分中文输入路径可能失败，程序会使用英文工作目录暂存资源。

## 从源码运行

在仓库根目录使用 Python 3.11 或更高版本：

```powershell
py -3 -m pip install -r requirements.txt
py -3 -m lc_packer gui
```

[config.example.json](config.example.json) 列出了配置字段。可复制为 `local-config.json` 后填写本机路径，也可在界面中设置并保存。配置中的路径、工程、模板和密钥需由使用者提供。

图形界面之外，也可使用同一套服务的命令行入口：

```powershell
py -3 -m lc_packer inspect "工程.mqb"
py -3 -m lc_packer inspect "模板.apk" --template
py -3 -m lc_packer capture --config local-config.json --timeout 180
py -3 -m lc_packer doctor --config local-config.json --report environment-report.json
py -3 -m lc_packer build --config local-config.json
```

`capture` 返回的新 LC 路径需要写入配置的 `compiled` 字段。命令行构建从 `LC_PACKER_STORE_PASSWORD` 环境变量读取仓库密码；可交互终端也支持隐藏密码提示。独立私钥密码可通过 `LC_PACKER_KEY_PASSWORD` 提供。不要把密码写入命令参数或配置文件。检测报告导出也要求使用尚不存在的文件名。

窗口版 EXE 的命令行输出需通过日志文件保存，例如：

```powershell
.\LC打包.exe --log-file inspect.log inspect "工程.mqb"
.\LC打包.exe --self-test self-test.json
```

`--self-test` 只加载自身界面并生成自检报告，不执行设备验证。日志及自检报告均不会覆盖已有文件。

## 开发与构建 EXE

Qt 界面定义位于 [lc_packer/ui/main_window.ui](lc_packer/ui/main_window.ui)，主题位于 [lc_packer/ui/style.qss](lc_packer/ui/style.qss)。可双击 `edit_ui.cmd` 用 Qt Designer 编辑；请保留已接线控件的 `objectName` 和类型。程序直接加载 `.ui`，无需生成 Python 界面代码。

在 Windows 64 位 Python 环境中构建 EXE：

```powershell
py -3 -m pip install -r requirements-build.txt
.\build_exe.ps1 -OutputDirectory dist\1.0.0
```

输出为 `dist/1.0.0/LC打包.exe` 及使用说明。构建脚本使用 PyInstaller 打包程序、Python / Qt 运行库和界面资源，不会打包本机配置、密钥、脚本、APK 模板或外部工具。

生成根目录包含 EXE 的完整发行 ZIP：

```powershell
py -3 -m lc_packer.release --exe dist/1.0.0/LC打包.exe
```

输出为 `LC打包-v1.0.0.zip`，已有文件不会被覆盖。内容由 [发布清单](packaging/release_manifest.json) 明确列出，包含源码、界面资源、测试、构建脚本、说明和第三方许可；Git 元数据、内部工作记录、缓存、本机配置和私有文件均不纳入。第三方组件及许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

运行自动化测试：

```powershell
py -3 -m unittest discover -s tests -v
```

测试覆盖工程读取、LC 捕获、资源映射、签名配置、工具错误与密码脱敏、环境检测、命令行和 Qt 离屏界面；自动化测试不能替代具体模板与设备的运行验证。

版本变更见 [CHANGELOG](CHANGELOG.md)。
