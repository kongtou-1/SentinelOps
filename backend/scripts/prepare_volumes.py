"""One-shot root initializer; services consume secrets as non-root users.

File-backed Compose secrets do not apply UID/mode overrides. Copy source
secrets to Linux volumes with explicit ownership, never printing their contents.
"""

import json
import os
import shutil
from pathlib import Path


def owned_directory(path, uid, mode):
    path.mkdir(parents=True, exist_ok=True)
    os.chown(path, uid, uid)
    path.chmod(mode)


def copy_secret(source, directory, name, uid):
    destination = directory / name
    temporary = directory / f".{name}.tmp"
    temporary.touch(mode=0o600, exist_ok=True)
    os.chown(temporary, 0, 0)
    temporary.chmod(0o600)
    temporary.write_bytes(source.read_bytes())
    os.chown(temporary, uid, uid)
    temporary.chmod(0o400)
    # Existing readers retain access during reinitialization.
    os.replace(temporary, destination)


def main():
    source = Path("/seed")
    for directory, uid, names in (
        (
            Path("/owned/backend"),
            1000,
            ("credential_key", "monitoring_token", "alertmanager_token"),
        ),
        (Path("/owned/prometheus"), 65534, ("monitoring_token",)),
        (Path("/owned/alertmanager"), 65534, ("alertmanager_token",)),
    ):
        owned_directory(directory, uid, 0o700)
        for name in names:
            copy_secret(source / name, directory, name, uid)
    smtp = Path("/config/smtp.password")
    ai_key = Path("/config-ai/deepseek.key")
    if ai_key.is_file():
        copy_secret(ai_key, Path("/owned/backend"), "deepseek_key", 1000)
    else:
        (Path("/owned/backend") / "deepseek_key").unlink(missing_ok=True)
    if smtp.exists():
        copy_secret(smtp, Path("/owned/alertmanager"), "smtp_password", 65534)
        copy_secret(smtp, Path("/owned/backend"), "smtp_password", 1000)
    else:
        (Path("/owned/alertmanager") / "smtp_password").unlink(missing_ok=True)
        (Path("/owned/backend") / "smtp_password").unlink(missing_ok=True)
    rules = Path("/owned/rules")
    if Path("/owned/sources").exists():
        owned_directory(Path("/owned/sources"), 1000, 0o700)
    if Path("/owned/archives").exists():
        owned_directory(Path("/owned/archives"), 1000, 0o700)
    owned_directory(rules, 1000, 0o755)
    initial = rules / "platform.yml"
    if not initial.exists():
        shutil.copyfile("/config/rules/platform.yml", initial)
    for path in rules.iterdir():
        if path.is_file():
            os.chown(path, 1000, 1000)
            path.chmod(0o644)
    notifications = Path("/owned/notifications")
    if notifications.exists():
        import yaml

        owned_directory(notifications, 1000, 0o755)
        config = yaml.safe_load(Path("/config/alertmanager.yml").read_text())
        mode_path = notifications / "mode.json"
        if mode_path.is_file():
            mode = json.loads(mode_path.read_text())
            receiver = next(r for r in config["receivers"] if r["name"] == "platform")
            if receiver.get("email_configs"):
                mode["legacy_email_configs"] = receiver["email_configs"]
            if mode.get("managed"):
                receiver.pop("email_configs", None)
            mode_path.write_text(json.dumps(mode))
            os.chown(mode_path, 1000, 1000)
            mode_path.chmod(0o644)
        destination = notifications / "alertmanager.yml"
        destination.write_text(yaml.safe_dump(config, sort_keys=False))
        os.chown(destination, 1000, 1000)
        destination.chmod(0o644)
    print("Service volumes initialized with restricted Linux permissions")


if __name__ == "__main__":
    main()
