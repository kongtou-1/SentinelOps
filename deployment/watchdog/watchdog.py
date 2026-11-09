"""Independent HTTP watchdog. Python standard library only; no platform DB access."""

import argparse
import hashlib
import json
import re
import smtplib
import ssl
import time
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import urlopen


def validate(config):
    targets = config.get("targets", [])
    if not 1 <= len(targets) <= 20:
        raise ValueError("Configure 1–20 HTTP targets")
    names = set()
    for target in targets:
        url = urlsplit(target["url"])
        if (
            not re.fullmatch(r"[A-Za-z0-9_.-]{1,60}", target["name"])
            or target["name"] in names
        ):
            raise ValueError("Target names must be unique")
        names.add(target["name"])
        if (
            url.scheme not in ("http", "https")
            or not url.hostname
            or url.username
            or url.query
            or url.fragment
        ):
            raise ValueError(
                "Use HTTP(S) health URLs without credentials or query strings"
            )
    smtp = config.get("smtp", {})
    recipients = smtp.get("recipients", [])
    if config.get("enabled"):
        for address in [smtp.get("sender", ""), *recipients]:
            if not re.fullmatch(
                r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+", address
            ):
                raise ValueError("Configure valid ASCII mail addresses")
        if not 1 <= len(recipients) <= 20:
            raise ValueError("Configure 1–20 explicit recipients")
        if smtp.get("username") and not smtp.get("starttls"):
            raise ValueError("SMTP authentication requires TLS")


def check(target):
    try:
        with urlopen(target["url"], timeout=3) as response:
            return (
                "healthy" if response.status == 200 else "http_" + str(response.status)
            )
    except HTTPError as exc:
        return "http_" + str(exc.code)
    except (OSError, URLError, TimeoutError):
        return "unavailable"


def send(config, failures):
    smtp = config["smtp"]
    message = EmailMessage()
    message["From"], message["To"] = smtp["sender"], ", ".join(smtp["recipients"])
    message["Subject"] = "SentinelOps independent watchdog"
    message.set_content(
        "\n".join(
            [
                datetime.now(UTC).isoformat(),
                *[name + ": " + status for name, status in failures.items()],
            ]
        )
    )
    dispatched = False
    try:
        with smtplib.SMTP(
            smtp["host"], int(smtp.get("port", 25)), timeout=10
        ) as client:
            client.ehlo()
            if smtp.get("starttls"):
                client.starttls(context=ssl.create_default_context())
                client.ehlo()
            if smtp.get("username"):
                client.login(
                    smtp["username"], Path(smtp["password_file"]).read_text().strip()
                )
            code, _ = client.mail(smtp["sender"])
            if code != 250:
                return "failed"
            for recipient in smtp["recipients"]:
                code, _ = client.rcpt(recipient)
                if code not in (250, 251):
                    return "failed"
            dispatched = True
            code, _ = client.data(message.as_bytes())
            return "sent" if code == 250 else "failed"
    except smtplib.SMTPResponseException:
        return "failed"
    except (OSError, smtplib.SMTPException):
        return "unknown" if dispatched else "failed"


def run_once(config, state_path):
    validate(config)
    statuses = {target["name"]: check(target) for target in config["targets"]}
    failures = {
        name: status for name, status in statuses.items() if status != "healthy"
    }
    fingerprint = hashlib.sha256(
        json.dumps(failures, sort_keys=True).encode()
    ).hexdigest()
    previous = json.loads(state_path.read_text()) if state_path.exists() else {}
    delivery = "unchanged"
    if failures and previous.get("fingerprint") != fingerprint:
        # Persist the attempt before SMTP. A crash cannot trigger a duplicate send.
        pending = {
            "fingerprint": fingerprint,
            "delivery": "unknown",
            "checked_at": datetime.now(UTC).isoformat(),
        }
        state_path.write_text(json.dumps(pending), encoding="utf-8")
        delivery = send(config, failures) if config.get("enabled") else "disabled"
    state = {
        "fingerprint": fingerprint,
        "statuses": statuses,
        "delivery": previous.get("delivery", "none")
        if delivery == "unchanged"
        else delivery,
        "checked_at": datetime.now(UTC).isoformat(),
    }
    temporary = state_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state), encoding="utf-8")
    temporary.replace(state_path)
    return {"statuses": statuses, "delivery": delivery}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    arguments = parser.parse_args()
    while True:
        config = json.loads(arguments.config.read_text(encoding="utf-8"))
        result = run_once(config, arguments.config.with_suffix(".state.json"))
        print(json.dumps(result), flush=True)
        if arguments.once:
            return
        time.sleep(max(15, min(60, int(config.get("interval_seconds", 60)))))


if __name__ == "__main__":
    main()
