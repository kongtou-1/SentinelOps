"""Git credential helper: only the configured HTTPS origin can request secrets."""

import os
import sys

if __name__ == "__main__":
    values = {}
    for line in sys.stdin:
        line = line.rstrip("\r\n")
        if not line:
            break
        key, _, value = line.partition("=")
        values[key] = value
    if (
        sys.argv[-1] == "get"
        and values.get("protocol") == "https"
        and values.get("host") == os.environ.get("SOURCE_GIT_HOST")
    ):
        sys.stdout.write("username=" + os.environ.get("SOURCE_GIT_USERNAME", "") + "\n")
        sys.stdout.write("password=" + os.environ.get("SOURCE_GIT_PASSWORD", "") + "\n\n")
