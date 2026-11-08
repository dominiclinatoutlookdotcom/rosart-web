"""Settings, secrets and the child environment (spec 006 T035; contracts/environment.md, FR-015, FR-019).

Seam: ``publisher.config.load_config`` over an environment mapping, the ``Config`` it returns, its
``child_env`` and ``write_aws_files``. Nothing here reads a real ``.env`` or touches the network.
"""
import json
import os
import stat
import sys
from pathlib import Path

import pytest

from shopify_admin import Secret

FAKE_ENV = {
    "ROSART_SHOPIFY_CLIENT_ID": "client-id-planted",
    "ROSART_SHOPIFY_CLIENT_SECRET": "client-secret-planted",
    "ROSART_AWS_ACCESS_KEY_ID": "AKIAPLANTEDKEY0000",
    "ROSART_AWS_SECRET_ACCESS_KEY": "aws-secret-planted-value",
    "SLACK_BOT_TOKEN": "xoxb-planted-slack",
    "SLACK_SYSTEM_CHANNEL_ID": "C0PLANTED",
    "PATH": "/usr/local/bin:/usr/bin",
    "HOME": "/root",
    "LANG": "en_AU.UTF-8",
}
PLANTED = ["client-secret-planted", "AKIAPLANTEDKEY0000", "aws-secret-planted-value", "xoxb-planted-slack"]
STORED = {
    "ROSART_SHOPIFY_TOKEN_MODE": "stored-token",
    "ROSART_SHOPIFY_ACCESS_TOKEN": "shpat-planted-token",
    "ROSART_AWS_ACCESS_KEY_ID": "AKIAPLANTEDKEY0000",
    "ROSART_AWS_SECRET_ACCESS_KEY": "aws-secret-planted-value",
}


def load(env, tmp_path):
    from publisher.config import load_config

    return load_config(env, tmp_dir=tmp_path)


def test_every_setting_has_its_documented_default(tmp_path):
    cfg = load(dict(FAKE_ENV), tmp_path)

    assert cfg.site == "rosart"
    assert cfg.public_origin == "https://int.solagratia.com.au"
    assert cfg.base_path == "/rosart"
    assert cfg.group == "rosart-publishers"
    assert cfg.data == Path("/data")
    assert cfg.export_repo == Path("/export.git")
    assert cfg.bind == "0.0.0.0:8080"
    assert (cfg.check_timeout_s, cfg.publish_timeout_s, cfg.invalidation_wait_s) == (1200, 1200, 600)
    assert cfg.snapshot_window_s == 3600
    assert cfg.retention_days == 365
    assert cfg.tz_display == "Australia/Melbourne"
    assert cfg.shopify_api_version == "2026-10"
    assert cfg.token_mode == "client-credentials"


def test_every_setting_can_be_overridden_by_its_variable(tmp_path):
    env = dict(FAKE_ENV, PUBLISHER_SITE="other", PUBLIC_ORIGIN="https://x.example", PUBLISHER_BASE_PATH="/other",
               PUBLISHER_GROUP="g", PUBLISHER_DATA=str(tmp_path / "d"), PUBLISHER_EXPORT_REPO=str(tmp_path / "e.git"),
               PUBLISHER_BIND="127.0.0.1:0", CHECK_TIMEOUT_S="10", PUBLISH_TIMEOUT_S="11", INVALIDATION_WAIT_S="12",
               SNAPSHOT_WINDOW_S="13", RETENTION_DAYS="14", TZ_DISPLAY="UTC", SHOPIFY_API_VERSION="2027-01")
    cfg = load(env, tmp_path)

    assert (cfg.site, cfg.public_origin, cfg.base_path, cfg.group) == ("other", "https://x.example", "/other", "g")
    assert cfg.data == tmp_path / "d" and cfg.export_repo == tmp_path / "e.git" and cfg.bind == "127.0.0.1:0"
    assert (cfg.check_timeout_s, cfg.publish_timeout_s, cfg.invalidation_wait_s) == (10, 11, 12)
    assert (cfg.snapshot_window_s, cfg.retention_days, cfg.tz_display, cfg.shopify_api_version) == (13, 14, "UTC", "2027-01")


