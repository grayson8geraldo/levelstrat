#!/usr/bin/env python3
"""
DLS — Diagonal Level Strategy Scanner

Crypto intraday signal scanner based on diagonal (sloping) levels.
Sends trade signals to Telegram when entry conditions are met.

Usage:
  python main.py              # Run scanner
  python main.py --once       # Run single scan cycle
  python main.py --test       # Test Telegram connection
"""

import sys
import logging

from src.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, BYBIT_API_KEY


def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def check_config():
    """Verify required configuration is set."""
    errors = []

    if not BYBIT_API_KEY:
        errors.append(
            "BYBIT_API_KEY not set. "
            "Get read-only API keys from https://www.bybit.com/app/user/api-management"
        )

    if not TELEGRAM_BOT_TOKEN:
        errors.append(
            "TELEGRAM_BOT_TOKEN not set. "
            "Create a bot via @BotFather in Telegram"
        )

    if not TELEGRAM_CHAT_ID:
        errors.append(
            "TELEGRAM_CHAT_ID not set. "
            "Send /start to your bot, then visit: "
            "https://api.telegram.org/bot<TOKEN>/getUpdates"
        )

    if errors:
        print("\n[!] Configuration errors:\n")
        for e in errors:
            print(f"  - {e}")
        print("\nCopy .env.example to .env and fill in your credentials:")
        print("  cp .env.example .env")
        print("  nano .env\n")
        return False

    return True


def test_connection():
    """Test Bybit and Telegram connections."""
    print("Testing connections...\n")

    # Test Bybit
    print("[1/2] Bybit API...")
    try:
        from src.data_fetcher import DataFetcher
        fetcher = DataFetcher()
        markets = fetcher.load_markets()
        perps = [m for m in markets.values() if m.get("swap") and m.get("settle") == "USDT"]
        print(f"  OK: {len(perps)} USDT-M perpetual futures available\n")
    except Exception as e:
        print(f"  FAILED: {e}\n")
        return

    # Test Telegram
    print("[2/2] Telegram Bot...")
    try:
        from src.notifier import TelegramNotifier
        notifier = TelegramNotifier()
        notifier.send_status_sync(
            "\u2705 <b>DLS Scanner: \u0422\u0435\u0441\u0442 \u0443\u0441\u043f\u0435\u0448\u0435\u043d!</b>\n"
            "\u0421\u043e\u0435\u0434\u0438\u043d\u0435\u043d\u0438\u0435 \u0441 Telegram \u0440\u0430\u0431\u043e\u0442\u0430\u0435\u0442."
        )
        print("  OK: Test message sent to Telegram\n")
    except Exception as e:
        print(f"  FAILED: {e}\n")
        return

    print("All connections OK!")


def main():
    setup_logging()

    if "--test" in sys.argv:
        if not check_config():
            sys.exit(1)
        test_connection()
        return

    if not check_config():
        sys.exit(1)

    from src.scanner import DLSScanner
    scanner = DLSScanner()

    if "--once" in sys.argv:
        scanner.scan_once()
    else:
        scanner.run()


if __name__ == "__main__":
    main()
