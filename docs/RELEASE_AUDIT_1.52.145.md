# Hausman Hub HACS 1.52.145: защита подтверждённого выключения климата

Дата: 2026-08-22

## Результат

- Добавлен restart-safe guard по contracts `0.52.0` (`da932b1`). Он
  вооружается только после принятой команды `off` и наблюдаемого read-back
  `off`; первый снимок и недоступное состояние не могут запустить повтор.
- `monitor` только фиксирует отклонение. `enforce` имеет grace, cooldown,
  предел 1-5 точных повторов `off` и единственную durable эскалацию.
- Настройки принимают только stable ID управляемых кондиционеров, используют
  optimistic locking и по умолчанию содержат пустой список устройств.
- Готовый интерфейс HACS сохранён. В frontend изменена только версия кеша.

## Релиз и проверки

- Contracts: commit `da932b1`, tag `v0.52.0`.
- HACS: commit `459b2c2`, tag `v1.52.145`.
- GitHub Actions `32573556407` завершён успешно за 3 минуты 32 секунды.
- Локальный release-gate: 1609 tests, 4 skipped; contract, package и
  repository safety checks успешны.
- Три схемы HACS побайтно совпадают с contracts. Все 88 frontend JS/CSS
  файлов отличаются от принятого UI baseline только cache-bust версии.

## Production deploy

- Цель: Home Assistant Core `2026.8.2`, `172.30.0.92`.
- До установки создан full backup `87e95767`. Он включает Home Assistant,
  базу, 10 add-ons и папки `ssl`, `share`, `media`. Защищённые копии по
  920258560 байт подтверждены в `hassio.local` и `hassio.KeeneticSSD`,
  agent errors отсутствуют.
- До и после установки `homeassistant.check_config` вернул HTTP 200.
- `v1.52.145` установлена явно через `update.install` с `backup=false`, затем
  Home Assistant перезапущен ровно один раз и восстановился примерно за
  10 секунд.
- Update entity и manifest подтвердили installed/latest `v1.52.145`.

## Безопасная production-конфигурация

- Для `detskaia_air_conditioner` включён только режим `monitor`: grace
  120 секунд, cooldown 300 секунд, max retries 3.
- Monitor не использует executor и не отправляет физические команды. Guard
  останется невооружённым до будущей принятой команды `off` с наблюдаемым
  read-back `off`.
- Enforce не включался. Живой кондиционер для проверки не переключался.
- Operation journal сохранил sequence 977 до и после deploy и настройки.

## Проверка после restart

- Guard settings, climate runtime и dashboard отвечают HTTP 200.
- Все 88 frontend JS/CSS файлов, три схемы и guard service на production
  побайтно совпадают с release.
- В system log нет ошибок Hausman. Физические команды при установке,
  перезапуске и настройке monitor не отправлялись.

## Откат

- Для полного отката использовать backup `87e95767`.
- Для немедленного отключения наблюдения заменить settings на пустой список
  с текущей revision. Это command-free операция и не включает кондиционер.
