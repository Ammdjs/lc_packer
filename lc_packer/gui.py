"""Qt Designer frontend for the existing shared packer services."""
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import threading

from PySide6.QtCore import QFile, QIODevice, QObject, QThread, QTimer, Qt, Signal, Slot, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QFontDatabase, QPixmap
from PySide6.QtUiTools import QUiLoader
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QHeaderView,
                              QFileDialog, QLabel, QLineEdit, QListWidget, QMainWindow,
                              QPlainTextEdit, QProgressBar, QPushButton, QSplitter,
                              QSizePolicy, QStackedWidget, QStyle, QTableWidget, QTableWidgetItem,
                              QHBoxLayout)

from .app import (CONFIG_PATH, FIELDS, ROOT, build_from_settings, capture_from_settings,
                  create_keystore, environment_config, load_settings, output_path_for_name, save_settings)
from .diagnostics import check_environment
from . import __version__
from .builtin_templates import load_templates, template_path
from .templates import inspect_template
from .toolchain import ToolInstallError, install_packages, load_catalog, package_status

UI_DIR = Path(__file__).with_name("ui")
PAGE_TITLES = ("打包配置", "编译与签名", "工具路径", "环境检测", "构建日志")
PAGE_HINTS = ("导入 MQB 工程，配置应用并生成已签名 APK", "使用手机助手的新编译产物，并配置自己的 APK 签名",
              "安装随包工具，或选择本机工具、插件目录及用于验证的设备", "查看实际路径、版本、错误原因和修复建议",
              "跟踪真实构建阶段，定位失败原因")
CHECK_TITLES = {"python": "Python 运行时", "qt": "Qt / PySide6 界面", "java": "Java / JDK",
                "apktool": "APK 构建工具", "aapt": "APK 资源检查", "zipalign": "APK 对齐工具",
                "apksigner": "APK 签名工具", "assistant": "手机助手版本", "template": "模板与 ABI",
                "compiled": "编译文件", "compatibility": "编译文件与模板匹配", "signing": "签名配置",
                "workspace": "构建目录", "output": "输出路径", "automatic_compilation": "自动源码编译入口",
                "adb": "ADB 工具与服务", "device": "设备连接状态", "device_abi": "设备 ABI 匹配"}
CHECK_STATUS = {"pass": "通过", "warning": "提示", "error": "错误"}
STAGES = {"decode-template": "解码 APK 模板", "build-apk": "构建 APK", "zipalign": "对齐 APK",
          "sign": "签名 APK", "verify-signature": "验证 APK 签名", "verify-metadata": "核对包信息与脚本",
          "create-keystore": "创建签名密钥"}
TOOL_STATES = {"installed": "已安装", "missing": "未安装", "broken": "需要修复",
               "package_missing": "缺少离线包", "error": "检查失败"}
TOOL_FIELDS = ("java", "apktool", "aapt", "zipalign", "apksigner", "adb")
TOOL_HELP = {
    "java": ("jdk17", "jdk17", "bin/java.exe",
             "java 运行 apktool 与 apksigner，构建必需；同一 JDK 的 keytool 创建签名密钥，新建密钥时必需。"),
    "apktool": ("apktool", "apktool", "apktool.jar", "解码模板并重建 APK，构建必需。"),
    "aapt": ("build-tools", "android-build-tools", "aapt.exe", "读取 APK 包信息、资源与 ABI，构建校验必需。"),
    "zipalign": ("build-tools", "android-build-tools", "zipalign.exe", "在签名前对齐 APK 内部文件，构建必需。"),
    "apksigner": ("build-tools", "android-build-tools", "lib/apksigner.jar", "为 APK 签名并验证签名，构建必需。"),
    "adb": ("platform-tools", "platform-tools", "adb.exe", "连接设备、查询设备 ABI 与连接状态；设备检测时必需，仅构建 APK 时可选。"),
}
PACKAGE_HELP = {
    "jdk17": "java 运行 apktool / apksigner，构建必需；keytool 创建签名密钥，新建密钥时必需。",
    "apktool": "解码 APK 模板并重建 APK，构建必需。",
    "build-tools": "aapt 读取包信息与 ABI，zipalign 对齐 APK，apksigner 签名及验证；构建必需。",
    "platform-tools": "adb 连接设备并检查设备 ABI；设备检测时必需，仅构建 APK 时可选。",
}


