"""Service settings and secrets, read once at start (spec 006 T036; contracts/environment.md).

``load_config`` reads the environment, moves every secret into a redacting ``Secret`` holder and
removes it from the mapping (``os.environ`` by default) so no child process inherits it. Children
get an environment built from an allowlist (``Config.child_env``), never a copy of ours. The AWS
pair is written only to a private credentials file on tmpfs (``Config.write_aws_files``).

Errors name keys, never values.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import MutableMapping, Optional

from shopify_admin import MODE_CLIENT_CREDENTIALS, MODE_STORED_TOKEN, Secret

AWS_PROFILE = "rosart-deploy"
AWS_REGION = "ap-southeast-2"

DEFAULT_PATH = "/usr/local/bin:/usr/bin:/bin"

# Every variable that carries a secret: read into a holder, then removed from the environment.
SECRET_KEYS = ("ROSART_SHOPIFY_CLIENT_ID", "ROSART_SHOPIFY_CLIENT_SECRET", "ROSART_SHOPIFY_ACCESS_TOKEN",
               "ROSART_AWS_ACCESS_KEY_ID", "ROSART_AWS_SECRET_ACCESS_KEY", "SLACK_BOT_TOKEN")

# One credential source (research R-3): refuse the variables that would add another.
FORBIDDEN_NAMES = ("AWS_ACCESS_KEY_ID", "AWS_PROFILE")


class ConfigError(ValueError):
    """The configuration is unusable; the message names variables, never values."""


@dataclass(frozen=True)
class Config:
    site: str
    public_origin: str
    base_path: str
    group: str
    data: Path
    export_repo: Path
    bind: str
    check_timeout_s: int
    publish_timeout_s: int
    invalidation_wait_s: int
    snapshot_window_s: int
    retention_days: int
    tz_display: str
    shopify_api_version: str
    token_mode: str
    tmp_dir: Path
    shopify_client_id: Optional[Secret]
    shopify_client_secret: Optional[Secret]
    shopify_access_token: Optional[Secret]
    aws_access_key_id: Secret
    aws_secret_access_key: Secret
    slack_bot_token: Optional[Secret]
    slack_channel_id: str
    _passthrough: dict = field(default_factory=dict, repr=False)

    def secrets(self) -> list[Secret]:
        """Every held secret, for registering with the scrubber."""
        held = (self.shopify_client_id, self.shopify_client_secret, self.shopify_access_token,
                self.aws_access_key_id, self.aws_secret_access_key, self.slack_bot_token)
        return [secret for secret in held if secret is not None]

    def token_config(self) -> dict[str, str]:
        """The mapping ``shopify_admin.token_source_from_config`` reads (the only place values leave the holders)."""
        cfg = {"ROSART_SHOPIFY_TOKEN_MODE": self.token_mode}
        for key, secret in (("ROSART_SHOPIFY_CLIENT_ID", self.shopify_client_id),
                            ("ROSART_SHOPIFY_CLIENT_SECRET", self.shopify_client_secret),
                            ("ROSART_SHOPIFY_ACCESS_TOKEN", self.shopify_access_token)):
            if secret is not None:
                cfg[key] = secret.reveal()
        return cfg

    @property
    def aws_dir(self) -> Path:
        return self.tmp_dir / "aws"

    def write_aws_files(self, profile: str = AWS_PROFILE, region: str = AWS_REGION) -> None:
        """Write ``<tmp>/aws/credentials`` (mode 0600) and ``<tmp>/aws/config``: the one AWS credential source."""
        self.aws_dir.mkdir(parents=True, exist_ok=True)
        _write_private(self.aws_dir / "credentials",
                       f"[{profile}]\naws_access_key_id = {self.aws_access_key_id.reveal()}\n"
                       f"aws_secret_access_key = {self.aws_secret_access_key.reveal()}\n")
        _write_private(self.aws_dir / "config", f"[profile {profile}]\nregion = {region}\n")

    def child_env(self, aws: bool = False) -> dict[str, str]:
        """The environment of a child process: an allowlist, never a secret."""
        env = {
            "PATH": self._passthrough.get("PATH", DEFAULT_PATH),
            "HOME": (self.tmp_dir / "home").as_posix(),
            "LANG": self._passthrough.get("LANG", "C.UTF-8"),
            "ASTRO_TELEMETRY_DISABLED": "1",
            "NPM_CONFIG_UPDATE_NOTIFIER": "false",
            "NPM_CONFIG_FUND": "false",
            "NPM_CONFIG_AUDIT": "false",
            "NPM_CONFIG_CACHE": (self.tmp_dir / "npm").as_posix(),
        }
        if "TZ" in self._passthrough:
            env["TZ"] = self._passthrough["TZ"]
        if aws:
            env.update({
                "AWS_SHARED_CREDENTIALS_FILE": (self.aws_dir / "credentials").as_posix(),
                "AWS_CONFIG_FILE": (self.aws_dir / "config").as_posix(),
                "AWS_EC2_METADATA_DISABLED": "true",
                "AWS_PAGER": "",
            })
        return env


def _write_private(path: Path, text: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    os.chmod(path, 0o600)


def _number(environ: MutableMapping[str, str], key: str, default: int) -> int:
    raw = (environ.get(key) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{key} must be a whole number") from None
    if value < 0:
        raise ConfigError(f"{key} must not be negative")
    return value


def load_config(environ: Optional[MutableMapping[str, str]] = None, tmp_dir: Optional[Path] = None) -> Config:
    """Read the settings, take the secrets out of ``environ`` and refuse an unusable configuration.

    ``tmp_dir`` (the tmpfs: credentials file, HOME, npm cache) defaults to ``PUBLISHER_TMP``, then ``/tmp``.
    """
    environ = os.environ if environ is None else environ
    tmp_dir = Path(tmp_dir or environ.get("PUBLISHER_TMP") or "/tmp")
    taken = {key: (environ.pop(key) or "").strip() for key in SECRET_KEYS if key in environ}

    def text(key: str, default: str) -> str:
        return (environ.get(key) or "").strip() or default

    refused = sorted(name for name in environ
                     if name.upper().endswith("_PROXY") or name.upper() in FORBIDDEN_NAMES)
    if refused:
        raise ConfigError("these variables must not be set in the publisher (one credential source, no proxy): "
                          + ", ".join(refused))

    base_path = text("PUBLISHER_BASE_PATH", "/rosart")
    if not base_path.startswith("/") or base_path.endswith("/"):
        raise ConfigError("PUBLISHER_BASE_PATH must start with / and have no trailing slash")

    mode = text("ROSART_SHOPIFY_TOKEN_MODE", MODE_CLIENT_CREDENTIALS)
    if mode not in (MODE_CLIENT_CREDENTIALS, MODE_STORED_TOKEN):
        raise ConfigError(f"ROSART_SHOPIFY_TOKEN_MODE must be {MODE_CLIENT_CREDENTIALS} or {MODE_STORED_TOKEN}")
    required = (("ROSART_SHOPIFY_CLIENT_ID", "ROSART_SHOPIFY_CLIENT_SECRET") if mode == MODE_CLIENT_CREDENTIALS
                else ("ROSART_SHOPIFY_ACCESS_TOKEN",)) + ("ROSART_AWS_ACCESS_KEY_ID", "ROSART_AWS_SECRET_ACCESS_KEY")
    missing = [key for key in required if not taken.get(key)]
    if missing:
        raise ConfigError(f"missing {', '.join(missing)}; run rosart-publisher/set-secrets.sh")

    def holder(key: str) -> Optional[Secret]:
        return Secret(taken[key]) if taken.get(key) else None

    passthrough = {key: environ[key] for key in ("PATH", "LANG", "TZ") if environ.get(key)}
    return Config(
        site=text("PUBLISHER_SITE", "rosart"),
        public_origin=text("PUBLIC_ORIGIN", "https://int.solagratia.com.au"),
        base_path=base_path,
        group=text("PUBLISHER_GROUP", "rosart-publishers"),
        data=Path(text("PUBLISHER_DATA", "/data")),
        export_repo=Path(text("PUBLISHER_EXPORT_REPO", "/export.git")),
        bind=text("PUBLISHER_BIND", "0.0.0.0:8080"),
        check_timeout_s=_number(environ, "CHECK_TIMEOUT_S", 1200),
        publish_timeout_s=_number(environ, "PUBLISH_TIMEOUT_S", 1200),
        invalidation_wait_s=_number(environ, "INVALIDATION_WAIT_S", 600),
        snapshot_window_s=_number(environ, "SNAPSHOT_WINDOW_S", 3600),
        retention_days=_number(environ, "RETENTION_DAYS", 365),
        tz_display=text("TZ_DISPLAY", "Australia/Melbourne"),
        shopify_api_version=text("SHOPIFY_API_VERSION", "2026-10"),
        token_mode=mode,
        tmp_dir=Path(tmp_dir),
        shopify_client_id=holder("ROSART_SHOPIFY_CLIENT_ID"),
        shopify_client_secret=holder("ROSART_SHOPIFY_CLIENT_SECRET"),
        shopify_access_token=holder("ROSART_SHOPIFY_ACCESS_TOKEN"),
        aws_access_key_id=Secret(taken["ROSART_AWS_ACCESS_KEY_ID"]),
        aws_secret_access_key=Secret(taken["ROSART_AWS_SECRET_ACCESS_KEY"]),
        slack_bot_token=holder("SLACK_BOT_TOKEN"),
        slack_channel_id=text("SLACK_SYSTEM_CHANNEL_ID", ""),
        _passthrough=passthrough,
    )
