"""Tests for AlertManager notification system."""

from unittest.mock import AsyncMock, patch

import pytest

from naut_hedgegrid.config.operations import AlertConfig
from naut_hedgegrid.ops.alerts import AlertManager, AlertSeverity


@pytest.fixture
def alert_config():
    """Create test alert configuration."""
    return AlertConfig(
        enabled=True,
        slack_webhook="https://hooks.slack.com/services/TEST/WEBHOOK/URL",
        telegram_token="TEST_BOT_TOKEN",
        telegram_chat_id="TEST_CHAT_ID",
        alert_on_flatten=True,
        alert_on_circuit_breaker=True,
        alert_on_large_loss=True,
        large_loss_threshold_usdt=100.0,
        alert_on_high_funding=True,
        high_funding_threshold_bps=15.0,
    )


@pytest.fixture
def alert_manager(alert_config):
    """Create AlertManager instance."""
    return AlertManager(alert_config)


class TestAlertConfig:
    """Tests for AlertConfig model."""

    def test_config_defaults(self):
        """Test default configuration values."""
        config = AlertConfig()

        assert config.enabled is True
        assert config.alert_on_flatten is True
        assert config.alert_on_circuit_breaker is True
        assert config.large_loss_threshold_usdt == 100.0
        assert config.high_funding_threshold_bps == 15.0

    def test_config_from_env(self, monkeypatch):
        """Test loading credentials from environment variables."""
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://test.slack.com/webhook")
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test_bot_token")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "test_chat_id")

        config = AlertConfig()

        assert config.slack_webhook == "https://test.slack.com/webhook"
        assert config.telegram_token == "test_bot_token"
        assert config.telegram_chat_id == "test_chat_id"

    def test_has_slack_configured(self):
        """Test Slack configuration check."""
        config = AlertConfig(slack_webhook="https://test.slack.com")
        assert config.has_slack_configured() is True

        config_no_slack = AlertConfig()
        assert config_no_slack.has_slack_configured() is False

    def test_has_telegram_configured(self):
        """Test Telegram configuration check."""
        config = AlertConfig(
            telegram_token="test_token",
            telegram_chat_id="test_chat",
        )
        assert config.has_telegram_configured() is True

        config_no_telegram = AlertConfig()
        assert config_no_telegram.has_telegram_configured() is False

    def test_has_any_channel(self):
        """Test any channel configuration check."""
        config_slack = AlertConfig(slack_webhook="https://test.slack.com")
        assert config_slack.has_any_channel() is True

        config_telegram = AlertConfig(
            telegram_token="test_token",
            telegram_chat_id="test_chat",
        )
        assert config_telegram.has_any_channel() is True

        config_none = AlertConfig()
        assert config_none.has_any_channel() is False


