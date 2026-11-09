import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from app.core.config import BACKEND_ROOT, get_settings


class SourceUnavailable(Exception):
    pass


def tree_bytes(root):
    total = 0
    if root.exists():
        for parent, _, files in os.walk(root):
            for filename in files:
                try:
                    total += (Path(parent) / filename).stat().st_size
                except FileNotFoundError:
                    continue
    return total


def git_command(
    arguments,
    deadline,
    *,
    cwd=None,
    credentials=None,
    credential_host=None,
    cache=None,
    max_output=8388608,
):
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("GIT_") and not k.startswith("SOURCE_GIT_")
    }
    env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
    )
    if get_settings().source_git_ca_file:
        env["GIT_SSL_CAINFO"] = str(get_settings().source_git_ca_file)
    if credentials:
        env.update(
            {
                "SOURCE_GIT_USERNAME": credentials[0],
                "SOURCE_GIT_PASSWORD": credentials[1],
                "SOURCE_GIT_HOST": credential_host or "",
            }
        )
    command = [
        "git",
        "-c",
        "protocol.allow=never",
        "-c",
        "protocol.https.allow=always",
        "-c",
        "credential.helper=",
        "-c",
        "http.followRedirects=false",
        "-c",
        f"core.hooksPath={os.devnull}",
    ]
    if credentials:
        helper = "!" + " ".join(
            "'" + str(p).replace("\\", "/").replace("'", "'\\''") + "'"
            for p in (sys.executable, BACKEND_ROOT / "app/integrations/git_credentials.py")
        )
        command.extend(["-c", "credential.helper=" + helper])
    command.extend(arguments)
    with tempfile.TemporaryFile() as output:
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.DEVNULL,
                start_new_session=sys.platform != "win32",
            )
        except OSError:
            raise SourceUnavailable("git_unavailable") from None
        try:
            next_cache_check = 0
            while process.poll() is None:
                if time.monotonic() >= deadline:
                    raise SourceUnavailable("sync_timeout")
                if output.tell() > max_output:
                    raise SourceUnavailable("git_output_limit")
                if cache and time.monotonic() >= next_cache_check:
                    if tree_bytes(cache) > get_settings().source_max_cache_bytes:
                        raise SourceUnavailable("git_cache_limit")
                    next_cache_check = time.monotonic() + 1
                time.sleep(0.05)
            if process.returncode:
                raise SourceUnavailable("git_fetch_or_object_unavailable")
            output.seek(0)
            result = output.read(max_output + 1)
            if len(result) > max_output:
                raise SourceUnavailable("git_output_limit")
            return result
        finally:
            if process.poll() is None:
                if sys.platform == "win32":
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                else:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                process.wait()
