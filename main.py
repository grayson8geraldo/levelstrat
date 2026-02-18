#!/usr/bin/env python3
"""
DLS — Diagonal Level Strategy Scanner v2.0

Crypto intraday signal scanner based on diagonal (sloping) levels.
Sends trade signals to Telegram when entry conditions are met.

Usage:
  python main.py              # Run scanner (continuous)
  python main.py --once       # Run single scan cycle
  python main.py --test       # Test Bybit + Telegram connections
  python main.py --backtest SYMBOL  # Run backtest on a symbol
  python main.py --stats      # Show performance stats
  python main.py --export     # Export trade journal to CSV
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
            "\u2705 <b>DLS Scanner v2.0: \u0422\u0435\u0441\u0442 \u0443\u0441\u043f\u0435\u0448\u0435\u043d!</b>\n"
            "\u0421\u043e\u0435\u0434\u0438\u043d\u0435\u043d\u0438\u0435 \u0441 Telegram \u0440\u0430\u0431\u043e\u0442\u0430\u0435\u0442.\n\n"
            "\U0001f4e6 \u041c\u043e\u0434\u0443\u043b\u0438: \u043a\u043e\u043d\u0444\u043b\u044e\u0435\u043d\u0446\u0438\u044f, \u0444\u0430\u043d\u0434\u0438\u043d\u0433, \u043f\u0430\u043c\u043f\u044b, \u0440\u0438\u0441\u043a\u0438, \u0436\u0443\u0440\u043d\u0430\u043b, \u0434\u0430\u0448\u0431\u043e\u0440\u0434"
        )
        print("  OK: Test message sent to Telegram\n")
    except Exception as e:
        print(f"  FAILED: {e}\n")
        return

    print("All connections OK!")


def run_backtest(symbol: str):
    """Run backtest on a symbol and print results."""
    from src.backtest import BacktestEngine, format_backtest_report

    print(f"\nRunning backtest for {symbol}...\n")
    engine = BacktestEngine()
    result = engine.run_backtest(symbol, limit=500)
    print(format_backtest_report(result))


def show_stats(days: int = 30):
    """Show performance statistics from the trade journal."""
    from src.trade_journal import TradeJournal

    journal = TradeJournal()
    report = journal.get_performance_report(days=days)

    print(f"\n{'=' * 50}")
    print(f"  PERFORMANCE REPORT ({report['period_days']} days)")
    print(f"{'=' * 50}")
    print(f"  Total Signals:    {report['total_signals']}")
    print(f"  Total Trades:     {report['total_trades']}")
    print(f"  Wins:             {report['wins']}")
    print(f"  Losses:           {report['losses']}")
    print(f"  Win Rate:         {report['win_rate']:.1%}")
    print(f"  Profit Factor:    {report['profit_factor']:.2f}")
    print(f"  Total PnL:        {report['total_pnl_pct']:+.2%}")
    print(f"  Max Drawdown:     {report['max_drawdown_pct']:.2%}")
    print(f"  Avg Level Str:    {report['avg_level_strength']:.1f}/100")
    print(f"  Avg Confirmations:{report['avg_confirmations']:.1f}/5")

    scenarios = report.get("scenarios", {})
    if scenarios:
        print(f"\n  BY SCENARIO:")
        for sc, data in scenarios.items():
            wr = data["wins"] / data["total"] if data["total"] > 0 else 0
            print(f"    {sc:>12}: {data['total']} trades, WR {wr:.0%}, PnL {data['pnl']:+.2%}")

    print(f"{'=' * 50}\n")


def export_journal():
    """Export trade journal to CSV."""
    from src.trade_journal import TradeJournal

    journal = TradeJournal()
    filepath = journal.export_csv()
    print(f"Journal exported to: {filepath}")


def main():
    setup_logging()

    if "--test" in sys.argv:
        if not check_config():
            sys.exit(1)
        test_connection()
        return

    if "--backtest" in sys.argv:
        if not check_config():
            sys.exit(1)
        idx = sys.argv.index("--backtest")
        if idx + 1 < len(sys.argv):
            symbol = sys.argv[idx + 1].upper()
            # Normalize: BTCUSDT -> BTC/USDT:USDT
            if "/" not in symbol:
                symbol = symbol.replace("USDT", "")
                symbol = f"{symbol}/USDT:USDT"
            run_backtest(symbol)
        else:
            print("Usage: python main.py --backtest BTCUSDT")
        return

    if "--stats" in sys.argv:
        show_stats()
        return

    if "--export" in sys.argv:
        export_journal()
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
