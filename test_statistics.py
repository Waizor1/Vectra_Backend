#!/usr/bin/env python3
"""
Тестовый скрипт для проверки системы автоматической статистики
"""

import asyncio
import sys
import os
from datetime import date, timedelta

# Добавляем путь к модулю bloobcat
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

async def test_statistics():
    """Тестирует систему статистики"""
    try:
        print("🧪 Тестирование системы статистики...")
        
        # Инициализируем Tortoise ORM
        from bloobcat.clients import TORTOISE_ORM
        from tortoise import Tortoise
        
        await Tortoise.init(config=TORTOISE_ORM)
        
        print("✅ База данных подключена")
        
        # Импортируем модули статистики
        from bloobcat.statistics.collector import StatisticsCollector
        from bloobcat.statistics.trends import TrendsCalculator
        from bloobcat.statistics.formatter import StatisticsFormatter
        
        print("✅ Модули статистики импортированы")
        
        # Тестируем сбор данных за сегодня
        print("\n📊 Тестируем сбор дневной статистики...")
        today = date.today()
        daily_stats = await StatisticsCollector.collect_daily_stats(today)
        print(f"Статистика за {today}:")
        for key, value in daily_stats.items():
            print(f"  {key}: {value}")
        
        # Тестируем тренды
        print("\n📈 Тестируем расчет трендов...")
        trends_data = await TrendsCalculator.calculate_daily_trends(today)
        print("✅ Тренды рассчитаны")
        
        # Тестируем форматирование
        print("\n🎨 Тестируем форматирование отчета...")
        report = StatisticsFormatter.format_daily_report(trends_data)
        print("Сформированный отчет:")
        print("=" * 50)
        print(report)
        print("=" * 50)
        
        # Тестируем общую статистику
        print("\n📊 Тестируем общую статистику...")
        total_stats = await StatisticsCollector.get_total_stats()
        total_report = StatisticsFormatter.format_test_report(total_stats, "total")
        print("Общая статистика:")
        print("=" * 50)
        print(total_report)
        print("=" * 50)
        
        print("\n✅ Все тесты прошли успешно!")
        
    except Exception as e:
        print(f"❌ Ошибка при тестировании: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Закрываем соединение с БД
        await Tortoise.close_connections()


if __name__ == "__main__":
    asyncio.run(test_statistics()) 