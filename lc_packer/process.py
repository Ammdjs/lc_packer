"""Hidden, bounded child processes with stage-specific redacted errors."""
import os
import subprocess


class ProcessError(RuntimeError):
    pass


def run_process(args, *, stage, timeout=180, env=None, secrets=(), log=None):
    def clean(text):
        for secret in sorted((s for s in secrets if s), key=len, reverse=True):
            text = text.replace(secret, "[REDACTED]")
        return text
    if log:
        log(stage)
    try:
        result = subprocess.run([str(x) for x in args], stdout=subprocess.PIPE,
                                stdin=subprocess.DEVNULL,
                                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace", timeout=timeout, env=env,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProcessError(clean(f"{stage}: {exc}")) from None
    output = clean(result.stdout or "")
    if result.returncode:
        raise ProcessError(f"{stage}: exit {result.returncode}\n{output[-12000:]}")
    return output
