"""
Safety check before eval data is sent to Langfuse.

This project's Langfuse project lives in the US cloud. A leftover LANGFUSE_HOST or
LANGFUSE_BASE_URL from another setup (an old employer, another project) would send the
eval traces, which carry full email text, to the wrong place without any error. So
every script that talks to Langfuse calls check_environment() before it creates the
client and check_client() right after, and aborts if the host is not the expected one.
"""

import os

EXPECTED_HOST = "https://us.cloud.langfuse.com"


class WrongLangfuseHost(SystemExit):
    """A SystemExit, so a script that doesn't catch it stops with the message."""


def has_keys() -> bool:
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))


def check_environment() -> None:
    """Before creating the client: the variables must point to the expected host."""
    if os.environ.get("LANGFUSE_BASE_URL") != EXPECTED_HOST:
        raise WrongLangfuseHost(
            f"ABORT: LANGFUSE_BASE_URL must be {EXPECTED_HOST} (set it in .env, and "
            "unset any LANGFUSE_* variable inherited from the shell)"
        )
    host = os.environ.get("LANGFUSE_HOST")
    if host is not None and host.rstrip("/") != EXPECTED_HOST:
        raise WrongLangfuseHost("ABORT: LANGFUSE_HOST is set to a different host")


def check_client(client) -> str:
    """After creating the client: the host it resolved must be the expected one."""
    host = getattr(client, "_base_url", None)
    print(f"Langfuse host: {host}", flush=True)
    if not host or host.rstrip("/") != EXPECTED_HOST:
        raise WrongLangfuseHost(f"ABORT: the client resolved {host}, not {EXPECTED_HOST}")
    return host
