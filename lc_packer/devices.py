"""Explicit ADB device operations, kept separate from offline APK building."""
from dataclasses import dataclass
from pathlib import Path

from .process import run_process


@dataclass(frozen=True)
class Device:
    serial: str
    state: str


class AdbService:
    def __init__(self, adb, *, runner=run_process):
        self.adb, self.runner = Path(adb), runner

    def _run(self, args, stage):
        return self.runner([self.adb, *args], stage=stage, timeout=120)

    def enumerate(self):
        lines = self._run(["devices"], "adb-devices").splitlines()
        return tuple(Device(parts[0], parts[1]) for line in lines if len(parts := line.split()) == 2 and parts[1] in ("device", "offline", "unauthorized"))

    def abis(self, serial):
        return tuple(self._run(["-s", serial, "shell", "getprop", "ro.product.cpu.abilist"], "adb-abi").strip().split(","))

    def packages(self, serial):
        return {line.removeprefix("package:") for line in self._run(["-s", serial, "shell", "pm", "list", "packages"], "adb-packages").splitlines() if line.startswith("package:")}

    def install(self, serial, result):
        device = next((device for device in self.enumerate() if device.serial == serial), None)
        if not device or device.state != "device":
            raise ValueError(f"设备不在线或未授权：{serial}")
        if not set(result.abis).intersection(self.abis(serial)):
            raise ValueError("模板 ABI 与设备不匹配")
        if result.package_name in self.packages(serial):
            raise ValueError("设备中已有相同包名，拒绝覆盖；请更换包名或自行处理旧应用")
        output = self._run(["-s", serial, "install", str(result.path)], "adb-install")
        if "Success" not in output:
            raise ValueError(f"安装未成功：{output}")

    def launch(self, serial, result):
        output = self._run(["-s", serial, "shell", "am", "start", "-W", "-n", f"{result.package_name}/{result.launch_activity}"], "adb-launch")
        if "Status: ok" not in output:
            raise ValueError(f"启动失败：{output}")
        return output