@pytest.mark.parametrize("bad", ["rosart", "/", "/rosart/", ""])
def test_a_base_path_must_start_with_a_slash_and_have_no_trailing_slash(bad, tmp_path):
    from publisher.config import ConfigError

    with pytest.raises(ConfigError, match="PUBLISHER_BASE_PATH"):
        load(dict(FAKE_ENV, PUBLISHER_BASE_PATH=bad), tmp_path)


def test_a_non_numeric_number_names_the_variable(tmp_path):
    from publisher.config import ConfigError

    with pytest.raises(ConfigError, match="CHECK_TIMEOUT_S"):
        load(dict(FAKE_ENV, CHECK_TIMEOUT_S="soon"), tmp_path)


def test_secrets_are_redacting_holders_and_leave_the_environment(tmp_path):
    env = dict(FAKE_ENV)
    cfg = load(env, tmp_path)

    for holder in (cfg.shopify_client_secret, cfg.aws_access_key_id, cfg.aws_secret_access_key, cfg.slack_bot_token):
        assert isinstance(holder, Secret)
        assert repr(holder) == "[redacted]" == str(holder)
    assert cfg.aws_secret_access_key.reveal() == "aws-secret-planted-value"
    assert not [key for key in env if key.startswith("ROSART_") and key != "ROSART_SHOPIFY_TOKEN_MODE"]
    assert "SLACK_BOT_TOKEN" not in env
    assert env["SLACK_SYSTEM_CHANNEL_ID"] == "C0PLANTED"          # not a secret
    for planted in PLANTED:
        assert planted not in repr(cfg) and planted not in str(cfg)


def test_the_process_environment_is_cleaned_when_none_is_passed(tmp_path, monkeypatch):
    from publisher.config import load_config

    for key, value in FAKE_ENV.items():
        monkeypatch.setenv(key, value)
    load_config(tmp_dir=tmp_path)

    assert "ROSART_AWS_SECRET_ACCESS_KEY" not in os.environ and "ROSART_SHOPIFY_CLIENT_SECRET" not in os.environ


def test_the_client_credentials_mode_names_every_missing_key_and_no_value(tmp_path):
    from publisher.config import ConfigError

    env = dict(FAKE_ENV)
    del env["ROSART_SHOPIFY_CLIENT_ID"]
    del env["ROSART_AWS_SECRET_ACCESS_KEY"]
    with pytest.raises(ConfigError) as caught:
        load(env, tmp_path)

    message = str(caught.value)
    assert "ROSART_SHOPIFY_CLIENT_ID" in message and "ROSART_AWS_SECRET_ACCESS_KEY" in message
    for planted in PLANTED:
        assert planted not in message


def test_the_stored_token_mode_needs_the_token_and_not_the_client_pair(tmp_path):
    from publisher.config import ConfigError

    cfg = load(dict(STORED), tmp_path)
    assert cfg.token_mode == "stored-token"
    assert cfg.shopify_access_token.reveal() == "shpat-planted-token"
    assert cfg.shopify_client_secret is None

    broken = dict(STORED)
    del broken["ROSART_SHOPIFY_ACCESS_TOKEN"]
    with pytest.raises(ConfigError, match="ROSART_SHOPIFY_ACCESS_TOKEN"):
        load(broken, tmp_path)


def test_an_unknown_token_mode_is_refused(tmp_path):
    from publisher.config import ConfigError

    with pytest.raises(ConfigError, match="ROSART_SHOPIFY_TOKEN_MODE"):
        load(dict(FAKE_ENV, ROSART_SHOPIFY_TOKEN_MODE="magic"), tmp_path)


@pytest.mark.parametrize("name", ["HTTPS_PROXY", "http_proxy", "NO_PROXY", "AWS_ACCESS_KEY_ID", "AWS_PROFILE"])
def test_start_up_refuses_a_proxy_or_a_standard_aws_credential_variable(name, tmp_path):
    from publisher.config import ConfigError

    with pytest.raises(ConfigError, match=name):
        load(dict(FAKE_ENV, **{name: "somevalue-planted"}), tmp_path)


