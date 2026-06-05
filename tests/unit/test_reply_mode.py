"""Unit tests for triage_dashboard.reply_mode — read-only vs reply detection."""

from __future__ import annotations

from triage_dashboard import reply_mode


class TestSecretsHasKey:
    def test_none_and_empty(self):
        assert reply_mode.secrets_has_key(None) is False
        assert reply_mode.secrets_has_key("") is False

    def test_export_prefixed_key(self):
        assert reply_mode.secrets_has_key("export BUGZILLA_BOT_API_KEY=abc123") is True

    def test_bare_key(self):
        assert reply_mode.secrets_has_key("BUGZILLA_BOT_API_KEY=abc123") is True

    def test_quoted_value(self):
        assert reply_mode.secrets_has_key('export BUGZILLA_BOT_API_KEY="abc123"') is True

    def test_empty_value_is_not_a_key(self):
        assert reply_mode.secrets_has_key("export BUGZILLA_BOT_API_KEY=") is False
        assert reply_mode.secrets_has_key("BUGZILLA_BOT_API_KEY=   ") is False

    def test_unrelated_lines_ignored(self):
        text = "# comment\nexport OTHER=1\nBUGZILLA_BOT_API_KEY=k\n"
        assert reply_mode.secrets_has_key(text) is True
        assert reply_mode.secrets_has_key("export OTHER=1\n") is False


class TestReplyCapable:
    def test_env_key_present(self):
        assert reply_mode.reply_capable("abc", None) is True

    def test_env_key_blank_falls_back_to_secrets(self):
        assert reply_mode.reply_capable("   ", "BUGZILLA_BOT_API_KEY=k") is True
        assert reply_mode.reply_capable("   ", None) is False

    def test_secrets_only(self):
        assert reply_mode.reply_capable(None, "BUGZILLA_BOT_API_KEY=k") is True

    def test_neither(self):
        assert reply_mode.reply_capable(None, None) is False
        assert reply_mode.reply_capable("", "") is False


class TestDetectReplyMode:
    def test_secrets_file_present(self, tmp_path, monkeypatch):
        monkeypatch.delenv("BUGZILLA_BOT_API_KEY", raising=False)
        secrets = tmp_path / "secrets"
        secrets.write_text("export BUGZILLA_BOT_API_KEY=abc\n", encoding="utf-8")
        assert reply_mode.detect_reply_mode(secrets) is True

    def test_no_key_no_file(self, tmp_path, monkeypatch):
        monkeypatch.delenv("BUGZILLA_BOT_API_KEY", raising=False)
        assert reply_mode.detect_reply_mode(tmp_path / "missing") is False

    def test_env_var_present_without_file(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BUGZILLA_BOT_API_KEY", "fromenv")
        assert reply_mode.detect_reply_mode(tmp_path / "missing") is True
