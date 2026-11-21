import unittest
from unittest.mock import patch

import requests

import Bot
import notifier


class TestTelegramTokenLogging(unittest.TestCase):

    def test_bot_message_request_error_does_not_log_token(self):
        token = "BOT_TOKEN_SECRET"

        with (
            patch.object(Bot, "TELEGRAM_ENABLED", True),
            patch.object(
                Bot,
                "TELEGRAM_API_URL",
                f"https://api.telegram.org/bot{token}",
            ),
            patch(
                "Bot.requests.post",
                side_effect=requests.RequestException(
                    f"Request failed for bot{token}/sendMessage"
                ),
            ),
        ):
            with self.assertLogs("Bot", level="ERROR") as captured:
                result = Bot.send_telegram_message("test")

        self.assertFalse(result)
        self.assertNotIn(token, "\n".join(captured.output))

    def test_bot_photo_request_error_does_not_log_token(self):
        token = "BOT_TOKEN_SECRET"

        with (
            patch.object(Bot, "TELEGRAM_ENABLED", True),
            patch.object(
                Bot,
                "TELEGRAM_API_URL",
                f"https://api.telegram.org/bot{token}",
            ),
            patch(
                "Bot.requests.post",
                side_effect=requests.RequestException(
                    f"Request failed for bot{token}/sendPhoto"
                ),
            ),
        ):
            with self.assertLogs("Bot", level="ERROR") as captured:
                result = Bot.send_telegram_photo(b"image", "caption")

        self.assertFalse(result)
        self.assertNotIn(token, "\n".join(captured.output))

    def test_bot_rejects_overlong_photo_caption_without_truncating(self):
        with (
            patch.object(Bot, "TELEGRAM_ENABLED", True),
            patch.object(Bot, "TELEGRAM_API_URL", "https://api.telegram.org/botTEST"),
            patch("Bot.requests.post") as post,
        ):
            with self.assertLogs("Bot", level="ERROR") as captured:
                result = Bot.send_telegram_photo(b"image", "X" * 1025)

        self.assertFalse(result)
        post.assert_not_called()
        self.assertIn("CAPTION REJECTED", "\n".join(captured.output))

    def test_legacy_notifier_does_not_log_token(self):
        token = "BOT_TOKEN_SECRET"

        with (
            patch.object(notifier, "TELEGRAM_TOKEN", token),
            patch.object(notifier, "TELEGRAM_CHAT_ID", "123"),
            patch(
                "notifier.requests.post",
                side_effect=requests.RequestException(
                    f"Request failed for bot{token}/sendMessage"
                ),
            ),
        ):
            with self.assertLogs("notifier", level="ERROR") as captured:
                result = notifier.send_telegram("test")

        self.assertFalse(result)
        self.assertNotIn(token, "\n".join(captured.output))


if __name__ == "__main__":
    unittest.main()
