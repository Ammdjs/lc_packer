# Only cataloged UI, templates and legal resources are embedded.
# User projects, configuration, signing material and tool archives stay external.
from pathlib import Path
from lc_packer.builtin_templates import load_templates, template_path

root = Path(SPECPATH)
a = Analysis(
    [str(root / "packaging/entrypoint.py")],
    pathex=[str(root)],
    binaries=[],
    datas=[(str(root / "lc_packer/ui/main_window.ui"), "lc_packer/ui"),
           (str(root / "lc_packer/ui/style.qss"), "lc_packer/ui"),
           (str(root / "lc_packer/data/tools_catalog.json"), "lc_packer/data"),
           (str(root / "lc_packer/data/templates_catalog.json"), "lc_packer/data"),
           *[(str(template_path(entry['id'])), "lc_packer/assets/templates") for entry in load_templates()],
           (str(root / "THIRD_PARTY_NOTICES.md"), "."),
           *[(str(path), "licenses") for path in sorted((root / "licenses").glob("*.txt"))]],
    hiddenimports=["encodings.gb18030", "encodings.cp437"],
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["PyQt5", "PyQt6", "PySide2", "tkinter", "pytest", "numpy", "pandas", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="LC打包", debug=False, bootloader_ignore_signals=False,
    strip=False, upx=False, console=False, disable_windowed_traceback=False,
    uac_admin=False, version=str(root / "packaging/version_info.txt"),
)
