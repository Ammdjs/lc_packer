"""APK alignment and user-keystore signing; passwords never enter arguments."""
from dataclasses import dataclass, field
import os
from pathlib import Path

from .process import run_process


@dataclass(frozen=True)
class SigningConfig:
    keystore: Path
    key_alias: str
    store_password: str = field(repr=False)
    key_password: str | None = field(default=None, repr=False)


def sign_apk(unsigned, output, tools, signing, *, runner=run_process, log=None, timeout=180):
    if not Path(signing.keystore).is_file() or not signing.key_alias or not signing.store_password:
        raise ValueError("签名配置不完整：请选择密钥库并填写别名、仓库密码")
    aligned = Path(unsigned).with_name("aligned.apk")
    runner([tools.zipalign, "-p", "4", unsigned, aligned], stage="zipalign", timeout=timeout, log=log)
    env = dict(os.environ)
    env["LC_PACKER_STORE_PASSWORD"] = signing.store_password
    env["LC_PACKER_KEY_PASSWORD"] = signing.key_password or signing.store_password
    secrets = (signing.store_password, signing.key_password)
    runner([tools.java, "-jar", tools.apksigner_jar, "sign", "--ks", signing.keystore,
            "--ks-key-alias", signing.key_alias, "--ks-pass", "env:LC_PACKER_STORE_PASSWORD",
            "--key-pass", "env:LC_PACKER_KEY_PASSWORD", "--out", output, aligned],
           stage="sign", timeout=timeout, env=env, secrets=secrets, log=log)
    # run_process raises on nonzero status; apksigner's exit status is the
    # success signal. Display text can be redacted and is not a protocol.
    runner([tools.java, "-jar", tools.apksigner_jar, "verify", "--verbose", output],
           stage="verify-signature", timeout=timeout, log=log, secrets=secrets)
