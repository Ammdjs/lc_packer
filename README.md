# LC打包 2.0.1

LC打包是面向 Windows 64 位的安卓脚本打包工具，使用 Qt 6 / PySide6 图形界面，提供工程配置、LC 捕获、签名密钥管理、离线工具安装、环境检测和构建日志。

当前打包链路为：**MQ / MQB 工程资源 + 手机助手生成的 LC + 匹配的 APK 模板 → 重建、对齐、签名并验证 APK**。

2.0.1 内置 **16 个旧 `0a14` APK 模板**，可在界面中选择并查看说明；默认使用 **510 Root 模板**。只有原 510 模板与旧 LC 的指定组合曾经过设备验证，其余 15 个模板未实测。完整发行 ZIP 另附四个通用工具安装包。手机助手需自行准备，仍需通过手机助手 **3.7.2** 手动调试同一工程并捕获 LC；程序尚未提供经过验证的独立源码自动编译入口。

## 下载与启动

从 [Releases](https://github.com/Ammdjs/lc_packer/releases) 下载 **`LC-Packer-v2.0.1-full.zip` 完整发行包**，全部解压后双击根目录的 **`LC打包.exe`**。发行包已包含 Python 和 Qt 运行库，无需另行安装 Python 或 PySide6。

| 下载内容 | 包含内容 |
| --- | --- |
| `LC-Packer-v2.0.1-full.zip` | EXE、16 个内置模板、源码与说明、许可文件，以及 `tool-packages/` 中的四个工具包和 JDK 对应源码归档，可离线安装工具 |
| `LC-Packer-v2.0.1.exe` | 程序运行库和全部 16 个内置模板；不含外置 `tool-packages/`，离线安装工具请下载完整 ZIP |
| 仓库及 GitHub 自动生成的源码 ZIP | 项目源码与已纳入仓库的资源，不含工具安装原包；构建完整发行包前需取回锁定的工具包 |

请解压到可写目录。EXE 将 `local-config.json`、默认工作目录和输出目录保存在自身目录中。构建工作目录请使用英文路径；输出 APK 可以使用中文文件名。`start.cmd` 也可用于启动，源码运行方式见下文。

手机助手、私有工程、用户脚本和签名密钥不随发行包提供。

## 安装随包工具

进入“工具路径”页，点击 **“安装缺失工具 / 使用随包工具”**。程序校验本地包的大小与 SHA-256，再安装到程序目录下的 `tools/`；无需联网。可用“刷新状态”重新检查安装情况，安装过程中可点击“取消安装”；已成功安装的工具会保留。

| 工具 | 用途与必需场景 | 随包版本 | 默认位置 |
| --- | --- | --- | --- |
| Java / JDK 17 | `java` 运行 Apktool、apksigner，构建必需；同一 JDK 的 `keytool` 在新建签名密钥时必需 | Temurin 17.0.20.1+1 | `tools/jdk17/bin/java.exe`、`tools/jdk17/bin/keytool.exe` |
| Apktool | 解码模板并重建 APK，构建必需 | 3.0.3 | `tools/apktool/apktool.jar` |
| aapt | 读取 APK 包信息、资源和 ABI，构建校验必需 | Build-Tools 36.1.0 | `tools/android-build-tools/aapt.exe` |
| zipalign | 在签名前对齐 APK 内部文件，构建必需 | Build-Tools 36.1.0 | `tools/android-build-tools/zipalign.exe` |
| apksigner | 签名并验证 APK 签名，构建必需 | Build-Tools 36.1.0 | `tools/android-build-tools/lib/apksigner.jar` |
| ADB | 连接设备并检查设备状态、ABI；设备检测时必需，仅构建 APK 时可选 | Platform-Tools 37.0.1 | `tools/platform-tools/adb.exe` |

上述工具由 JDK、Apktool、Build-Tools 和 Platform-Tools 四个安装包提供。“工具路径”页的六个工具组和四项安装状态均显示用途、必需场景、随包版本与默认位置。

安装完成后，界面自动绑定六个工具路径，保存普通配置并重新运行环境检测。密码不会写入配置。也可手动选择已有工具；手机助手路径仍需自行选择。“刷新状态”按安装记录核对全部受管文件的大小和 SHA-256，缺失或损坏时可点击安装按钮修复。修复会将旧目录完整保留为 `tools/.lc-backup-*`，确认无用后可自行清理。

安装与状态刷新不修改系统环境变量或注册表，不启动 ADB，也不要求管理员权限；目标目录必须可写。JDK 对应源码位于 `tool-packages/OpenJDK17U-jdk-sources_17.0.20.1_1.tar.gz`，用于随包提供源码，无需安装或解压。工具来源和许可见 [第三方组件说明](THIRD_PARTY_NOTICES.md)。

## 使用前准备

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Windows 64 位，建议 Windows 10 / 11 |
| 工程 | 自己的 MQ 脚本或 MQB 工程包，以及由同一工程生成的 LC |
| 手机助手 | 自行准备；当前已验证的旧 `0a14` LC 来自手机助手 3.7.2 |
| APK 模板 | 从 16 个内置模板中选择，默认 510 Root；也可选择格式和运行时匹配的外部模板 |
| 通用工具 | 在“工具路径”页安装随包工具，或手动配置兼容工具 |
| 签名 | 自己的密钥库、实际别名及密码；也可在界面中新建 `.p12` 密钥库 |
| 设备 | ADB 用于设备检查；离线构建无需连接设备 |

原生 `.mq` 可直接导入，无需改后缀或手工压缩。程序会读取同目录、同名的 `.prop`、`.uip`、`.atc`；旁边其他脚本的文件不会混入。配套文件不存在时按缺省资源处理。已有 ZIP 格式 `.mqb` 仍可使用。

## 打包步骤

1. 在“工具路径”页安装随包工具或核对已有路径，并选择自行准备的手机助手。
2. 在“打包配置”页选择 MQ 脚本或 MQB 工程包，在下拉列表选择内置模板并阅读验证说明，或浏览匹配的外部模板。指定一个尚不存在的输出文件，填写应用名称、包名、版本及图标。修改应用名称时，输出 APK 文件名会同步更新，并保留原输出目录。
3. 点击“开始捕获新 LC”，再到手机助手 3.7.2 中手动调试**同一工程**。也可显式选择已有 LC，但须自行确认与所选 MQ / MQB 工程对应。切换工程会清空旧 LC 选择。
4. 在“编译与签名”页选择自己的密钥库，填写实际别名并输入密码。新配置的默认别名为 `lcpacker`；新建密钥时，仓库密码至少为 6 位。
5. 点击“环境检测”，查看实际路径、版本、错误原因和修复建议，修复所有构建必需项。
6. 点击“构建签名 APK”，查看构建日志和输出结果。已有 APK 不会被覆盖。

请备份签名密钥、别名和密码，后续更新同一应用需要使用同一密钥。“保存配置”和环境报告导出均不保存密码。环境报告对应一次配置快照，修改配置后需重新检测。

APK 生成、签名验证与设备运行是不同的验证阶段；构建成功不代表脚本已经在设备上运行成功。环境检测为只读检查，工具安装通过“工具路径”页单独执行。

## 模板与兼容性

“打包配置”页提供以下 16 个模板，名称与 [模板清单](lc_packer/data/templates_catalog.json) 一致。**暂停、自启、自运行、热更、免 ROOT、标准版、精简版和企业版等标签来自原模板文件名，不表示这些功能已完成运行验证。** 已清除示例更新地址，热更功能需自行配置和验证。

| 模板名称 | 标识 |
| --- | --- |
| 510 · 暂停/热更/不自启 · 支持雷电9（默认） | `root-510` |
| 501 · 暂停/自启/自运行 | `root-501-autostart-autorun` |
| 510 · 暂停/热更/不自启 | `root-510-standard` |
| 560 · 1668063481 | `root-560-1668063481` |
| 560 · 无开机自启 | `root-560-no-boot` |
| 标准版 · 开机自启/默认自启/热更 | `standard-boot-default-hot-update` |
| 标准版 · 开机自启/默认自启 | `standard-boot-default` |
| 标准版 · 开机自启/热更 | `standard-boot-hot-update` |
| 标准版 · 开机自启 | `standard-boot` |
| 标准版 · 免ROOT/开机自启/默认自启 | `standard-no-root-boot-default` |
| 标准版 · 免ROOT/开机自启 | `standard-no-root-boot` |
| 精简版 · 免ROOT | `lite-no-root` |
| 精简版 · 热更 | `lite-hot-update` |
| 精简版 | `lite` |
| 企业版 · 开机自启/默认自启 | `enterprise-boot-default` |
| 企业版 · 开机自启 | `enterprise-boot` |

- **只有默认 `root-510` 沿用原组合的设备验证记录**：手机助手 **3.7.2** 生成的旧 **`0a14` LC**、原 **510（支持雷电9）模板**及雷电 9 / Android 9 **Root 模拟器**。清理后的默认模板本次仅完成构建验证，其余 **15 个模板均未经过设备运行验证**。界面中的“已验证”标记仅指上述原组合，详细范围以模板说明为准。
- 16 个内置模板均已清理样例工程的 LC、ATC、UI、属性和信息数据，清空示例更新 URL 与注入任务配置；原有 DEX、native 运行时及第三方授权组件配置保持原样，包括原有 RSA 配置常量。嵌套的 `assets/inject/test.apk` 作为必要运行组件保留；模板不包含用户签名私钥文件。
- 模板中的原包名、provider 和图标来自第三方模板本身。构建阶段按所填应用信息替换名称、包名、版本、相关身份字段及选定图标；模板用于 APK 重建，不作为独立应用运行。
- 已知手机助手 4.6 生成的 `eflc` LC 与上述旧模板不兼容。工具会拒绝不匹配的组合；文件头相似也不能证明运行兼容。
- **Root 真机尚未实测**。非 Root 设备及其他 Android 版本的运行兼容性需自行验证。
- 默认 `root-510` 的 ABI 为 `armeabi-v7a`、`x86`；其他模板的实际 ABI 可在界面检查，不能添加模板没有的架构。所有内置模板均为旧 `0a14` 格式，外部模板也需匹配 LC 格式和运行时版本。
- **自定义 UI、附件执行和插件运行尚未完成设备验证**。非空 UIP / ATC 默认阻止打包；可在工具页明确允许原样嵌入，但构建结果会保留警告。`Import` 依赖须在所选插件 / 命令库目录中存在。
- Windows `aapt` 对部分中文输入路径可能失败，程序会使用英文工作目录暂存资源。

## 从源码运行

在仓库根目录使用 Python 3.11 或更高版本：

```powershell
py -3 -m pip install -r requirements.txt
py -3 -m lc_packer gui
```

[config.example.json](config.example.json) 列出了配置字段。可复制为 `local-config.json` 后填写本机路径，也可在界面中设置并保存。手机助手、工程和签名密钥需自行提供；内置模板可直接使用。

图形界面之外，也可使用同一套服务的命令行入口：

```powershell
py -3 -m lc_packer tools-status
py -3 -m lc_packer install-tools --config local-config.json
py -3 -m lc_packer inspect "工程.mq"
py -3 -m lc_packer capture --config local-config.json --timeout 180
py -3 -m lc_packer doctor --config local-config.json --report environment-report.json
py -3 -m lc_packer build --config local-config.json
```

`install-tools` 和 `tools-status` 的 `--root` 可指定程序根目录，默认使用程序目录；安装包从该目录的 `tool-packages/` 读取，工具安装到其 `tools/` 下。`install-tools` 只有显式传入 `--config` 才保存工具路径；CLI 安装后可按上例单独运行 `doctor`。

`capture` 返回的新 LC 路径需要写入配置的 `compiled` 字段。命令行构建从 `LC_PACKER_STORE_PASSWORD` 环境变量读取仓库密码；可交互终端也支持隐藏密码提示。独立私钥密码可通过 `LC_PACKER_KEY_PASSWORD` 提供。不要把密码写入命令参数或配置文件。检测报告导出要求使用尚不存在的文件名。

窗口版 EXE 的命令行输出需通过 `--log-file` 保存，例如：

```powershell
.\LC打包.exe --log-file tools-status.log tools-status
.\LC打包.exe --log-file install-tools.log install-tools --config local-config.json
.\LC打包.exe --log-file doctor.log doctor --config local-config.json --report environment-report.json
.\LC打包.exe --self-test self-test.json
```

`--self-test` 只加载自身界面并生成自检报告，不执行设备验证。日志及自检报告均不会覆盖已有文件。

## 开发与构建 EXE

Qt 界面定义位于 [lc_packer/ui/main_window.ui](lc_packer/ui/main_window.ui)，主题位于 [lc_packer/ui/style.qss](lc_packer/ui/style.qss)。可双击 `edit_ui.cmd` 用 Qt Designer 编辑；请保留已接线控件的 `objectName` 和类型。程序直接加载 `.ui`，无需生成 Python 界面代码。

在 Windows 64 位 Python 环境中取回工具包并构建：

```powershell
py -3 -m pip install -r requirements-build.txt
py -3 packaging/fetch_tool_packages.py
.\build_exe.ps1 -OutputDirectory dist\2.0.1
py -3 -m lc_packer.release --exe dist/2.0.1/LC打包.exe
```

取回脚本需要联网，按 [工具清单](lc_packer/data/tools_catalog.json) 从官方来源下载并严格校验锁定版本，生成保留完整 NOTICE 和版本信息的最小 SDK 工具包。仓库不保存这些工具原包。该步骤用于准备发行包；最终用户从完整 ZIP 安装工具无需联网。

EXE 输出为 `dist/2.0.1/LC打包.exe`，内置 Python / Qt、界面资源、工具清单与全部 16 个模板；外置工具包位于发行根目录的 `tool-packages/`。发布命令在本地默认生成 **`LC打包-v2.0.1.zip`**，已有文件不会被覆盖。GitHub Release 中的完整包命名为 **`LC-Packer-v2.0.1-full.zip`**，单独 EXE 命名为 **`LC-Packer-v2.0.1.exe`**；完整包内的程序名仍为 `LC打包.exe`。

发行内容由 [发布清单](packaging/release_manifest.json) 明确列出。Git 元数据、内部工作记录、缓存、本机配置、用户工程及签名密钥不纳入发行包。第三方组件及许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

运行自动化测试：

```powershell
py -3 -m unittest discover -s tests -v
```

测试覆盖工程读取、LC 捕获、资源映射、签名配置、离线工具安装、环境检测、命令行和 Qt 离屏界面；自动化测试不能替代具体模板与设备的运行验证。

版本变更见 [CHANGELOG](CHANGELOG.md)。