def test_the_token_config_feeds_token_source_from_config(tmp_path):
    from shopify_admin import ClientCredentialsTokenSource, StoredTokenSource, token_source_from_config

    cc = token_source_from_config(load(dict(FAKE_ENV), tmp_path).token_config(), shop="s.myshopify.com", opener=object())
    stored = token_source_from_config(load(dict(STORED), tmp_path).token_config(), shop="s.myshopify.com", opener=object())

    assert isinstance(cc, ClientCredentialsTokenSource) and isinstance(stored, StoredTokenSource)


def test_the_aws_pair_is_written_only_to_the_credentials_file(tmp_path):
    cfg = load(dict(FAKE_ENV), tmp_path)
    cfg.write_aws_files(profile="rosart-deploy", region="ap-southeast-2")

    files = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    assert files == ["aws/config", "aws/credentials"]
    credentials = (tmp_path / "aws" / "credentials").read_text(encoding="utf-8")
    assert "[rosart-deploy]" in credentials
    assert "AKIAPLANTEDKEY0000" in credentials and "aws-secret-planted-value" in credentials
    config_text = (tmp_path / "aws" / "config").read_text(encoding="utf-8")
    assert "[profile rosart-deploy]" in config_text and "region = ap-southeast-2" in config_text
    assert "AKIAPLANTEDKEY0000" not in config_text and "aws-secret-planted-value" not in config_text


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_the_credentials_file_is_private(tmp_path):
    cfg = load(dict(FAKE_ENV), tmp_path)
    cfg.write_aws_files(profile="rosart-deploy", region="ap-southeast-2")

    assert stat.S_IMODE((tmp_path / "aws" / "credentials").stat().st_mode) == 0o600


def test_the_profile_and_region_match_the_committed_deploy_target():
    deploy = json.loads((Path(__file__).resolve().parent.parent.parent / "deploy.config.json").read_text(encoding="utf-8"))
    from publisher.config import AWS_PROFILE, AWS_REGION

    assert (AWS_PROFILE, AWS_REGION) == (deploy["profile"], deploy["region"])


def test_a_child_gets_only_allowlisted_names_and_never_a_secret(tmp_path):
    cfg = load(dict(FAKE_ENV, TZ="Australia/Melbourne", SOMETHING_ELSE="leak"), tmp_path)
    env = cfg.child_env()

    assert set(env) == {"PATH", "HOME", "LANG", "TZ", "ASTRO_TELEMETRY_DISABLED", "NPM_CONFIG_UPDATE_NOTIFIER",
                        "NPM_CONFIG_FUND", "NPM_CONFIG_AUDIT", "NPM_CONFIG_CACHE"}
    assert env["PATH"] == "/usr/local/bin:/usr/bin" and env["LANG"] == "en_AU.UTF-8"
    assert env["HOME"] == (tmp_path / "home").as_posix()
    assert env["ASTRO_TELEMETRY_DISABLED"] == "1"
    assert (env["NPM_CONFIG_UPDATE_NOTIFIER"], env["NPM_CONFIG_FUND"], env["NPM_CONFIG_AUDIT"]) == ("false",) * 3
    for value in env.values():
        for planted in PLANTED:
            assert planted not in value


def test_only_an_aws_child_gets_the_two_credential_file_paths(tmp_path):
    cfg = load(dict(FAKE_ENV), tmp_path)

    assert "AWS_SHARED_CREDENTIALS_FILE" not in cfg.child_env()
    aws_env = cfg.child_env(aws=True)
    assert aws_env["AWS_SHARED_CREDENTIALS_FILE"] == (tmp_path / "aws" / "credentials").as_posix()
    assert aws_env["AWS_CONFIG_FILE"] == (tmp_path / "aws" / "config").as_posix()
    assert aws_env["AWS_EC2_METADATA_DISABLED"] == "true" and aws_env["AWS_PAGER"] == ""
    assert not any(k in aws_env for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_PROFILE"))
