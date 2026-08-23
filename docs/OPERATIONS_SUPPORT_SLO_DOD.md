# Эксплуатация, поддержка и Definition of Done

## Безопасный support flow

Публичное сообщение создаётся через GitHub issue template. Разрешены версии,
описание поведения и воспроизводимые шаги на synthetic home. Запрещены токены,
адрес дома, названия и entity ID, raw payload, Node-RED export, private Android
APK/AAB и снимки реального дома. Redacted diagnostic export передаётся только
после явного согласия владельца и только приватным каналом поддержки.

## SLO и actionable alerting

| Сигнал | Цель | Alert |
|---|---:|---:|
| Crash-free sessions, 30 дней | не ниже 99,5% | ниже 99,0% |
| ANR sessions, 30 дней | ниже 0,3% | выше 0,5% |
| Command confirmation p95 | до 2 с | выше 5 с |
| Dashboard load p95 | до 1 с | выше 3 с |
| SSE reconnect p95 | до 10 с | выше 30 с |
| Snapshot stale duration p95 | до 30 с | выше 120 с |

Сейчас crash/ANR сведения остаются локальными и попадают только в redacted
экспорт по согласию. Сторонний telemetry SDK не подключается без отдельного
privacy review. Ежедневный read-only smoke создаёт локальный P1/P2 alert только
при превышении уже закреплённых thresholds. Alert не содержит данных дома.

## Классы дефектов

- P0: опасная физическая команда, утечка секрета, потеря управления водой или
  security. Реакция немедленно, release и writer блокируются.
- P1: массовый crash/ANR, недоступен dashboard или подтверждение команд.
  Реакция до 4 часов, выпуск блокируется.
- P2: функция деградировала, безопасный обход есть. Триаж до следующего
  рабочего дня.
- P3: косметика и улучшения без потери функции. Плановая очередь.

У каждой записи есть owner, severity, evidence и следующий контрольный срок.

## Rollback

HACS: остановить rollout, проверить config, восстановить последний full backup,
перезапустить Home Assistant один раз, повторить read-only smoke. Android:
остановить staged rollout, вернуть последний проверенный AAB в Play Console;
до публикации установить предыдущий private debug build поверх текущего без
очистки данных. Нельзя откатывать storage/schema молча: сначала compatibility и
migration note.

## Ежемесячный UX-аудит

На реальном Lenovo в landscape проверяются главная, комнаты, устройства,
климат, энергия, сценарии, настройки, offline/pending/error, крупный шрифт и
ночная тема. В Chromium проверяется тот же маршрут, keyboard/focus и 900/1280/
1440 px. Результат содержит версии, открытые P0-P3 и ссылки только на redacted
evidence. Systemd timer создаёт локальное напоминание первого числа месяца.

## Release blocker и Definition of Done

Release запрещён при открытом P0/P1, красном автоматическом gate, неизвестном
owner, устаревшей product documentation либо неподтверждённом rollback.
`operations/release-readiness.json` проверяется тестом и обновляется перед
каждым релизом.

Готово означает: код и контракт согласованы, unit/integration/browser/device
gates зелёные, live evidence актуально, accessibility проверена, privacy и
redaction не ухудшены, changelog и migration note написаны человеческим языком,
N/N-1 compatibility и rollback подтверждены.
