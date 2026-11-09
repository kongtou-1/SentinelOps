"""SMTP transport shared by platform notifications and approved report delivery."""

import smtplib
import ssl


def connect(config, timeout=10):
    if config["security"] == "ssl":
        client = smtplib.SMTP_SSL(
            config["host"], config["port"], timeout=timeout, context=ssl.create_default_context()
        )
    else:
        client = smtplib.SMTP(config["host"], config["port"], timeout=timeout)
    try:
        client.ehlo()
        if config["security"] == "starttls":
            client.starttls(context=ssl.create_default_context())
            client.ehlo()
        if config["username"]:
            client.login(config["username"], config["password"])
        return client
    except Exception:
        client.close()
        raise