class TestAlertManager:
    """Tests for AlertManager class."""

    def test_initialization(self, alert_manager):
        """Test AlertManager initialization."""
        assert alert_manager.config is not None
        assert alert_manager.logger is not None

    def test_initialization_no_channels_warning(self, caplog):
        """Test warning when no channels configured."""
        config = AlertConfig(enabled=True)
        AlertManager(config)

        assert "no channels configured" in caplog.text.lower()

    def test_disabled_manager_no_send(self, alert_manager):
        """Test that disabled manager doesn't send alerts."""
        alert_manager.config.enabled = False

        with patch.object(alert_manager, "send_alert_async") as mock_send:
            alert_manager.send_alert("Test message")
            mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_slack_success(self, alert_manager):
        """Test successful Slack alert."""
        mock_response = AsyncMock()
        mock_response.status = 200

        with patch("aiohttp.ClientSession.post") as mock_post:
            mock_post.return_value.__aenter__.return_value = mock_response

            await alert_manager._send_slack("Test message", AlertSeverity.INFO)

            # Verify webhook was called
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert alert_manager.config.slack_webhook in str(call_args)

    @pytest.mark.asyncio
    async def test_send_slack_failure(self, alert_manager):
        """Test Slack alert failure handling."""
        mock_response = AsyncMock()
        mock_response.status = 400
        mock_response.text = AsyncMock(return_value="Bad request")

        with patch("aiohttp.ClientSession.post") as mock_post:
            mock_post.return_value.__aenter__.return_value = mock_response

            with pytest.raises(RuntimeError, match="Slack webhook returned status 400"):
                await alert_manager._send_slack("Test message", AlertSeverity.INFO)

    @pytest.mark.asyncio
    async def test_send_telegram_success(self, alert_manager):
        """Test successful Telegram alert."""
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"ok": True})

        with patch("aiohttp.ClientSession.post") as mock_post:
            mock_post.return_value.__aenter__.return_value = mock_response

            await alert_manager._send_telegram("Test message", AlertSeverity.INFO)

            # Verify API was called
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert "api.telegram.org" in str(call_args)

    @pytest.mark.asyncio
    async def test_send_telegram_failure(self, alert_manager):
        """Test Telegram alert failure handling."""
        mock_response = AsyncMock()
        mock_response.status = 401
        mock_response.json = AsyncMock(return_value={"ok": False, "description": "Unauthorized"})

        with patch("aiohttp.ClientSession.post") as mock_post:
            mock_post.return_value.__aenter__.return_value = mock_response

            with pytest.raises(RuntimeError, match="Telegram API returned status 401"):
                await alert_manager._send_telegram("Test message", AlertSeverity.INFO)

    def test_format_message_basic(self, alert_manager):
        """Test basic message formatting."""
        formatted = alert_manager._format_message(
            "Test alert",
            AlertSeverity.INFO,
            None,
        )

        assert "ℹ️" in formatted  # INFO emoji
        assert "[INFO]" in formatted
        assert "Test alert" in formatted
        assert "Timestamp:" in formatted

    def test_format_message_with_data(self, alert_manager):
        """Test message formatting with extra data."""
        extra_data = {
            "current_value": 5.23,
            "threshold": 5.0,
            "position_size": 1000.0,
        }

        formatted = alert_manager._format_message(
            "Test alert",
            AlertSeverity.WARNING,
            extra_data,
        )

        assert "⚠️" in formatted  # WARNING emoji
        assert "[WARNING]" in formatted
        assert "Additional Details:" in formatted
        assert "Current Value:" in formatted
        assert "5.23" in formatted

    def test_send_circuit_breaker_alert(self, alert_manager):
        """Test circuit breaker alert."""
        with patch.object(alert_manager, "send_alert") as mock_send:
            alert_manager.send_circuit_breaker_alert(
                breaker_type="Drawdown",
                current_value=5.2,
                threshold=5.0,
                action="flatten positions",
            )

            mock_send.assert_called_once()
            call_args = mock_send.call_args
            assert "Drawdown" in str(call_args)
            assert call_args[0][1] == AlertSeverity.CRITICAL

    def test_send_flatten_alert(self, alert_manager):
        """Test position flatten alert."""
        with patch.object(alert_manager, "send_alert") as mock_send:
            alert_manager.send_flatten_alert(
                reason="Max drawdown exceeded",
                sides_flattened=["long", "short"],
                cancelled_orders=10,
                positions_closed=[
                    {"side": "long", "size": 0.5, "order_id": "test-1"},
                    {"side": "short", "size": 0.3, "order_id": "test-2"},
                ],
            )

            mock_send.assert_called_once()
            call_args = mock_send.call_args
            assert call_args[0][1] == AlertSeverity.CRITICAL

    def test_send_large_loss_alert(self, alert_manager):
        """Test large loss alert."""
        with patch.object(alert_manager, "send_alert") as mock_send:
            # Loss above threshold
            alert_manager.send_large_loss_alert(
                loss_amount=-150.0,
                instrument="BTCUSDT-PERP",
                side="LONG",
            )

            mock_send.assert_called_once()

    def test_send_large_loss_alert_below_threshold(self, alert_manager):
        """Test that small losses don't trigger alert."""
        with patch.object(alert_manager, "send_alert") as mock_send:
            # Loss below threshold
            alert_manager.send_large_loss_alert(
                loss_amount=-50.0,
                instrument="BTCUSDT-PERP",
                side="LONG",
            )

            mock_send.assert_not_called()

    def test_send_high_funding_alert(self, alert_manager):
        """Test high funding rate alert."""
        with patch.object(alert_manager, "send_alert") as mock_send:
            # Funding above threshold
            alert_manager.send_high_funding_alert(
                funding_rate_bps=20.0,
                projected_cost_usdt=50.0,
                instrument="BTCUSDT-PERP",
            )

            mock_send.assert_called_once()

    def test_send_high_funding_alert_below_threshold(self, alert_manager):
        """Test that low funding doesn't trigger alert."""
        with patch.object(alert_manager, "send_alert") as mock_send:
            # Funding below threshold
            alert_manager.send_high_funding_alert(
                funding_rate_bps=10.0,
                projected_cost_usdt=25.0,
                instrument="BTCUSDT-PERP",
            )

            mock_send.assert_not_called()

    def test_send_startup_alert(self, alert_manager):
        """Test startup alert."""
        with patch.object(alert_manager, "send_alert") as mock_send:
            alert_manager.send_startup_alert(
                strategy_name="HedgeGridV1",
                config_path="/path/to/config.yaml",
            )

            mock_send.assert_called_once()
            call_args = mock_send.call_args
            assert call_args[0][1] == AlertSeverity.INFO

    def test_send_shutdown_alert(self, alert_manager):
        """Test shutdown alert."""
        with patch.object(alert_manager, "send_alert") as mock_send:
            alert_manager.send_shutdown_alert(
                strategy_name="HedgeGridV1",
                reason="Manual stop",
                final_pnl=1234.56,
            )

            mock_send.assert_called_once()
            call_args = mock_send.call_args
            assert call_args[0][1] == AlertSeverity.INFO


class TestAlertSeverity:
    """Tests for AlertSeverity enum."""

    def test_severity_emojis(self):
        """Test severity emoji mapping."""
        assert AlertSeverity.INFO.emoji == "ℹ️"
        assert AlertSeverity.WARNING.emoji == "⚠️"
        assert AlertSeverity.CRITICAL.emoji == "🚨"

    def test_severity_colors(self):
        """Test severity color mapping."""
        assert AlertSeverity.INFO.color == "#36a64f"
        assert AlertSeverity.WARNING.color == "#ff9900"
        assert AlertSeverity.CRITICAL.color == "#ff0000"

    def test_severity_from_string(self):
        """Test creating severity from string value."""
        assert AlertSeverity("info") == AlertSeverity.INFO
        assert AlertSeverity("warning") == AlertSeverity.WARNING
        assert AlertSeverity("critical") == AlertSeverity.CRITICAL