class _Worker(QObject):
    progress = Signal(str)
    result = Signal(str, object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, action, secrets):
        super().__init__()
        self.action = action
        self.secrets = tuple(secret for secret in secrets if secret)

    def clean(self, text):
        text = str(text)
        for secret in sorted(self.secrets, key=len, reverse=True):
            text = text.replace(secret, "[REDACTED]")
        return text

    @Slot()
    def run(self):
        try:
            kind, result = self.action(lambda text: self.progress.emit(self.clean(text)))
            self.result.emit(kind, result)
        except Exception as exc:
            self.failed.emit(self.clean(exc))
        finally:
            self.secrets = ()
            self.finished.emit()


class PackerWindow(QMainWindow):
    """The Designer form owns appearance; this class connects real actions."""

    def __init__(self, settings, config_path=CONFIG_PATH):
        super().__init__()
        # Qt's Windows offscreen plugin has no system font database. Register a
        # local system font for the same readable form in previews and tests.
        if not QFontDatabase.families() and os.name == "nt":
            font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc"
            if font.is_file():
                QFontDatabase.addApplicationFont(str(font))
        self.config_path = Path(config_path)
        self.busy = False
        self.source_revision = 0
        self.settings_revision = 0
        self.report_revision = -1
        self.report = None
        self.cancel = threading.Event()
        self._thread = self._worker = None
        self._action_name = ""
        self._tool_status_loaded = False
        self._pending_environment_check = False
        self._loading_settings = False
        form_file = QFile(str(UI_DIR / "main_window.ui"))
        if not form_file.open(QIODevice.OpenModeFlag.ReadOnly):
            raise RuntimeError(f"无法读取 Qt 界面文件：{form_file.fileName()}")
        loader = QUiLoader()
        try:
            form = loader.load(form_file, self)
        finally:
            form_file.close()
        if form is None:
            raise RuntimeError(f"Qt 界面加载失败：{loader.errorString()}")
        self.setCentralWidget(form)
        self.setWindowTitle("LC打包")
        self.resize(1280, 900)
        self.setMinimumSize(960, 660)
        self.setFont(QFont("Microsoft YaHei UI", 10))
        self.setStyleSheet((UI_DIR / "style.qss").read_text(encoding="utf-8"))
        self.edits = {name: self.required(QLineEdit, name + "Edit") for name in FIELDS}
        for name, edit in self.edits.items():
            edit.setText(str(settings.get(name, "")))
        self.password = self.required(QLineEdit, "store_passwordEdit")
        self.key_password = self.required(QLineEdit, "key_passwordEdit")
        self.pages = self.required(QStackedWidget, "pages")
        self.navigation = self.required(QListWidget, "navigation")
        self.logs = self.required(QPlainTextEdit, "logsEdit")
        self.details = self.required(QPlainTextEdit, "checkDetails")
        self.checks = self.required(QTableWidget, "checksTable")
        self.build_button = self.required(QPushButton, "buildButton")
        self.capture_button = self.required(QPushButton, "captureButton")
        self.progress = self.required(QProgressBar, "taskProgress")
        self.allow_resources = self.required(QCheckBox, "allowResourcesCheck")
        self.status_label = self.required(QLabel, "statusLabel")
        self.summary_label = self.required(QLabel, "envSummary")
        self.tools_table = self.required(QTableWidget, "toolsTable")
        self.tools_hint = self.required(QLabel, "toolsStatusHint")
        self.template_combo = self.required(QComboBox, "builtinTemplateCombo")
        self.builtin_templates = ()
        self.cancel_install = self.required(QPushButton, "cancelInstallButton")
        self.cancel_install.setEnabled(False)
        self.required(QLabel, "toolsInstallLocation").setText(
            f"安装位置：{ROOT / 'tools'}\n随程序目录保存；如需更换位置，请将整个程序目录移动到可写位置。")
        self.tools_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tools_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tools_table.setWordWrap(False)
        self.tools_table.verticalHeader().hide()
        tool_header = self.tools_table.horizontalHeader()
        for column in (0, 1, 2):
            tool_header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        for column in (3, 4):
            tool_header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        self.progress.hide()
        self.required(QHBoxLayout, "headerLayout").setStretch(0, 1)
        self.required(QHBoxLayout, "footerLayout").setStretch(0, 1)
        for name in ("loadConfigButton", "saveButton", "doctorButton", "revealButton", "buildButton"):
            self.required(QPushButton, name).setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.logs.setFont(QFont("Consolas", 10))
        self.logs.setMaximumBlockCount(5000)
        self.checks.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.checks.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.checks.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.checks.setAlternatingRowColors(True)
        self.checks.verticalHeader().hide()
        self.checks.verticalHeader().setDefaultSectionSize(34)
        header = self.checks.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.required(QSplitter, "environmentSplitter").setSizes([390, 200])
        self.navigation.currentRowChanged.connect(self.select_page)
        self.navigation.setCurrentRow(0)
        self._actions = [self.build_button, self.capture_button]
        self._wire("buildButton", self.build)
        self._wire("captureButton", self.capture)
        self._wire("doctorButton", self.doctor, busy=True)
        self._wire("rerunDoctorButton", self.doctor, busy=True)
        self._wire("newKeyButton", self.new_key, busy=True)
        self._wire("inspectTemplateButton", self.inspect_selected_template, busy=True)
        self._wire("useBuiltinTemplateButton", self.use_builtin_template)
        self._wire("cancelCaptureButton", self.cancel.set)
        self._wire("installToolsButton", self.install_tools, busy=True)
        self._wire("refreshToolsButton", self.refresh_tools, busy=True)
        self._wire("cancelInstallButton", self.cancel.set)
        self._wire("saveButton", self.save)
        self._wire("loadConfigButton", self.load_config)
        self._wire("revealButton", self.reveal)
        self._wire("exportReportButton", self.export_report)
        self._wire("exportLogsButton", self.export_logs)
        self._wire("clearLogsButton", self.logs.clear)
        for key in FIELDS:
            browse = self.findChild(QPushButton, key + "Browse")
            if browse:
                browse.clicked.connect(lambda checked=False, name=key: self.choose(name))
            self.edits[key].textChanged.connect(self._settings_changed)
        self.password.textChanged.connect(self._settings_changed)
        self.key_password.textChanged.connect(self._settings_changed)
        self.edits["source"].textChanged.connect(self._source_changed)
        self.edits["app_name"].textChanged.connect(self._app_name_changed)
        self.edits["template"].textChanged.connect(lambda: self.required(QLabel, "templateSummary").setText("模板已变化，请重新检查"))
        self.edits["template"].textChanged.connect(self.sync_builtin_template)
        self.template_combo.currentIndexChanged.connect(self.use_builtin_template)
        for key in ("app_name", "package_name", "version_name", "version_code", "icon"):
            self.edits[key].textChanged.connect(self.refresh_preview)
        self.checks.itemSelectionChanged.connect(self.show_check)
        self._set_icons()
        self.refresh_preview()
        self.prepare_builtin_templates()
        self.prepare_tools_catalog()

    def required(self, cls, name):
        widget = self.findChild(cls, name)
        if widget is None:
            raise RuntimeError(f"Qt Designer 控件缺失或类型不符：{name}")
        return widget

    def _wire(self, name, action, busy=False):
        button = self.required(QPushButton, name)
        button.clicked.connect(action)
        if busy:
            self._actions.append(button)

    def _set_icons(self):
        icons = {"buildButton": QStyle.StandardPixmap.SP_MediaPlay,
                 "doctorButton": QStyle.StandardPixmap.SP_DialogApplyButton,
                 "saveButton": QStyle.StandardPixmap.SP_DialogSaveButton,
                 "loadConfigButton": QStyle.StandardPixmap.SP_DialogOpenButton,
                 "revealButton": QStyle.StandardPixmap.SP_DirOpenIcon}
        for name, icon in icons.items():
            self.required(QPushButton, name).setIcon(self.style().standardIcon(icon))

    def settings(self):
        return {name: edit.text().strip() for name, edit in self.edits.items()}

    @Slot()
    def _settings_changed(self):
        self.settings_revision += 1
        if self.report:
            self.summary_label.setText("配置已变化；当前表格为上次检测快照，请重新检测")

    @Slot()
    def _source_changed(self):
        self.source_revision += 1
        self.edits["compiled"].clear()
        self.set_status("工程已变化，请重新选择或捕获对应 LC", "warning")

    @Slot()
    def _app_name_changed(self):
        if not self._loading_settings:
            output = self.edits["output"]
            output.setText(output_path_for_name(output.text(), self.edits["app_name"].text()))

    @Slot(int)
    def select_page(self, index):
        if 0 <= index < self.pages.count():
            self.pages.setCurrentIndex(index)
            self.required(QLabel, "pageTitle").setText(PAGE_TITLES[index])
            self.required(QLabel, "pageSubtitle").setText(PAGE_HINTS[index])
            if index == 2 and not self._tool_status_loaded and not self.busy:
                self.refresh_tools()

    def prepare_builtin_templates(self):
        """Populate metadata without changing a saved external template path."""
        self.template_combo.blockSignals(True)
        try:
            self.builtin_templates = load_templates()
            for item in self.builtin_templates:
                state = "已验证" if item["verified"] else "未实测"
                self.template_combo.addItem(f"{item['label']} · {state}", item["id"])
            self.template_combo.setCurrentIndex(-1)
        except (OSError, ValueError) as exc:
            self.required(QLabel, "builtinTemplateDescription").setText(f"无法读取内置模板目录：{exc}；仍可浏览外部模板。")
            self.required(QPushButton, "useBuiltinTemplateButton").setEnabled(False)
            return
        finally:
            self.template_combo.blockSignals(False)
        self.sync_builtin_template()

    @Slot()
    def sync_builtin_template(self):
        selected = self.edits["template"].text().strip()
        index = next((index for index, item in enumerate(self.builtin_templates)
                      if selected and Path(selected).resolve() ==
                      (Path(__file__).parent / "assets/templates" / item["file"]).resolve()), -1)
        self.template_combo.blockSignals(True)
        self.template_combo.setCurrentIndex(index)
        self.template_combo.blockSignals(False)
        self.required(QPushButton, "useBuiltinTemplateButton").setEnabled(index >= 0)
        label = self.required(QLabel, "builtinTemplateDescription")
        if index < 0:
            label.setText("外部模板：保留当前路径。运行兼容性未实测，请先检查模板、LC 格式与设备 ABI。"
                          if selected else "请选择内置模板，或浏览外部 APK / APKM 模板。")
            return
        item = self.builtin_templates[index]
        scope = ("已验证记录（此前）：旧 510 模板 + 3.7.2 LC，在雷电 9 运行；本次清理后仅构建验证，其他设备与功能未实测。"
                 if item["verified"] else "未实测：名称特征依据原文件名，不代表运行兼容性，请先验证目标设备。")
        label.setText(f"{scope}\n{item['label']} · {item['file']}\n{item['description']}\n"
                      f"模板 ABI：{', '.join(item['abis']) or '未识别'}；脚本格式：{item['format']}。")

    @Slot()
    def use_builtin_template(self):
        identifier = self.template_combo.currentData()
        if identifier is None:
            return
        try:
            path = template_path(identifier)
        except (OSError, ValueError) as exc:
            self.required(QLabel, "builtinTemplateDescription").setText(f"内置模板不可用：{exc}；可重新选用或浏览外部模板。")
            self.required(QPushButton, "useBuiltinTemplateButton").setEnabled(True)
            self.set_status("内置模板校验失败，请查看模板说明", "error")
            return
        self.edits["template"].setText(str(path))
        self.sync_builtin_template()
        self.set_status("已选用内置模板；请检查模板与 LC 格式及目标设备 ABI")

    def set_tool_help(self, packages):
        catalog = {package.id: package for package in packages}
        for field, (package_id, default_target, suffix, purpose) in TOOL_HELP.items():
            package = catalog.get(package_id)
            target = package.target if package else default_target
            version = package.version if package else "清单不可用"
            help_text = f"用途：{purpose}\n随包版本：{version}；默认位置：{ROOT / 'tools' / target / suffix}"
            if field == "java":
                help_text += f"\nkeytool 默认位置：{ROOT / 'tools' / target / 'bin/keytool.exe'}"
            self.required(QLabel, field + "Help").setText(help_text)
            for widget in (self.edits[field], self.required(QLabel, field + "Label"),
                           self.required(QPushButton, field + "Browse")):
                widget.setToolTip(help_text)

    def package_help(self, identifier, version, path):
        purpose = PACKAGE_HELP.get(identifier, "按工具清单提供构建或设备检测功能。")
        return f"用途：{purpose}\n随包版本：{version}；默认位置：{path}"

    def prepare_tools_catalog(self):
        """List metadata without hashing tool files on the GUI thread."""
        try:
            packages = load_catalog()
            self.set_tool_help(packages)
            rows = [(pkg.label, pkg.version, "待检查", str(ROOT / "tools" / pkg.target),
                     self.package_help(pkg.id, pkg.version, ROOT / "tools" / pkg.target) +
                     "\n进入工具页或点击刷新检查安装状态") for pkg in packages]
            self._set_tool_rows(rows)
            self.tools_hint.setText("工具状态尚未检查；安装会校验并解压随程序提供的离线包。手机助手请自行选择。")
        except (OSError, ValueError, ToolInstallError) as exc:
            self.set_tool_help(())
            self._set_tool_rows(())
            self.tools_hint.setText(f"无法读取随包工具清单：{exc}")

    def _set_tool_rows(self, rows):
        self.tools_table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, value in enumerate(values):
                cell = QTableWidgetItem(str(value))
                cell.setToolTip(str(value) + "\n" + str(values[4]) if column != 4 else str(value))
                self.tools_table.setItem(row, column, cell)

    def _read_tool_statuses(self):
        return tuple(package_status(ROOT, package) for package in load_catalog())

    def _show_tool_statuses(self, statuses):
        self._set_tool_rows([(item.label, item.version, TOOL_STATES.get(item.state, item.state),
                              str(item.path), self.package_help(item.id, item.version, item.path) +
                              "\n" + item.detail) for item in statuses])
        self._tool_status_loaded = True
        installed = sum(item.state == "installed" for item in statuses)
        self.tools_hint.setText(f"随包工具：{installed} / {len(statuses)} 已安装并通过文件校验；可继续手动选择外部工具。")

    @Slot()
    def refresh_tools(self):
        self.start_job("检查随包工具状态", lambda log: ("tools-status", self._read_tool_statuses()))

    def _install_tools(self, log):
        # Do not reuse the startup metadata: the installer must validate the
        # real catalog again and surface errors, including a missing catalog.
        settings = install_packages(ROOT, log=log, cancel_event=self.cancel)
        return "tools-installed", (settings, self._read_tool_statuses())

    @Slot()
    def install_tools(self):
        if self.busy:
            return
        self.report = None
        self.report_revision = -1
        self.checks.setRowCount(0)
        self.details.clear()
        self.summary_label.setText("工具安装中；此前的环境检测已失效，安装后会重新检测")
        self.start_job("安装随包工具", self._install_tools)
        self.go(2)

    def go(self, index):
        self.navigation.setCurrentRow(index)

    @Slot()
    def refresh_preview(self):
        self.required(QLabel, "previewAppName").setText(self.edits["app_name"].text() or "我的脚本")
        self.required(QLabel, "previewPackage").setText(self.edits["package_name"].text() or "应用包名")
        self.required(QLabel, "previewVersion").setText(f"v{self.edits['version_name'].text()}  ·  build {self.edits['version_code'].text()}")
        preview = self.required(QLabel, "iconPreview")
        icon_path = self.edits["icon"].text().strip()
        pixmap = QPixmap(icon_path) if icon_path and Path(icon_path).is_file() else QPixmap()
        if pixmap.isNull():
            preview.setPixmap(QPixmap())
            preview.setText("APK")
        else:
            preview.setPixmap(pixmap.scaled(112, 112, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def choose(self, key):
        current = self.edits[key].text()
        if key in ("workspace", "dependency_dir"):
            path = QFileDialog.getExistingDirectory(self, "选择目录", current)
        elif key == "output":
            path, _ = QFileDialog.getSaveFileName(self, "选择新的 APK 输出文件", current, "APK 文件 (*.apk)")
        else:
            filters = {"source": "MQB 工程 (*.mqb)", "template": "APK 模板 (*.apk *.apkm)",
                       "compiled": "LC 编译文件 (*.lc)", "icon": "PNG 图标 (*.png)",
                       "keystore": "密钥库 (*.p12 *.jks *.keystore)"}
            path, _ = QFileDialog.getOpenFileName(self, "选择文件", current, filters.get(key, "可执行文件和工具 (*.exe *.jar)") + ";;所有文件 (*)")
        if path:
            self.edits[key].setText(path)

    @Slot()
    def save(self):
        try:
            save_settings(self.config_path, self.settings())
            self.set_status("配置已保存（不含密码）")
        except (OSError, ValueError) as exc:
            self.handle_failure(str(exc))

    @Slot()
    def load_config(self):
        path, _ = QFileDialog.getOpenFileName(self, "载入配置", str(self.config_path), "JSON 配置 (*.json)")
        if not path:
            return
        try:
            settings = load_settings(Path(path))
            self._loading_settings = True
            try:
                for key, edit in self.edits.items():
                    edit.setText(settings[key])
            finally:
                self._loading_settings = False
            self.config_path = Path(path)
            self.set_status("配置已载入；请重新检测环境")
        except (OSError, ValueError) as exc:
            self.handle_failure(str(exc))

    @Slot()
    def reveal(self):
        path = Path(self.edits["output"].text()).absolute().parent
        if not path.is_dir() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.set_status(f"输出目录不存在或无法打开：{path}", "error")

    def start_job(self, name, action):
        if self.busy:
            return
        self.busy = True
        self._action_name = name
        self.cancel.clear()
        self.cancel_install.setEnabled(name == "安装随包工具")
        for button in self._actions:
            button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.progress.show()
        self.set_status(f"正在{name}…")
        thread = QThread(self)
        worker = _Worker(action, (self.password.text(), self.key_password.text()))
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self.append_log)
        worker.result.connect(self.handle_result)
        worker.failed.connect(self.handle_failure)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(self.job_finished)
        thread.finished.connect(thread.deleteLater)
        self._thread, self._worker = thread, worker
        thread.start()

    @Slot()
    def job_finished(self):
        self.busy = False
        self.progress.hide()
        for button in self._actions:
            button.setEnabled(True)
        self._thread = self._worker = None
        self.cancel_install.setEnabled(False)
        if self._pending_environment_check:
            QTimer.singleShot(0, self._run_pending_environment_check)

    @Slot()
    def _run_pending_environment_check(self):
        if self._pending_environment_check and not self.busy:
            self._pending_environment_check = False
            self.doctor()

    @Slot()
    def build(self):
        settings, password, key_password = self.settings(), self.password.text(), self.key_password.text()
        allow = self.allow_resources.isChecked()
        self.go(4)
        self.start_job("构建签名 APK", lambda log: ("build", build_from_settings(settings, password, key_password, log=log, allow_unverified_resources=allow)))

    @Slot()
    def capture(self):
        settings, revision = self.settings(), self.source_revision
        self.go(4)
        self.start_job("捕获新 LC", lambda log: ("capture", (revision, capture_from_settings(settings, timeout=180, cancel_event=self.cancel, log=log))))

    @Slot()
    def doctor(self):
        config = environment_config(self.settings(), self.password.text(), self.key_password.text())
        revision = self.settings_revision
        self.go(3)
        self.start_job("检测环境", lambda log: ("report", (revision, check_environment(config))))

    @Slot()
    def new_key(self):
        path, _ = QFileDialog.getSaveFileName(self, "新建签名密钥库", self.edits["keystore"].text(), "PKCS12 密钥库 (*.p12)")
        if not path:
            return
        if not Path(path).suffix:
            path += ".p12"
        self.edits["keystore"].setText(path)
        settings, password = self.settings(), self.password.text()
        self.start_job("新建签名密钥", lambda log: ("key", create_keystore(settings, password, log=log)))

    @Slot()
    def inspect_selected_template(self):
        path = Path(self.edits["template"].text())
        self.start_job("检查模板", lambda log: ("template", (str(path), inspect_template(path))))

    @Slot(str)
    def append_log(self, text):
        text = str(text)
        for secret in sorted((value for value in (self.password.text(), self.key_password.text()) if value), key=len, reverse=True):
            text = text.replace(secret, "[REDACTED]")
        self.logs.appendPlainText(f"[{datetime.now():%H:%M:%S}] {STAGES.get(text, text)}")

    def set_status(self, text, state="normal"):
        self.status_label.setText(text)
        self.status_label.setProperty("state", state)
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    @Slot(str)
    def handle_failure(self, text):
        for secret in sorted((value for value in (self.password.text(), self.key_password.text()) if value), key=len, reverse=True):
            text = str(text).replace(secret, "[REDACTED]")
        self.append_log("失败详情：" + text)
        if self._action_name in ("安装随包工具", "检查随包工具状态"):
            self.tools_hint.setText(str(text))
        self.set_status("操作失败，请查看构建日志", "error")
        self.go(4)

    @Slot(str, object)
    def handle_result(self, kind, result):
        if kind == "tools-status":
            self._show_tool_statuses(result)
            self.set_status("随包工具状态已刷新")
        elif kind == "tools-installed":
            settings, statuses = result
            for name in TOOL_FIELDS:
                if name in settings:
                    self.edits[name].setText(str(settings[name]))
            self._show_tool_statuses(statuses)
            self._pending_environment_check = True
            self.append_log(f"随包工具已就绪：{ROOT / 'tools'}；正在准备重新检测环境。")
            try:
                save_settings(self.config_path, self.settings())
                self.set_status("工具已安装并绑定，配置已保存；等待环境重检")
            except (OSError, ValueError) as exc:
                self.handle_failure(f"工具已安装，但保存配置失败：{exc}")
        elif kind == "build":
            self.append_log(f"APK 构建、签名验证通过：{result.path}\n编译模式：{result.compilation_mode}\n设备执行：尚未验证")
            for warning in result.warnings:
                self.append_log("提示：" + warning)
            self.set_status("APK 已生成并通过签名验证", "success")
        elif kind == "capture":
            revision, compiled = result
            if revision == self.source_revision:
                self.edits["compiled"].setText(str(compiled.path))
                self.append_log(f"新 LC 已捕获：{compiled.path}\nSHA256：{compiled.sha256}")
                self.set_status("LC 已捕获，请确认它对应当前 MQB", "success")
            else:
                self.append_log("工程在捕获期间已变化，旧结果未绑定；请重新开始捕获。")
                self.set_status("已丢弃过时捕获结果，请重新捕获", "warning")
        elif kind == "report":
            self.report_revision, self.report = result
            self.populate_report()
            self.set_status("环境检测完成，选择一项查看详细信息")
        elif kind == "key":
            self.append_log(f"密钥库已创建：{result}；请妥善备份密钥与密码。")
            self.set_status("签名密钥已创建", "success")
        elif kind == "template":
            path, template = result
            if Path(path) == Path(self.edits["template"].text().strip()):
                self.required(QLabel, "templateSummary").setText(" · ".join(template.abis) or "没有原生 ABI")
                self.set_status("模板检查完成；格式和设备兼容性请运行环境检测")

    def populate_report(self):
        self.checks.setRowCount(len(self.report.items))
        colors = {"pass": QColor("#168251"), "warning": QColor("#b77913"), "error": QColor("#c63838")}
        for row, item in enumerate(self.report.items):
            values = (CHECK_STATUS[item.status], CHECK_TITLES.get(item.key, item.title), item.version or item.path or "—", "构建必需" if item.blocking else "参考 / 设备")
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if column == 0:
                    cell.setForeground(colors[item.status])
                self.checks.setItem(row, column, cell)
        errors = sum(item.status == "error" for item in self.report.items)
        warnings = sum(item.status == "warning" for item in self.report.items)
        if self.report_revision == self.settings_revision:
            self.summary_label.setText(f"{errors} 个错误 · {warnings} 个提示 · 构建条件{'具备' if self.report.build_ready else '未具备'}")
        else:
            self.summary_label.setText("配置已变化；下方为检测快照，请重新检测")
        if self.report.items:
            row = next((row for row, item in enumerate(self.report.items) if item.status != "pass"), 0)
            self.checks.selectRow(row)
            self.show_check()

    @Slot()
    def show_check(self):
        row = self.checks.currentRow()
        if not self.report or not 0 <= row < len(self.report.items):
            return
        item = self.report.items[row]
        self.details.setPlainText(f"检测项：{CHECK_TITLES.get(item.key, item.title)}\n状态：{CHECK_STATUS[item.status]}\n实际路径：{item.path or '—'}\n版本：{item.version or '—'}\n\n检测结果 / 错误原因：\n{item.detail}\n\n处理建议：\n{item.remedy}")

    def write_report(self, path):
        if self.report is None:
            raise ValueError("请先运行环境检测")
        data = self.report.to_dict()
        data["configuration_current"] = self.report_revision == self.settings_revision
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @Slot()
    def export_report(self):
        if not self.report:
            self.set_status("请先运行环境检测", "warning")
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出环境检测报告", "environment-report.json", "JSON 报告 (*.json)")
        if path:
            try:
                self.write_report(path)
                self.set_status("环境报告已导出（不含密码）")
            except (OSError, ValueError) as exc:
                self.handle_failure(str(exc))

    @Slot()
    def export_logs(self):
        path, _ = QFileDialog.getSaveFileName(self, "导出日志", "build-log.txt", "文本日志 (*.txt)")
        if path:
            try:
                Path(path).write_text(self.logs.toPlainText(), encoding="utf-8")
                self.set_status("构建日志已导出")
            except OSError as exc:
                self.handle_failure(str(exc))

    def closeEvent(self, event):
        if self.busy:
            self.cancel.set()
            self.set_status("已请求取消；请等待当前任务结束后再关闭", "warning")
            event.ignore()
            return
        self._pending_environment_check = False
        self.password.clear()
        self.key_password.clear()
        super().closeEvent(event)


def run_gui(config_path=CONFIG_PATH):
    application = QApplication.instance() or QApplication(sys.argv[:1])
    application.setApplicationName("LC打包")
    application.setApplicationVersion(__version__)
    application.setStyle("Fusion")
    settings = load_settings(config_path)
    for directory in (Path(settings["workspace"]), Path(settings["output"]).parent):
        directory.mkdir(parents=True, exist_ok=True)
    window = PackerWindow(settings, config_path)
    if screen := application.primaryScreen():
        available = screen.availableGeometry()
        window.resize(min(1280, max(960, available.width() - 40)), min(900, max(660, available.height() - 40)))
    window.show()
    application.exec()
