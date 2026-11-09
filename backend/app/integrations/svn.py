"""Read-only SVN commands with bounded output and isolated authentication."""

import os
import signal
import subprocess
import sys
import tempfile
import time

from app.core.config import get_settings
from app.integrations.git import SourceUnavailable


def svn_command(arguments, deadline, *, credentials=None, max_output=8388608):
    if time.monotonic() >= deadline:
        raise SourceUnavailable("sync_timeout")
    # Never reuse a worker account's cached credentials or external helper configuration.
    with tempfile.TemporaryDirectory(prefix="source-svn-") as config_dir:
        command = [
            "svn",
            *arguments,
            "--non-interactive",
            "--no-auth-cache",
            "--config-dir",
            config_dir,
            "--config-option",
            "config:auth:password-stores=",
            "--config-option",
            "servers:global:store-auth-creds=no",
        ]
        if get_settings().source_svn_ca_file:
            command.extend(
                [
                    "--config-option",
                    "servers:global:ssl-authority-files=" + str(get_settings().source_svn_ca_file),
                ]
            )
        if credentials:
            command.extend(["--username", credentials[0], "--password-from-stdin"])
        with tempfile.TemporaryFile() as output:
            try:
                process = subprocess.Popen(
                    command,
                    stdin=subprocess.PIPE if credentials else subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.DEVNULL,
                    start_new_session=sys.platform != "win32",
                )
            except OSError:
                raise SourceUnavailable("svn_unavailable") from None
            try:
                password = (credentials[1] + "\n").encode() if credentials else None
                while True:
                    if time.monotonic() >= deadline:
                        raise SourceUnavailable("sync_timeout")
                    if output.tell() > max_output:
                        raise SourceUnavailable("svn_output_limit")
                    try:
                        process.communicate(input=password, timeout=0.05)
                        break
                    except subprocess.TimeoutExpired:
                        password = None
                if process.returncode:
                    raise SourceUnavailable("svn_remote_or_revision_unavailable")
                output.seek(0)
                result = output.read(max_output + 1)
                if len(result) > max_output:
                    raise SourceUnavailable("svn_output_limit")
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
                if process.stdin:
                    process.stdin.close()
