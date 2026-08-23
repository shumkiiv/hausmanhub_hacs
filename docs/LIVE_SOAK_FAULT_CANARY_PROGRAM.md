# Live, soak, fault и canary программа

## Классы проверок

| Класс | Где выполняется | Физическая команда |
|---|---|---|
| Read-only smoke | Production API, HACS и состояние планшета | Запрещена |
| Reversible control | Только выделенная виртуальная entity | Допустима после явного выбора test target |
| Dangerous | Вода, домофон, питание, security | Только отдельный ручной план и подтверждение владельца |

Обычный health probe никогда не запускает сценарии и не вызывает device,
water, intercom, power или security actions. `tools/run_live_readonly_smoke.py`
содержит закрытый список GET endpoint и сохраняет только агрегаты без entity
ID, имён и сырых payload.

## Ежедневный smoke

Запуск:

```bash
python3 tools/run_live_readonly_smoke.py
```

На рабочем Linux-хосте unit-файлы из `operations/systemd/` запускают этот же
smoke ежедневно в 04:20 MSK с random delay до 10 минут. Timer persistent:
пропущенная из-за выключенного компьютера проверка выполняется после старта.

Пользовательские endpoints вызываются least-privilege токеном планшета,
admin-метрики - отдельным внешним admin-токеном. Оба access-файла находятся
вне Git. Отчёт записывается рядом с ними. Gate проверяет доступность девяти
read-only endpoint, latency до 5 секунд, возраст snapshot
до 120 секунд, fresh climate snapshot и отсутствие pending operation старше
120 секунд. Число offline devices, ближайших событий и записей журнала
остаётся наблюдаемой метрикой, но не превращает известное отключённое
устройство в ложный P0.

На планшете read-only smoke подтверждает установленную версию, запущенную
MainActivity, отсутствие FATAL/ANR и успешное обновление snapshot. В CI те же
маршруты выполняются на двух managed emulator profiles.

## Fault matrix

Исполняемый реестр `tests/product_readiness_fault_matrix.json` связывает
latency, 401, 409, 500, dropped SSE, HA restart, unavailable entity и stale
Recorder с конкретными тестами. Любая строка без существующего теста ломает
release gate. Все ожидания fail-closed и не разрешают физическую команду.

## Soak

- После изменения сети требуется 24 часа reconnect-наблюдения. Новый
  сетевой релиз не получает production verdict до полного окна.
- Перед сменой climate или scenario writer требуется 14 суток наблюдения.
  Минимум 7 суток разрешён только как оформленное исключение владельца.
- Окно сбрасывается при P0/P1, duplicate writer, незапланированной команде,
  потере SSE дольше 120 секунд или stale snapshot дольше 120 секунд.
- Если текущий релиз не меняет сеть и writer, длительный soak помечается как
  `not_required`, но будущий cutover без нового полного окна запрещён.

## Canary и rollback

HACS canary выполняется только после config check и backup в двух местах.
После install выполняются один restart, повторный config check, совпадение
frontend assets и проверка ошибок Hausman. Автоматический rollback обязателен
при P0/P1, config entry не `loaded`, несовпадении asset, росте pending age
выше 120 секунд или незапланированной физической команде.

Ответственный за решение rollback - владелец Hausman Home. Техническое
выполнение - агент релиза. Для Android rollback выполняется установкой
последней проверенной private версии без публикации APK/AAB.

## Release gate

Кандидат считается готовым, когда:

1. Полный HACS и Android gate зелёный.
2. Read-only smoke production API прошёл.
3. Fault matrix прошла локально и в CI.
4. Реальный планшет и два managed emulator профиля подтверждены.
5. Требуемое по типу изменения soak-окно завершено без P0/P1.
6. Backup, rollback threshold и ответственный записаны в release audit.
7. Физических команд от smoke, fault и deploy не было.
