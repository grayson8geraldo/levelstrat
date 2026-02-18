# DIAGONAL LEVEL STRATEGY (DLS) — Crypto Intraday

## Premium Crypto Day Trading Strategy: "Наклонные Уровни"

**Версия:** 1.0
**Тип:** Внутридневная (Intraday)
**Рынок:** Криптовалютные фьючерсы (USDT-Margined Perpetual)
**Таймфреймы:** 1m, 5m, 15m (рабочие) + 1H, 4H (контекстные)

---

## Структура стратегии

| Файл | Описание |
|------|----------|
| [strategy/01_FOUNDATION.md](strategy/01_FOUNDATION.md) | Фундамент: философия, отбор монет, критерии |
| [strategy/02_DIAGONAL_LEVELS.md](strategy/02_DIAGONAL_LEVELS.md) | Построение наклонных уровней: 4 сценария |
| [strategy/03_ENTRY_RULES.md](strategy/03_ENTRY_RULES.md) | Правила входа: точки входа, подтверждения |
| [strategy/04_EXIT_RULES.md](strategy/04_EXIT_RULES.md) | Правила выхода: тейк-профиты, стоп-лоссы |
| [strategy/05_RISK_MANAGEMENT.md](strategy/05_RISK_MANAGEMENT.md) | Риск-менеджмент и управление капиталом |
| [strategy/06_INDICATORS.md](strategy/06_INDICATORS.md) | Индикаторы и их настройки |
| [strategy/07_SCENARIOS.md](strategy/07_SCENARIOS.md) | 4 торговых сценария с примерами |
| [strategy/08_CHECKLISTS.md](strategy/08_CHECKLISTS.md) | Чек-листы перед входом в сделку |
| [strategy/09_PUMP_TRADING.md](strategy/09_PUMP_TRADING.md) | Торговля пампов: вход на сдутии |
| [strategy/10_JOURNAL.md](strategy/10_JOURNAL.md) | Шаблон торгового журнала |

---

## Быстрый старт

1. Прочитайте `01_FOUNDATION.md` — поймите философию
2. Изучите `02_DIAGONAL_LEVELS.md` — научитесь строить наклонные уровни
3. Освойте `03_ENTRY_RULES.md` + `04_EXIT_RULES.md` — точки входа и выхода
4. Настройте индикаторы по `06_INDICATORS.md`
5. Перед каждой сделкой используйте `08_CHECKLISTS.md`
6. Ведите журнал по `10_JOURNAL.md`

---

## Ключевые принципы

- **3+ касания** — минимум для валидного наклонного уровня
- **Наторгованность** — уровень должен иметь историю реакций цены
- **Экстремум** — строим от значимого максимума или минимума
- **По тренду** — приоритет входов по направлению тренда
- **Объём 24ч > $20M** — минимальный порог ликвидности

---

## Сигнальный сканер (Программа)

Автоматический сканер, который мониторит рынок и присылает сигналы в Telegram.

### Установка

```bash
# 1. Установить зависимости
pip install -r requirements.txt

# 2. Скопировать конфигурацию
cp .env.example .env

# 3. Заполнить .env своими данными:
#    - BYBIT_API_KEY / BYBIT_API_SECRET (read-only ключи с Bybit)
#    - TELEGRAM_BOT_TOKEN (создать бота через @BotFather)
#    - TELEGRAM_CHAT_ID (отправить /start боту, затем узнать ID)
```

### Как получить ключи

**Bybit API (read-only):**
1. Зайти на bybit.com → API Management
2. Создать ключ с правами только на чтение (Read-Only)
3. Скопировать API Key и Secret в `.env`

**Telegram Bot:**
1. Написать @BotFather в Telegram → `/newbot`
2. Скопировать токен в `.env`
3. Написать `/start` своему боту
4. Узнать chat_id: открыть `https://api.telegram.org/bot<TOKEN>/getUpdates`

### Запуск

```bash
# Тест подключения к Bybit и Telegram
python main.py --test

# Одиночное сканирование (1 цикл)
python main.py --once

# Полный запуск (непрерывное сканирование каждые 60 сек)
python main.py
```

### Как работает

```
Каждые 60 секунд:

  1. Скринит топ-50 монет по объёму (> $20M)
  2. Классифицирует: аптренд / даунтренд / памп / новый листинг
  3. Загружает свечи на 4H, 1H, 15m, 5m
  4. Ищет наклонные уровни (3+ касания, от экстремума, наторгованные)
  5. Проверяет 5 подтверждений (Triple Confirmation)
  6. При 3+ подтверждениях → отправляет сигнал в Telegram
```

### Структура проекта

```
src/
├── config.py           # Все параметры стратегии
├── data_fetcher.py     # Загрузка данных с Bybit (ccxt)
├── screener.py         # Отбор и классификация монет
├── indicators.py       # EMA, RSI, MACD, OBV, ATR, BB
├── diagonal_levels.py  # Детектор наклонных уровней (ядро)
├── candle_patterns.py  # Свечные паттерны
├── signal_engine.py    # Triple Confirmation + генерация сигналов
├── notifier.py         # Отправка в Telegram
└── scanner.py          # Главный цикл сканирования
```

### Пример сигнала в Telegram

```
🟢 LONG ETH/USDT:USDT

🎯 Сценарий: ↑ Восходящий тренд
📊 Подтверждения: 4/5
  ✅ Наклонный уровень (4 касания)
  ✅ Свечной паттерн: bullish_engulfing
  ✅ Объём (x1.8)
  ✅ Индикатор (RSI: 38.5)
  ❌ Конфлюенция

──────────────────────────
💰 Вход:    2500.00
🛑 Стоп:     2478.75  (0.85%)
──────────────────────────
🏁 TP1 (30%): 2521.25
🏁 TP2 (40%): 2542.50
🏁 TP3 (30%): 2563.75
──────────────────────────
📈 R:R = 1:2.0
💪 Уровень: 80/100 (угол 35°)
⚖️ Плечо: 5x

🗂 Тренды: 4H=bullish | 1H=bullish | 15m=bullish
```
