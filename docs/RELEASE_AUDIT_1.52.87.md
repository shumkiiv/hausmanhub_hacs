# Release audit HausmanHub 1.52.87

Дата: 2026-08-13.

## Область выпуска

- Открытая карточка устройства больше не повторяет имя устройства перед
  каждой командой.
- Общие действия получили короткие подписи, а дочерние возможности сохраняют
  различимый контекст.
- Полное имя устройства остаётся в aria-label.
- API v1, backend, contracts, storage и исполнение команд не изменялись.

## Проверка

- Node syntax checks и отдельный runtime-тест нормализации подписей прошли.
- Полный pytest: 1374 теста пройдено, 4 пропущено, 922 subtests пройдено.
- `tools/check_local_release.py`: 1372 теста пройдено, 4 пропущено; fixtures,
  Android compatibility, HACS package, naming и file safety зелёные.
- Release commit `ae89b23`, tag и GitHub Release `v1.52.87` опубликованы;
  Actions `31717482272` завершён успешно.
- Перед production-установкой завершён automatic backup и принят config
  check. Явный `update.install v1.52.87` и restart прошли штатно.
- После restart installed/latest и panel marker равны `1.52.87`, все 10
  сущностей HausmanHub доступны. Dashboard вернул 13 комнат и 82 устройства;
  runtime fresh, active operations и blocked reasons 0.
- Физические команды устройствам не отправлялись.
