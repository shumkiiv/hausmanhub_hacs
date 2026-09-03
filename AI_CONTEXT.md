# HausmanHub AI Context

- 2026-09-03: после установки `1.52.206` живой цикл `25,0 -> 25,5 -> 25,0°C`
  снова попал в общий post-dispatch catch: `control_revision 9 -> 10 -> 11`,
  intent сохранялся, но планшетная квитанция ошибочно превращала шесть
  владельцев в 1/0. Значит, первый hotfix закрыл только snapshot-ветку, а
  production-сбой возникает позже. Подготовлен диагностический `1.52.207`:
  он пишет в системный журнал только класс исключения и traceback без request,
  entity, URL, токена и параметров. Следующий шаг: полный gate, выпуск,
  установка и один живой цикл с обязательным возвратом `25,0°C`.

- 2026-09-03: подготовлен HACS `1.52.206` для живого дефекта общей цели.
  Production `1.52.205` принимал четыре точные команды Home Assistant, но при
  временной ошибке следующего снимка общий обработчик переписывал все шесть
  владельцев в ложные 1/0 `dispatched_not_accepted`. Исправление сохраняет
  точную карту принятия и оставляет операцию ожидающей свежего чтения без
  повтора команды. Два регрессионных теста прошли красную и зелёную стадии:
  второй запрещает подтверждать новую цель по старому снимку.
  Отдельно подтверждено: Yandex.Station `3.21.4` принимает установку `25°C`
  для кондиционера комнаты Игоря, но через 10 секунд продолжает сообщать
  `27°C`; это внешний read-back gap, а не отказ Hausman Hub. До публикации и
  установки нужны полный gate, commit, Release, backup, config check и restart.

- 2026-09-03: GitHub Actions для HACS больше не падает до exhaustive browser
  gate из-за отсутствующего каталога артефактов. Workflow передаёт закрытый
  временный каталог runner через `QA_ARTIFACT_ROOT`; регрессионный тест прошёл
  красную и зелёную стадии. Product files релиза `1.52.206` не изменены.

- 2026-09-02: после живого воспроизведения планшетной цели подготовлен hotfix
  HACS `1.52.202`. Причина: локальный admin OAuth имел доступ к climate runtime
  и typed action, но `GET /capabilities` ошибочно принимал только tablet-группу.
  Теперь read-only discovery использует уже существующую локальную климатическую
  границу; внешний администратор и локальный обычный пользователь сохраняют
  `403`, POST-права не менялись. Независимо пройдены полный серверный набор,
  browser baseline и 703 из 703 UI-действий с digest
  `99f2c84e7949c2a2b0ad78681220c991b43988eff17bc3f79970763b16c9b5a9`.
  Android `1.0.271` совместим без production-правок. Reviewer и security audit
  дали PASS. Следующий шаг: commit, выпуск, точечный deploy и живой round-trip
  цели кабинета 25,0 -> 25,5 -> 25,0°C.

- 2026-09-02: подготовлен HACS `1.52.201`. Надёжная климатическая capability
  публикуется только после полной проверки внешнего keyring, main ledger,
  sidecar и anchor; ошибки хранения липко закрывают action, recovery,
  reservation и dispatch до перезапуска. CAS-конфликт остаётся неаварийным,
  degraded runtime отдаёт последнюю безопасную копию без фоновой записи.
  Полная QA проверила 703 из 703 интеракций в 15 состояниях, browser baseline
  19 из 19, manifest 38 исходных точек и content digest
  `c0ab8aa35c03d22665ef81a6f2f1544e239bba401bf6d59b50621365c0893453`.
  Live read-only навигация прошла 10 из 10 разделов с TLS leaf/SPKI pin,
  точным origin, заблокированными service workers и нулём отправленных
  мутаций или утечек. Полный local gate, reviewer и security audit дали PASS.
  Release, deploy и живой климатический round-trip ожидают фиксации выпуска.

- 2026-08-30: HACS `1.52.195` расширил режим «Вне дома»: живой системный
  сценарий запускает вложенные сценарии всего света и кондиционеров,
  выключает чайник с подогревом и подсветкой, две вытяжки, два увлажнителя и
  два телевизора, затем закрывает горячую и холодную воду. Охрана, сеть,
  холодильники, датчики протечки и оповещения не отключаются. Только точный
  `system-away-turn-off` вправе выключать ручной свет, остальные автоматизации
  сохраняют прежний ручной приоритет. Открытие замка Aqara A100 добавлено в
  managed-контроллер тамбура и использует существующий дневной или вечерний
  профиль. Commit/tag `ef7ab38`, Release `v1.52.195`, GitHub Actions
  `33318612015` успешен. Локальный gate прошёл 1866 тестов, 4 skipped; после
  rebase профильный набор прошёл 178 тестов и 15 подтестов. Production
  installed/latest равны `v1.52.195` после двух config check и одного restart;
  Scenario Health `healthy`, Node-RED 22.0.2 connected, source hash тамбура
  `0551ee02fc052a99a2e802054b8aaeaa1ada5885b927b90eb3cc8d2aca3414f9`
  синхронизирован как scenario/flow revision 7. Command-free dry-run подтвердил
  30 действий света, 5 кондиционеров и 13 действий ухода; отдельная проверка
  разрешила системное выключение ручного света без физической команды.
  Автоматическое открытие воды не включено: `directionVerified=false`,
  `automaticOpenAllowed=false`, два датчика душевой остаются `unknown`.
  Клапаны после restart восстановили достоверное состояние `open`. Новая копия
  не создавалась из-за заполненного хранилища; откат: protected backup
  `6d01ac53` либо точная установка `v1.52.194`.

- 2026-08-30: HACS `1.52.194` добавил для штор числовое свойство
  `current_position` в каталог условий сценариев. Это позволило безопасно
  обработать привод кабинета: у нижнего механического упора он сообщает 7-8%,
  а повторная команда позиции 0% раньше могла развернуть мотор на открытие.
  Четыре общие ветви теперь закрывают гостиную, кухню и комнату Алисы,
  revisions `4/3/4/4`. Кабинет закрывают три отдельные ветви с теми же
  событиями сумерек, света в комнатах и света в общих зонах, но только при
  `current_position > 20`; их revisions равны 0. Все семь определений прошли
  command-free dry-run. Живой тест открыл кабинет до логических 100%, первое
  выполнение закрыло его до 8%, повторное было пропущено без квитанций, после
  чего 32 секунды позиция оставалась 8%. Общий сценарий отдельно подтвердил
  закрытие остальных трёх штор до 0%. Release `v1.52.194`, commit `bcc5f20`,
  GitHub Actions `33318348229` успешен. Локально прошли 1864 теста, 4 skipped,
  critical branch coverage 76% и Chromium 19/19. Production использует
  installed/latest `v1.52.195`, который является прямым потомком `1.52.194`
  и содержит исправление; config check HTTP 200, один restart, Scenario
  Health `healthy`.

- 2026-08-30: HACS `1.52.193` устранил ложную классификацию комнатных штор
  как внешних ворот. Общая подпись каталога `Шторы и ворота` больше не входит
  в identity; специальные типы, конкретные имена и entity ID настоящих ворот
  сохраняют усиленную защиту. Регрессионный тест воспроизводит production
  `device_type=cover` и общую capability. Commit/tag `bc4bce0`, Release
  `v1.52.193`, GitHub Actions `33316835437` успешен. Полный gate прошёл 1862
  теста, 4 skipped, critical branch coverage 76% и Chromium 19/19. Production
  использует installed/latest `v1.52.193` после config check и одного restart;
  Scenario Health `healthy`, Node-RED 22.0.2 подключён. Четыре сценария
  закрытия содержат гостиную, кухню, кабинет и комнату Алисы. Живые запуски
  подтвердили приём четырёх действий. Для кабинета применяется точная позиция
  0%, но мотор после нижнего механического упора публикует остаточные 7%; его
  `lower_stroke_limit` оставлен `RESET`, поскольку `SET` блокировал открытие.
  Моторные `upper_stroke_limit=SET` кухни и кабинета сохраняют прежние
  физические 80% как новый логический максимум 100% для всех источников команд.

- 2026-08-30: HACS `1.52.192` исправил последний конфликт ручного приоритета
  тамбура: ручная люстра теперь блокирует только команды и параметры самой
  люстры, а отдельные точки продолжают работать по присутствию. Для остальных
  сценариев консервативная блокировка всей ветки взаимозаменяемых источников
  сохранена. Commit/tag `15c0384`, Release `v1.52.192`, GitHub Actions
  `33316357964` успешен. Локально прошли 1862 теста, 4 skipped, 230
  критических тестов с покрытием 76% и Chromium 19/19. Production использует
  installed/latest `v1.52.192` после двух config check и одного restart;
  Scenario Health `healthy`, Node-RED 22.0.2 подключён, 3 managed flows
  синхронизированы. Read-only smoke прошёл с максимальной задержкой 102 мс.
  Живой запуск `56289` подтвердил реальную команду `points_on`, пока команды
  люстры были пропущены как `manual_light_already_on`; следующий сигнал
  подтвердил точки уже включёнными. Откат: protected backup `6d01ac53` либо
  точная установка `v1.52.191`.

- 2026-08-30: HACS `1.52.191` объединил автоматику тамбура и малого коридора в
  управляемые Node-RED контроллеры. Тамбур использует дневной нейтральный
  профиль, зависимый от уличной освещённости вечерний профиль, 10 минут
  отсутствия и плавное выключение; зеркало работает по расписанию 23:00-01:00.
  Малый коридор ждёт 5 минут, плавно гасит свет и не включается автоматически
  с 00:00 до рассвета. Служебная подача питания отделена от физического
  выключателя, а автоматическое выключение требует подтверждённого владения.
  Commit/tag `fd710bf`, Release `v1.52.191`, Actions `33315169433` успешен.
  Production source hashes: тамбур
  `399a39d89a4b745c901b0201fe9510eb0a754106b49c1ef1a85c61359c1022c4`,
  малый коридор
  `ce2580a1a8616b313b832d4da4c7648c4d01e5ce6b65d1fabf0ae1ac15672a44`.
  Семь прежних конфликтующих сценариев малого коридора выключены; осталось
  55 сценариев, 46 включены. Реле малого коридора периодически проходит
  Zigbee interview и даёт `unknown -> on` без контекста команды. Контроллер
  безопасно считает существующее включение ручным и не забирает его себе до
  чистого физического цикла off/on.

- 2026-08-30: HACS `1.52.190` исправил сценарий
  `system-shower-comfort-controller`. При любом обычном входе с выключенным
  светом дневная и вечерняя ветки теперь включают подсветку шкафа вместе с
  выбранным светом, а вытяжка включается сразу по присутствию без ожидания
  120 секунд. Влажность выше 55% остаётся независимым условием запуска;
  выключение после 5 минут устойчивого отсутствия при нормальной влажности
  сохранено. Корневая причина пропуска вытяжки: второй канал устройства имел
  имя с фрагментом `свет`, поэтому общий приоритет ручного освещения ошибочно
  считал его лампой. Стабильный target `entity_afef5df0e0cae309` теперь явно
  классифицируется как неосветительная нагрузка. Commit/tag `fac1b75`, Release
  `v1.52.190`, GitHub Actions `33311451359` успешен. Локально прошли 1852
  теста, 4 skipped, critical branch coverage 76% и Chromium 19/19. Production
  использует installed/latest `v1.52.190` после config check и одного restart;
  Scenario Health `healthy`, Node-RED 22.0.2 подключён, новый source hash
  `4ecf6735e3350c89116c9e1ec56f649fc9c6ba420ca884dcd43347bbc8bb3257`
  синхронизирован как scenario/flow revision 3. Read-only smoke passed,
  максимальная задержка 107 мс. Production dry-run запланировал основной свет,
  шкаф и вытяжку без `manual_light_already_on`; физические команды не
  отправлялись. Новая резервная копия не создалась при 52 локальных копиях и
  43,9 ГБ, поэтому откат опирается на protected backup `6d01ac53` либо точную
  установку `v1.52.189`.

- 2026-08-30: HACS `1.52.189` опубликован и развёрнут как policy-only hotfix
  поверх `1.52.188`. Два синтетических HACS browser-baseline переименованы из
  путей с `tablet` в пути с `hacs`; Git подтвердил R100 и совпадающие blob,
  пиксели и UI не менялись. Это закрывает запрет публичного репозитория на
  Android/tablet materials. Commit/tag `cf12457`, Release `v1.52.189`,
  GitHub Actions `33304627416` успешен. Полный gate снова прошёл 1851 тест,
  4 skipped, critical branch coverage 76% и Chromium 19/19. После третьего
  restart installed/latest равны `v1.52.189`; config check, Scenario Health,
  Node-RED и read-only smoke зелёные, максимальная задержка 89 мс, записей
  Hausman в system log нет. Production-настройки revision 2/3 и 54 сценария,
  52 включённых, пережили restart без изменений. Физических команд не было.

- 2026-08-30: HACS `1.52.188` опубликован и развёрнут в production вместе с
  contracts `0.63.0`. Основной релиз `1.52.187` добавил достоверность
  reported/effective state, durable manual и automation ownership света,
  restart-safe delayed off душевой и вытяжки, полный idempotency/receipt
  protocol и строгий серверный envelope managed Node-RED. После первого
  restart реальный Node-RED `GET /flow/{id}` выявил, что сервер не возвращает
  пустой `configs`; hotfix `1.52.188` (`a0f07ed`) принимает только отсутствие
  этого пустого поля, сохраняя запрет непустых, неверно типизированных и
  неизвестных полей. Tag и Release `v1.52.188` опубликованы, GitHub Actions
  `33303232150` успешен. Локально прошли 1851 тест, 4 skipped, critical branch
  coverage 76% при пороге 75%, Chromium 19/19 и contracts validator 77
  schemas/167 fixtures. Использован полный backup `cd0ffd3c`; выполнены два
  restart, второй потребовался для production hotfix. Installed/latest равны
  `v1.52.188`, config check, Scenario Health, Node-RED и read-only smoke
  зелёные, максимальная задержка 87 мс, записей Hausman в system log нет.
  Managed sources душевой и тамбура синхронизированы до revision 2. Только
  люстра тамбура переведена на `auto_turn_on` с прогревом 3 секунды, две
  остальные power-связи сохранены. Создан обычный sunrise-сценарий кухни,
  два старых protected расписания отключены; итог 54 сценария, 52 включены.
  Физические команды, canary и power-cycle не выполнялись. Штора кабинета
  остаётся заблокированной до появления сущности Home Assistant.

- 2026-08-27: HACS `1.52.185` опубликован и развёрнут в production с отдельным
  представлением выбранных
  данных managed Node-RED-алгоритма. Над системным multi-select появился блок
  `Выбрано: N`: каждая строка показывает устройство и различимые комнату или
  свойство, имеет отдельное удаление, весь набор можно очистить одной кнопкой.
  Значения, временно отсутствующие в каталоге, не теряются при изменении
  доступной части списка и явно помечаются как недоступные. Browser-тест
  сначала воспроизвёл отсутствие отдельного представления, затем проверил
  счётчик, подписи, удаление и очистку. Commit и annotated tag указывают на
  `1678ee8`, Release опубликован без assets, GitHub Actions `33077543877`
  успешен. Повторно использован protected full backup `6d01ac53`. После точной
  установки, config checks и одного restart installed/latest равны
  `v1.52.185`; 53 из 53 сценариев включены, Scenario Health `healthy`,
  Node-RED 22.0.2 подключён и показывает два managed flow. Live assets содержат
  новый блок выбора. Read-only smoke прошёл с максимальной задержкой 101 мс,
  записей Hausman в system log нет. Физические команды не отправлялись.

- 2026-08-27: HACS `1.52.184` опубликован и развёрнут в production для
  корректного представления
  управляемых Node-RED-сценариев и стабильной прокрутки редактора. Секундная
  пауза `safe_placeholder`, которая нужна только для совместимости схемы,
  больше не считается пользовательским действием и не затирает сохранённое
  описание устройств. Вместо ложного «1 действие» виден динамический план
  Node-RED. Положение каждой колонки и горизонтальной полосы восстанавливается
  после локальной и фоновой перерисовки. Регрессионные тесты сначала
  воспроизвели оба дефекта, затем прошли. Commit и annotated tag указывают на
  `260fb4f`, Release опубликован без assets, GitHub Actions `33068253295`
  успешен. Повторно использован protected full backup `6d01ac53`. После
  точной установки, config checks и одного restart installed/latest равны
  `v1.52.184`; 53 из 53 сценариев включены, Scenario Health `healthy`,
  Node-RED 22.0.2 подключён и показывает два managed flow. Read-only smoke
  прошёл с максимальной задержкой 102 мс, все 12 служебных сущностей доступны,
  записей Hausman в system log нет. Физические команды не отправлялись.

- 2026-08-27: HACS `1.52.183` опубликован и развёрнут в production для
  исправления наложения текста в
  выборе движка редактора сценария. Общее правило кнопок задавало
  `white-space:nowrap`, поэтому многострочные описания Hausman и Node-RED
  выходили из карточек и пересекались. Карточки получили локальный
  `white-space:normal`, безопасную минимальную ширину и перенос внутри
  границ. Browser-тест проверяет обе карточки на переполнение, вложенность и
  интервал между заголовком и описанием. Commit, annotated tag и Release без
  assets указывают на `3969a41`; GitHub Actions `33065225032` успешен.
  Повторно использован protected full backup `6d01ac53`. После точной
  установки, config checks и одного restart installed/latest равны
  `v1.52.183`; 53 из 53 сценариев включены, Scenario Health `healthy`,
  Node-RED 22.0.2 подключён. Live CSS содержит перенос и ограничение карточек,
  read-only smoke прошёл с максимальной задержкой 102 мс, все 12 служебных
  сущностей доступны, записей Hausman в system log нет. Физические команды не
  отправлялись.

- 2026-08-27: HACS `1.52.182` опубликован и развёрнут в production для
  исправления съехавшей иконки в бейдже `Hausman · конструктор` и
  `Node-RED · код`. Tag и Release без assets указывают на `4b0852a`, GitHub
  Actions `33062946670` успешен. В `1.52.181` CSS уменьшал только внешнюю
  коробку `ha-icon` до 15 px, а Home Assistant продолжал рисовать внутреннюю
  Material-иконку штатным размером 24 px. Теперь заданы
  `--mdc-icon-size:15px`, блочное отображение, нулевая line-height и безопасное
  ограничение переполнения. Геометрический browser-тест проверяет поля 7 px,
  промежуток 5 px и вертикальный центр. Использован свежий protected full
  backup `6d01ac53` от этого же дня. После точной установки, config check и
  одного restart installed/latest равны `v1.52.182`; 53 из 53 сценариев
  включены, Scenario Health `healthy`, Node-RED 22.0.2 подключён, live CSS
  содержит внутренний размер 15 px. После нового telemetry-отчёта Android все
  12 служебных сущностей доступны, записей Hausman в system log нет.
  Физические команды не отправлялись.

- 2026-08-27: HACS `1.52.181` опубликован и развёрнут в production. Annotated
  tag и Release без assets указывают на `8e610d5`; GitHub Actions
  `33059483823` успешен. Перед установкой создан защищённый full backup
  `6d01ac53` только в `hassio.local`, 882913280 байт: автоматическая копия с
  недоступным `KeeneticSSD` была отклонена и не использовалась. После точной
  установки, config checks и одного restart installed/latest равны
  `v1.52.181`, 12 служебных сущностей доступны, 53 из 53 сценариев включены,
  Scenario Health `healthy`, Node-RED 22.0.2 подключён. Live assets содержат
  полный каталог, отступ карточки и симметричную Hero-геометрию. Read-only
  smoke passed, физических команд не было.

- 2026-08-27: нижнее обрезание активной комнаты в Hero воспроизведено и
  исправлено для HACS `1.52.181`. Геометрический browser-тест показал верхний
  inset `2 px`, нижний `-2 px`: кнопка 44 px начиналась после верхнего padding
  внутри полосы той же высоты. Контейнер увеличен с 50 до 54 px, поэтому
  сверху и снизу осталось по 2 px. Для hover добавлен локальный `transform:none`
  с достаточной специфичностью, иначе общее правило кнопок поднимало вкладку
  на 1 px. Проверены 34 panel-теста и 19 Chromium-тестов; светлый широкий и
  тёмный планшетный visual baseline обновлены, accessibility baseline зелёный.

- 2026-08-27: каталог сценариев HACS приведён к Android. Фильтр «Все» теперь
  показывает полный ответ backend, включая защищённые системные сценарии, а
  строка `Показано N из M` честно обновляется при поиске и фильтрации. Причиной
  меньшего количества было явное клиентское исключение `activationKind=system`.
  Отступ заголовка карточки от иконки закреплён на 14 px: общее позднее правило
  `.scenario-icon` больше не расширяет локальную иконку поверх сеточного зазора.
  Полный Python gate: 1734 теста, 4 skipped; browser gate: 19 тестов. Visual и
  accessibility baseline обновлены и проверены.

- 2026-08-27: подготовлен HACS `1.52.181` с безопасным встроенным редактором
  managed function Node-RED. Исходник читается и сохраняется по отдельному
  contract-first API с optimistic locking, пробным запуском без физических
  команд, read-back и восстановлением прежней версии при ошибке. В каталоге
  добавлен фильтр `Node-RED`, карточки показывают движок и формат редактирования,
  а также ручной, автоматический, гибридный или системный запуск. Редактор и
  стили разнесены по ограниченным модулям. Полный gate: 1734 Python-теста,
  19 browser-тестов и visual/accessibility baseline прошли. Production deploy,
  tag и release пока не выполнялись.

- 2026-08-27: HACS `1.52.180` устраняет цветовое мигание люстры тамбура при
  импульсах датчиков. Живой operation journal подтвердил, что прежний
  function-flow при каждом `motion_changed` отправлял промежуточную
  температуру, затем целевую, даже когда яркость уже совпадала с профилем.
  Новый расчёт независимо сравнивает фактические яркость и Kelvin: совпавший
  профиль возвращает пустой план, разошедшийся параметр исправляется отдельно,
  промежуточная температура остаётся только для запуска погашенной люстры с
  устаревшим Zigbee-кешем. Добавлены четыре регрессионные ветви. Local release
  gate прошёл 1732 теста, 4 skipped, и все выпускные проверки.

- 2026-08-26: HACS `1.52.175`, commit `9ad6c8d`, устраняет обрезание рамки
  активной страницы в нижней навигации Hero. Прокручиваемая полоса получила
  горизонтальный внутренний отступ и scroll padding; браузерный тест проверяет
  обе крайние вкладки. Полный gate: 1720 тестов, 4 skipped; Chromium 17/17.
  Production обновлён с использованием свежего backup `e153e033`, config
  check и restart прошли. Живой CSS содержит исправление, installed/latest
  равны `v1.52.175`, ошибок Hausman нет.

- 2026-08-26: HACS `1.52.174`, commit `3adc780`, исправляет название и
  иконку панели в боковом меню Home Assistant. Живая регистрация после
  restart подтверждает `title=Hausman`, `icon=mdi:home-heart` и модуль
  `1.52.174`. Перед установкой создан backup `e153e033`; config check прошёл,
  installed/latest равны `v1.52.174`, ошибок Hausman в system log нет.
  Полный release gate: 1720 тестов, 4 skipped.

- 2026-08-26: HACS `1.52.173`, commit `320e9e8`, разрешает удалять только
  отключённые protected-сценарии. На production удалены 4 устаревших правила,
  39 оставшихся привязаны к комнатам. «Закрыть шторы» подтверждённо содержит
  оба доступных привода, гостиная и кухня; health API вернул `healthy`.

- 2026-08-26: HACS `1.52.172`, commit `4516137`, возвращает кондиционер в
  automatic после успешного ручного выключения с Hausman. 148 профильных
  тестов прошли. Release опубликован, installed/latest в Home Assistant равны
  `1.52.172`, config check прошёл. Runtime Кабинета подтверждён read-only.

- 2026-08-26: HACS `1.52.171` выпущен и развёрнут. В инвентаризации
  устройства появился редактор имён отдельных линий, свойств и событий.
  Подпись сохраняется в entity registry Home Assistant, поэтому после
  обновления видна и в Hausman. `entity_id`, ссылки сценариев и физические
  команды не меняются; сброс возвращает исходное имя интеграции. Проверены
  84 профильных теста, 16 Chromium-тестов, проверка пакета и конфигурация
  Home Assistant до установки. После одного restart installed/latest равны
  `v1.52.171`.

- 2026-08-26: подготовлен HACS `1.52.170` для безопасного создания черновика
  сценария обычным текстом. Новый API принимает текст и разрешённые
  `@`-упоминания, передаёт AI только публичный каталог, а затем локально
  проверяет ответ по обычным правилам сценариев. Черновик всегда выключен,
  не сохранён и не отправляет физических команд. Если запрос неоднозначен,
  возвращаются вопросы. Панель получила ввод текста, picker упоминаний и
  голосовой ввод браузера с явным сообщением о обработке речи браузером.
  Pin: contracts `0.60.0` (`acce7df`). Проверки: полный pytest 1723 passed,
  4 skipped, 1009 subtests; browser suite 16 passed; JS syntax и Python
  compilation прошли. Release, push, deploy и физические команды не запускались.

- 2026-08-26: подготовлен HACS `1.52.169` с направленными связями питания.
  Политика `auto_turn_on` включает источник перед любой Hausman-командой,
  подтверждает состояние `on`, ждёт `warmupSeconds` от 0 до 30 и затем
  отправляет исходную команду. Источник остаётся включённым; зависимое
  устройство может быть логически выключено и сохранять питание. Прежняя
  политика `requires_on` сохранена. Недоступный источник блокирует команду.
  Панель получила редактор в `Настройки -> Связи питания`, защиту ревизией,
  отмену локальных изменений и отображение исчезнувших сущностей. Полный
  сброс очищает связи. Pin: contracts `0.59.0` (`5189a02`). Проверки:
  local release gate 1709 тестов, 4 skipped; critical runtime 151 тест и 77%
  branch coverage; Chromium 15 из 15, включая планшетный экран редактора.
  Release, push, deploy и физические команды не запускались.

- 2026-08-26: HACS `1.52.168` исправляет найденный финальным release-line
  validator разрыв: runtime pin поднят до contracts `0.58.1` (`b0f5811`).
  Опубликованный контракт теперь включает все уже используемые HACS поля
  `revision`, `contentRevision`, readiness, health и `scenario_changed`
  вместе с `roomIds`. Vendored schemas совпадают с contracts source. Release
  `v1.52.168` опубликован, commit `2245c9b`. Production обновлён после backup
  `0907efca`, трёх valid config checks и одного restart; installed/latest
  равны `v1.52.168`, пять read-only API отвечают 200, все 11 sensor Hausman
  доступны, ошибок интеграции нет. Аудит: `docs/RELEASE_AUDIT_1.52.168.md`.

- 2026-08-26: подготовлен HACS `1.52.167` для 20 улучшений сценариев.
  Backend хранит `roomIds`, читает legacy `roomId`, проверяет комнаты по
  живому каталогу и сохраняет прежнюю область при деградации каталога.
  Конфликт ревизий сообщает изменённые комнаты и действия, событие
  `scenario_changed` перечисляет изменённые поля. HACS UI получил выбор
  нескольких комнат, явный «Весь дом», группировку целей по комнате, типу,
  физическому устройству и команде, локальные фильтры, предпросмотр,
  восстановление после ошибок, выборочное дублирование и пакетные операции.
  Pin: contracts `0.58.0` (`828b288`). Полный gate: 1702 теста, 4 skipped;
  critical runtime 148 тестов и 77% branch coverage; Chromium 13 из 13.
  Tag/Release `v1.52.167` опубликован, release commit `623c98d`. Production
  backup `0907efca` защищён локально и содержит Home Assistant с базой.
  После трёх valid config checks, точной установки и одного restart
  installed/latest равны `v1.52.167`; пять read-only API отвечают 200,
  все 11 sensor Hausman доступны, ошибок интеграции нет. Аудит:
  `docs/RELEASE_AUDIT_1.52.167.md`.

- 2026-08-25: HACS `1.52.165` опубликован и развёрнут: при частичном dashboard-ответе
  без readiness главная честно показывает «Состояние обновляется» и сохраняет
  планшетную компоновку. Исправление закрывает последний frontend fallback,
  на котором остановился предыдущий gate. Проверены целевой panel-тест, 13
  Chromium tests и полный local release gate: 1693 tests, 4 skipped. Перед
  deploy создан full backup `545e7403`; два config checks и один restart
  приняты, installed/latest равны `v1.52.165`, все sensor Hausman доступны.

- 2026-08-25: подготовлен HACS `1.52.164` для устойчивого списка сценариев.
  ETag сопоставляется до тяжёлого расчёта списка, поддерживает weak и
  составной `If-None-Match`, а `304` не содержит JSON-тела. Ревизия базового
  содержимого кешируется до записи или удаления сценария. Единый обработчик
  возвращает конфликт ревизии во всех mutation routes; входящее `revision`
  отклоняется, сервер принимает только `expectedRevision`. Проверены 74
  scenario-service tests и синтаксис Python. Полный local gate дошёл до 1693
  тестов, 4 skipped и остановился на одном существующем frontend fallback
  тесте readiness, не связанном с API сценариев. Release, push и deploy не
  запускались. Клиентские K2-K6 из аудита переданы отдельным handoff в root
  workspace.

- 2026-08-25: подготовлен HACS `1.52.163` для главной страницы. Шапка и
  Hero теперь используют единое состояние связи с Home Assistant: при сбое
  остаётся красное «Нет связи с Home Assistant», а зелёный статус возвращается
  только после двух последовательных удачных чтений. Промежуточное состояние
  явно сообщает о восстановлении связи. Обновлены cache-версии frontend и
  visual baselines. Проверены профильные panel/UI-state tests, HACS package
  check и 13 Chromium tests. Release, push и deploy не запускались. C14
  Node-RED cutover отложен, Node-RED не менялся.

- 2026-08-25: завершён C13 плана сценариев. При ошибке записи HACS
  восстанавливает последнюю целую registry; если компенсационная запись тоже
  не удалась, сервис блокирует новые запуски. При unload старый экземпляр
  отменяет активные `restart` и `queued`-запуски, отклоняет поздние команды и
  не воспроизводит их после загрузки нового экземпляра. Тестами закрыты семь
  веток C13: обрыв storage write, reload во время `restart` и `queued`, SSE
  gap, пустой каталог, stale evidence с частичным receipt и повтор
  correlation ID. Повреждённая сохранённая registry по-прежнему отклоняется.
  Feature commit `d6de0cf`. Полный gate: 1690 tests, 4 skipped; critical
  runtime: 140 tests и 77% branch coverage при минимуме 75%. Release, push,
  deploy и физические команды не запускались. Следующий серверный пункт C14
  требует отдельного разрешения владельца на live cutover Node-RED.

- 2026-08-25: завершён C12 плана сценариев. HACS хранит до 128 последних
  замеров для пяти backend-путей и рассчитывает P50/P95 без payload и ID:
  список 100 мс, каталог 500 мс, dry-run 500 мс, storage 250 мс, последний
  результат 50 мс. Внешняя сборка каталога ограничена timeout 2 секунды;
  при timeout остаётся последний целый каталог и readiness `degraded`, а
  список и metadata редактора читаются из локального состояния. Registry
  ограничен 512 сценариями, правила и текстовые поля проверяются по schema.
  Дополнительно синхронизирован vendored retention snapshot события
  `scenario_changed`. Commits `b85f41b` и `fc92062`. Полный gate: 1681 test,
  4 skipped; финальный профиль после обработки validation error: 318 test,
  4 skipped. Release, push и deploy не запускались. Следующий серверный
  пункт: C13, fault-injection и восстановление.

- 2026-08-24: подготовлен HACS `1.52.162` для исправления надёжности
  сценариев. При restart вытесненный запуск больше не попадает в журнал как
  cancelled, а его вложенный вызов учитывается как пропущенный шаг. Если
  сценарий включил устройство и следующий шаг завершился ошибкой, исполнитель
  сразу запускает предусмотренный далее turn_off того же устройства и помечает
  квитанцию safety_cleanup. List payload закреплён тестом как полный источник
  definition для планшета, включая порядок шагов и выключенные сценарии.
  Cache-версии HACS frontend синхронизированы с `1.52.162`. Проверки: 1665
  tests, 4 skipped; полный local release gate passed. Kimi read-only review
  был запрошен, но не вернул результат за контрольное время и был остановлен;
  Codex выполнил финальный self-review. Commit подготовлен к push; deploy не
  запускался.

- 2026-08-23: HACS UI-линия объединена с актуальной `main` и выпущена как
  `1.52.161`. Планшетные представления главной, освещения, климата,
  безопасности, энергии, устройств и сценариев вошли без потери operational,
  energy anomaly, intercom confirmation и tablet power изменений основной
  ветки. Merge commits `d29488a` и `fa47abe`, tag/release `v1.52.161`, Actions
  `32616059573` success. Gate: 1662 tests, 4 skipped, 119 critical tests,
  75% branch coverage, 13 Chromium tests. Production backup `f251700b`
  защищён в local и KeeneticSSD по 914780160 bytes. После трёх valid config
  checks, exact install и одного restart installed/latest равны `v1.52.161`,
  config entry loaded, 92 assets match, Hausman errors 0. После 41 секунды
  прогрева финальный smoke passed: max latency 97 ms, unavailable 10,
  pending operations 0. Android, backend, API и contracts не менялись.
  Подробности: `docs/RELEASE_AUDIT_1.52.161.md`.

- 2026-08-23: HACS `1.52.160` завершил backend направления 31. Локальный
  bounded POST публикует батарею и питание планшета как два HA sensor,
  operation journal фиксирует `tablet_power_update`, физических команд API
  не отправляет. Стандартный blueprint реализует 40/80 и fallback `power on`
  при недоступном sensor/plug. Contracts `0.57.0` (`c4a948b`). UI не менялся,
  только cache-bust. Gate: 1656 tests, 4 skipped, 119 critical tests, 75%
  branch coverage, 13 Chromium tests. Actions `32615474963` success. Commit
  `974f942`, test-only `99f77e9`, release `v1.52.160` без assets. Production
  backup `b09b2e3c` защищён в local и KeeneticSSD по 908881920 bytes.
  Installed/latest `v1.52.160`, 88 assets match, live 39/80 journal smoke
  confirmed. Blueprint установлен, но не активирован: отдельной сущности
  умной розетки планшета в HA нет. Подробности:
  `docs/RELEASE_AUDIT_1.52.160.md` и
  `docs/TABLET_MAINTENANCE_AND_CHARGING.md`.

- 2026-08-23: HACS `1.52.159` закрыл operational readiness направления 30.
  Daily smoke имеет redacted `OnFailure` alert, добавлены SLO, P0-P3,
  rollback, release blocker, Definition of Done и monthly UX systemd timer.
  UI не менялся, только cache-bust. Gate: 1650 tests, 4 skipped, 119 critical
  tests, 75% branch coverage, 13 Chromium tests. Commit `8b58f54`, tag/release
  `v1.52.159` без assets, Actions `32611390522` success. Production backup
  `11c0460b` защищён в local и KeeneticSSD по 907284480 bytes, база включена.
  После двух valid config checks, HACS refresh/install и одного restart
  installed/latest равны `v1.52.159`, config entry loaded, 88 assets match.
  Final smoke passed: max latency 115 ms, fresh snapshots, pending 0,
  physical commands false. Подробности:
  `docs/RELEASE_AUDIT_1.52.159.md` и `docs/OPERATIONS_SUPPORT_SLO_DOD.md`.

- 2026-08-23: HACS `1.52.158` завершил HACS/operations часть направления 28
  и развёрнут в production. Добавлены dual-role read-only smoke, ежедневный
  persistent timer, fault matrix, soak/rollback policy и два изолированных
  virtual helper без физических связей. Полный gate: 1646 test, 4 skipped;
  critical runtime 119 test и 75% branch coverage; browser gate 13 test.
  Actions `32608714394` success. Backup `9f28fd76` находится в local и
  KeeneticSSD, по 905635840 байт. После restart offline count переходно вырос
  10 -> 58 и восстановился до 10 за 41 секунду; повторный smoke passed,
  max latency 123 ms, pending operations 0, физических команд 0. Production
  installed/latest `v1.52.158`, config entry loaded, 88 assets совпадают,
  ошибок Hausman нет. Commit/tag `b03a25b`.

- 2026-08-23: HACS `1.52.157` завершил направление 27 и развёрнут в
  production без изменения принятой компоновки интерфейса. Playwright
  проверяет шесть репрезентативных экранов, visual baselines, клавиатуру,
  focus, переполнение и axe accessibility. Полный gate: 1641 test,
  4 skipped; browser gate 13 test; critical runtime 119 test и 75% branch
  coverage. Actions `32607274228` success. Backup `3db97187` сохранён в
  local и KeeneticSSD, по 905011200 байт. Production installed/latest
  `v1.52.157`, config entry loaded, 88 assets совпадают, ошибок Hausman нет.
  Commit/tag `9926419`. Аудит: `docs/RELEASE_AUDIT_1.52.157.md`.

- 2026-08-23: HACS `1.52.156` завершил направление 25 и развёрнут в
  production. CI запускает 119 safety tests и блокирует release при branch
  coverage ниже 75% для water safety, scenario executor/service и climate
  deviation guard. Полный gate: 1641 test, 4 skipped; Actions `32600039797`
  success. UI не менялся, только cache-bust. Backup `4ad0c1ff` находится в
  local и KeeneticSSD, по 930426880 байт. Production installed/latest
  `v1.52.156`, config entry loaded, 88 assets совпадают, ошибок Hausman нет.
  Commit/tag `6398765`. Аудит: `docs/RELEASE_AUDIT_1.52.156.md`.

- 2026-08-23: HACS `1.52.155` завершил направление 24 и развёрнут в
  production. Исполняемый registry закрепляет общий продуктовый язык HACS и
  Hausman Android `1.0.251` для header, card, detail, control, notice, picker
  и empty state. Gate проверяет общую fixture, semantic tokens,
  keyboard/focus, Esc, focus return, aria-live, 900/1280/1440/1920 px,
  light/dark и zoom 125/150%. Визуальная компоновка не менялась, только
  cache-bust. Commit/tag `7f98228`, Actions `32599293350` success; полный
  gate: 1641 test, 4 skipped. Backup `b10cfc4f` находится в local и
  KeeneticSSD, по 930068480 байт. Production installed/latest `v1.52.155`,
  config entry loaded, 88 assets совпадают, ошибок Hausman нет. Аудит:
  `docs/RELEASE_AUDIT_1.52.155.md`.

- 2026-08-22: HACS `1.52.154` завершил backend направления 20 и развёрнут
  в production.
  Numeric catalog actions публикуют runtime bounds/unit, а scenario dry-run
  формирует redacted человекочитаемый report с условиями и шагами,
  `commandSent=false` и без entity ID. Synthetic gate покрывает motion light,
  sunset curtains и humidity fan без service calls. Existing `roomId` из
  contracts 0.51.0 подтверждён тестами. Pin: contracts `0.56.0` (`078ebd0`).
  Frontend layout не менялся, выполнен только cache-bust версии. Commit/tag
  `029e4ab`, Actions `32595001493` успешен; полный gate: 1640 passed,
  4 skipped, 1003 subtests. Backup `80718cd9` находится в local и
  KeeneticSSD, по 928481280 байт. Production installed/latest
  `v1.52.154`, config entry loaded, 88 assets совпадают. Три live dry-run
  вернули `commandSent=false`, journal sequence осталась 1394. Аудит:
  `docs/RELEASE_AUDIT_1.52.154.md`.

- 2026-08-22: направление 17 завершено в HACS `1.52.153`. Vendor timeout и
  circuit breaker изолируют медленные media/remote/Yandex services. Домофон
  требует явного подтверждения, поддерживает безопасный dry-run, bounded hold
  15 секунд, read-back release receipt и redacted durable audit без entity ID.
  Production dry-run `1.52.152` выявил пропущенную передачу флага через
  `ScenarioService`; hotfix `1.52.153` исправил её и добавил прямой тест.
  Full gate: 1639 passed, 4 skipped, 999 subtests; Actions `32591818087`
  success. Backup `89c8919a` защищён в local и KeeneticSSD. Production
  installed/latest `v1.52.153`, config checks зелёные, safe live dry-run
  HTTP 200 и не отправляет физическую команду. Коммиты `59f4bcb`, `e793219`.
  Детали: [[LLM_WIKI/Sessions/2026-08-22-codex-hacs-vendor-safety-1.52.153]].

Last updated: 2026-08-22 (HACS 1.52.154, scenario editor maturity).

## Current work

- 2026-08-22 (Codex): HACS `1.52.151` завершил backend направления 16.
  Contracts `0.54.0` добавляет коллекцию до 16 счётчиков, совместимый legacy
  primary, календарные окна day/week/month с timezone и устойчивую аномалию
  мощности. Feature release `1.52.149` прошёл 1626 tests, но live-проверка
  нашла неверную передачу календарного окна и `NameError` managed climate tick
  при deviation guard. Hotfixes `1.52.150` и `1.52.151` закрыли обе регрессии;
  финальный gate: 1625 passed, 4 skipped, Actions `32586366768` success.
  Production installed/latest `v1.52.151`, config entries loaded, календарная
  история `Europe/Moscow` вернула 12 series и 237 points, все 88 assets
  совпали. После двух климатических интервалов журнал Hausman пуст. Rollback:
  свежий защищённый backup `74abc5cf`. Совместимый Android до клиентского
  релиза: `1.0.246` build `250`. Аудит:
  `docs/RELEASE_AUDIT_1.52.151.md`.

- 2026-08-22 (Codex): HACS `1.52.148` завершил backend направления 15.
  Capability-aware каталог различает relay, dimmer, CCT и RGB, добавляет
  adaptive/night policies и закрывается fail-closed при неполном runtime.
  Ordered batch до 64 действий возвращает receipt по каждому устройству;
  journal сохраняет source, trigger target, recovery marker и target ID.
  UI-компоновка 1.52.147 сохранена. Pin: contracts `0.53.0`; feature commit
  `e80b5f8`, release/tag `e9ec853`, `v1.52.148`. Full gate: 1618 passed,
  4 skipped, 995 subtests; Actions `32578493597` success. Backup `3a6e9bfd`
  защищён в local и KeeneticSSD, по 922449920 байт. Три config check, install
  и один restart успешны; config entry loaded, 88 frontend assets совпадают,
  ошибок Hausman нет. Live matrix закрыла relay, dimmer+CCT, offline RGB и
  shadow 24 действий без физического executor. Принудительный recovery outage
  не создавался, marker покрыт integration-тестом. Совместимый Android:
  `1.0.246` build `250`. Аудит: `docs/RELEASE_AUDIT_1.52.148.md`.

- 2026-08-22 (Codex): HACS `1.52.147` с финальной плотностью главной и
  карточек устройств опубликован и развёрнут. Главная синхронизирует высоту
  меню, центральной ленты и правой активности, вместе раскрывает энергию и
  освещение, увеличивает Hero до 280 px и прокручивает ближайшие события
  внутри компактной панели. Физические карточки устройств уменьшены до
  высоты 150 px, изображения 56 px и заголовка 16 px. Feature commits:
  `305c83c`, `ddd6d6e`, `5a2387f`; release commit/tag `aa4ef59`,
  `v1.52.147`. Полный gate: 1609 passed, 4 skipped; Actions `32576732490`
  успешен. Backup `aab91cba` подтверждён в `hassio.local` и
  `hassio.KeeneticSSD`, по 921815040 байт, обе копии защищены. Два config
  check и один restart успешны. Production installed/latest равны
  `v1.52.147`, config entry loaded, Dashboard и 11 ближайших событий
  отвечают HTTP 200. Journal остался на sequence 998, climate guard сохранил
  monitor revision 1 и не вооружён. Все 88 frontend assets совпадают с
  release, ошибок Hausman в system log нет. Android, backend, API,
  contracts и storage не менялись. Аудит: `docs/RELEASE_AUDIT_1.52.147.md`.

- 2026-08-22 (Codex): технический HACS `1.52.146` опубликован и развёрнут.
  Pin обновлён
  до contracts `0.52.1` (`5e89ca2`), который синхронизирует Android
  production-screen consumer `1.0.245` build `249`. API, schemas, runtime,
  storage и monitor-политика 1.52.145 не менялись. Release commit/tag
  `e2b103a`, `v1.52.146`; Actions `32575357359` успешен, полный gate:
  1609 passed, 4 skipped. Backup `8e8032ca` включает Home Assistant, базу,
  10 add-ons и `media/share/ssl`; защищённые копии по 921159680 байт
  подтверждены в local и KeeneticSSD. Два config check и один restart
  успешны. Production installed/latest равны `v1.52.146`, monitor revision 1
  сохранился, guard events отсутствуют, journal sequence 981. Все 88 frontend
  assets совпадают с release, ошибок Hausman в system log нет. Frontend
  layout сохранён, выполнен только cache-bust. Аудит:
  `docs/RELEASE_AUDIT_1.52.146.md`.

- 2026-08-22 (Codex): HACS `1.52.145` опубликован и развёрнут в production
  для direction 14.A.
  Restart-safe guard вооружается только после принятого `off` и наблюдаемого
  read-back `off`; первый снимок не может создать ожидание. Monitor фиксирует
  отклонение без executor, enforce ждёт grace, делает не более 1-5 точных
  повторов `off` с cooldown и затем пишет единственную эскалацию в durable
  operation journal. Настройки по stable managed AC IDs сохраняются через
  command-free local-admin API с optimistic locking, default devices пуст.
  Runtime отдаёт guard status и существующий cooldown surface. Pin: contracts
  `0.52.0` (`da932b1`). Feature commit/tag `459b2c2`, `v1.52.145`; Actions
  `32573556407` успешен, полный gate: 1609 passed, 4 skipped. Backup
  `87e95767` включает Home Assistant, базу, 10 add-ons и `ssl/share/media`;
  защищённые копии по 920258560 байт подтверждены в `hassio.local` и
  `hassio.KeeneticSSD`. Два config check и один restart успешны. Production
  installed/latest равны `v1.52.145`; для `detskaia_air_conditioner`
  сохранён только `monitor` с grace 120 s, cooldown 300 s и max retries 3.
  Он не владеет executor и не отправляет повторный `off`. Journal sequence
  до и после deploy равна 977, ошибок Hausman в system log нет. Все 88
  frontend assets, три contract schema и guard service совпадают с release.
  Frontend layout не менялся, только cache-bust. Аудит:
  `docs/RELEASE_AUDIT_1.52.145.md`.

- 2026-08-22 (Codex): HACS `1.52.144` опубликован и развёрнут в production для направлений 12-13.
  Room settings v1 хранит canonical type/icon, порядок и видимость с
  optimistic locking. PUT применяет иконки Area Registry с read-back и
  rollback при ошибке storage. Dashboard публикует `order`, `visible`,
  `type`, per-room CO2/PM2.5/tVOC и `roomId` сценария. Старое preferences
  storage мигрирует с пустым room document. Pin: contracts `0.51.0`
  (`d54aa32`). Full backup `e70bddac` подтверждён в local и KeeneticSSD,
  `check_config` зелёный до и после install, выполнен один restart. Production
  GET вернул 13 комнат с revision 0, PUT не вызывался, Area Registry не
  менялся. Dashboard и 88 frontend assets проверены; journal sequence 941,
  текущий deploy добавил 0 operations. Frontend layout не менялся, выполнен
  только cache-bust.

- 2026-08-22 (Codex): HACS `1.52.143` опубликован и развёрнут для
  направления 18. Water safety v1 хранит policy и latch, использует
  quorum/debounce, требует verified direction и notify recipient до
  автозакрытия, подтверждает physical read-back и повторяет
  закрытие после restart, если авария осталась latched.
  Автооткрытие всегда запрещено; ручное открытие и latch clear
  блокируются при stale/unknown safety state. Критические операции
  пишутся в durable journal. Pin: contracts `0.50.0` (`92b050a`).
  Frontend layout не менялся. Release commit/tag `775ec8a`, Actions
  `32565864180` успешен. Backup `c79a46e3`: по 922449920 байт в
  local и KeeneticSSD. Production monitoring включает 4 сухих
  датчика, 2 открытых редуктора и notify recipient. Оба
  direction-test: `commandSent=false`, `readBack=open`; journal sequence 827
  не изменилась. `directionVerified=false`, `autoCloseEnabled=false`,
  поэтому физических команд не было. Все 88 frontend assets совпали
  с release; schemas зелёные, 9 shadow сохранены, ошибок Hausman
  в system log нет. Аудит: `docs/RELEASE_AUDIT_1.52.143.md`.

- 2026-08-22 (Codex): HACS `v1.52.142` опубликован и развёрнут в production.
  Scenario list соответствует новому contracts `0.49.0` (`5478341`) и
  возвращает серверные `activationKind`, `roomId`, `protected`, `nextRun`,
  `lastResult`, `temporaryException`. Защищённые system-сценарии нельзя
  удалить, legacy storage мигрирует безопасно. Feature commit `04064d2`,
  release commit/tag `3a1fb54`, Actions `32562805751` успешен. Финальный
  staged gate: 1580 tests, 4 skipped. Перед deploy создан full backup
  `7a4b14cd`, по 921886720 байт в `hassio.local` и `hassio.KeeneticSSD`.
  После двух config checks и одного restart installed/latest равны
  `v1.52.142`, config entry loaded. Production response из 39 сценариев и
  operation journal проходят JSON Schema; 24 сценария защищены как system,
  9 shadow-сценариев сохранили `commandMode=shadow`. Все 88 frontend JS/CSS
  совпадают с release по SHA-256, UI baseline не менялся. Ошибок Hausman в
  system log нет. Node-RED не изменялся и остаётся физическим владельцем до
  завершения soak.

- 2026-08-22 (Codex): HACS `v1.52.139`, корректировки `v1.52.140` и
  `v1.52.141` опубликованы и развёрнуты в production поверх UI baseline
  `1.52.138`. Добавлен fail-safe `commandMode=shadow`, contracts pin
  `0.48.1`, redacted journal marker и безопасный fallback уведомлений.
  Восемь веток ванной и расширенный away из 24 действий включены только в
  shadow; старые live-дубли ванной, away и системных сумеречных штор
  выключены. Node-RED flows не менялись, SHA-256 остался
  `7e2d3830ea4712531e0898a78c6c9bc53c42f80ff0b415a1831f03018a36a316`.
  Full backup `fe727f76` хранится в `hassio.local` и `hassio.KeeneticSSD`,
  по 914862080 байт, включает Home Assistant, базу, 10 add-ons и
  `ssl/share/media`; agent errors отсутствуют. Финальный staged gate:
  1574 tests, 4 skipped; Actions `32561158937` успешен. Production
  installed/latest `v1.52.141`, config entry loaded, Dashboard, upcoming,
  scenarios, journal и panel отвечают HTTP 200, journal schema valid, 27
  frontend assets совпадают. Stale target теперь даёт HTTP 400 вместо 500.
  Контрольный away shadow-run не вызвал физических сервисов и остановился
  fail-safe на `power_source_unavailable`; journal сохранил
  `command_mode=shadow`, confirmed false. До 7-14 суток сравнения Node-RED
  остаётся владельцем физических веток.

- 2026-08-22 (Codex): HACS `v1.52.138` опубликован и развёрнут в production.
  Главная получила независимые компактный и развёрнутый режимы энергии и
  освещения, полноразмерную панель «Активность», ближайшие события с
  пропуском cancellable-запусков и исправление стартовой прокрутки Hero.
  Feature commit `2921547`, release commit/tag `1596a74`, GitHub Release без
  assets. Local release-gate: 1563 tests, 4 skipped; Actions `32558198896`
  успешен. До установки создан full backup `22410676`, по 919613440 байт в
  `hassio.local` и `hassio.KeeneticSSD`, с Home Assistant, базой, 10 add-ons
  и `ssl`. Выполнены два успешных config checks и один restart. После deploy
  installed/latest равны `v1.52.138`, config entry loaded, все 12
  релевантных сущностей доступны, journal schema valid, 29 frontend assets
  совпадают с release. Ошибок и traceback нет, остаётся одно известное
  предупреждение о расхождении уличных источников температуры. Физические
  команды не отправлялись. Android, backend, API, contracts и storage не
  менялись.

- 2026-08-22 (Codex): только HACS frontend главной получил два локальных
  режима для карточек «Показания энергии» и «Освещение». Компактный режим
  сохранён по умолчанию, развёрнутый показывает уже загруженные реальные
  метрики, источники энергии и физические световые устройства. Выбор каждой
  карточки независим и хранится только в localStorage браузера. Панель
  «Активность» имеет ширину и высоту левого меню, содержит до 12 записей и
  собственную прокрутку. Ниже основных карточек подключены ближайшие события
  с действием «Пропустить» для cancellable-запусков. Исправлена стартовая
  прокрутка Hero при появлении содержимого ниже fold. Feature commit
  `2921547` поверх HACS `1.52.137`. Browser QA: 1440x1400, rail и Activity
  238x1360 px, overflow и runtime errors отсутствуют. Full pytest: 1566
  passed, 4 skipped, 985 subtests. Release, push и deploy не выполнялись;
  Android, backend, API, contracts и storage не менялись.

- 2026-08-22 (Codex): корректирующий HACS `v1.52.137` закрывает найденный
  release-line drift contracts `0.47.0`: синхронизированы Dashboard schema,
  water-meter schema и multi-source energy fixture. External validator
  подтверждает 52 schemas, 97 fixtures, 39 OpenAPI paths и точное совпадение
  HACS source. Feature commit `e51d58f`, release commit/tag `538bcc4`.
  Full release-gate: 1562 tests, 4 skipped; Actions `32555953795` успешен.
  Перед deploy создан full backup `6b14607f`, 918568960 байт, Home Assistant,
  база, 10 add-ons и `ssl`, failed-списки пусты. После явной установки,
  двух config checks и одного restart installed/latest `v1.52.137`, config
  entry loaded, 12 сущностей доступны, journal schema valid, 27 frontend
  assets совпадают с release, system log чист. Runtime сценариев и UI не
  менялись, физические команды не отправлялись.

- 2026-08-22 (Codex): HACS `v1.52.136` опубликован и развёрнут в
  production поверх принятого визуального baseline `1.52.135`. Сценарии
  получили bounded queue, trigger timing, единый snapshot условий,
  fail-closed проверку критичных действий, идемпотентность, partial-исходы,
  ограничение вложенности и редактированный operation journal. Контракты
  закреплены на `0.47.0` (`57a1b04`). Feature commit `e4bca0a`, release
  commit/tag `02206a7`, GitHub Release без assets. Full release-gate: 1562
  tests, 4 skipped; Actions `32554721280` успешен. Перед deploy создан full
  backup `f86830d3`, 917975040 байт, Home Assistant, база, 10 add-ons и
  `ssl`, failed-списки пусты. После явной установки, двух успешных
  `check_config` и одного restart installed/latest равны `v1.52.136`,
  config entry loaded, 12 сущностей доступны, журнал проходит JSON Schema,
  27 из 27 frontend assets совпадают с release. В system log нет ошибок и
  traceback, остаётся одно известное climate WARNING. Физические команды не
  отправлялись.

- 2026-08-22 (Codex): HACS `v1.52.135` опубликован и развёрнут в
  production. Выпуск объединяет последние планшетные правки освещения и
  нормализацию масштаба главной относительно остальных страниц. Feature
  commits `e6e2cf4`, `0a65de4`, release commit/tag `12c4609`, GitHub Release
  без assets. Staged release-gate: 1548 tests, 4 skipped; Actions
  `32552485736` успешен. Перед установкой создан full backup `362c8bac`:
  Home Assistant, база, 10 add-ons и папка `ssl`, копии по 916940800 байт в
  `hassio.local` и `hassio.KeeneticSSD`. После явной установки,
  `check_config` и одного restart installed/latest равны `v1.52.135`;
  30 из 30 изменённых frontend assets совпадают с release, все 10 сущностей
  доступны, ошибок и traceback Hausman Hub в system log нет. На живом
  viewport 2560x1306 главная и «Освещение» имеют одинаковую ширину `main`
  1600 px, cache version `1.52.135`, горизонтального overflow нет. В Chrome
  нет JS exceptions; сессия пользователя пишет пять известных network 403
  для scope-limited `capabilities/events`, при этом capabilities с read token
  отвечает 200. Android, backend, API, contracts и storage не менялись.

- 2026-08-22 (Codex): изменён только HACS frontend. Главная больше не
  увеличивает планшетные размеры на широком viewport до 125%: размеры
  шрифтов, отступов и пяти рядов зафиксированы в базовом масштабе. Внешняя
  геометрия совпадает с обычными страницами: rail 238 px, свёрнутый rail
  88 px, промежуток 28 px, поля main 28/34/56 px и предел 1600 px.
  Визуально проверены dark 1280x800 и 1600x1000, light 1600x1000, оба
  состояния rail. Горизонтального overflow и пересечений карточек нет.
  Full pytest: 1550 passed, 4 skipped, 984 subtests. Версия остаётся
  `1.52.134`; release, push и deploy не выполнялись. Android, backend, API,
  contracts и storage не менялись.

- 2026-08-21 (Codex): только HACS frontend синхронизирован с последними
  изменениями планшета Android 1.0.241-1.0.243. В компактной карточке «Дом
  сейчас» значение центрировано между верхней иконкой и нижней подписью.
  Комната освещения показывает физические устройства отдельными широкими
  карточками: нумерованные каналы `_1/_2/_3` управляются раздельно, люстра
  использует штатный образ потолочного светильника, яркость и температура
  света размещены рядом в компактных range-контролах. Используются только
  опубликованные snapshot controls и opaque target IDs, неизвестные actions
  по-прежнему блокируются. Визуально проверены dark 1600x1000 для главной и
  кабинета. Full pytest: 1550 passed, 4 skipped, 984 subtests. Версия остаётся
  `1.52.134`; release, push и deploy не выполнялись. Android, backend, API,
  contracts и storage не менялись.

- 2026-08-21 (Codex): HACS `v1.52.134` опубликован и развёрнут в production.
  Изменение задачи ограничено frontend: главная получила общий с обычными
  страницами предел `max-width: 1600px`, центрирование и горизонтальные поля
  34 px. Внутренняя планшетная сетка не менялась. Feature commit `a1e06e8`,
  release commit/tag `287f1e4`, GitHub Release без assets. Staged release-gate:
  1547 tests, 4 skipped; Actions `32514457615` успешен. Перед установкой
  создан защищённый full backup `2ac3c237` в local и KeeneticSSD. После
  явной установки, `check_config` и одного restart installed/latest равны
  `v1.52.134`; 28 изменённых frontend-файлов совпадают с release, 10
  сущностей интеграции доступны, ошибок Hausman Hub в журнале нет. На живом
  viewport 2560 px главная и «Освещение» имеют одинаковую ширину `main`
  1600 px и центрируются. Android, API и contracts не менялись. Tag собран
  поверх актуального `origin/main`, поэтому содержит ранее принятый backend
  commit `4c79d8d`; в этой задаче backend не редактировался.

- 2026-08-21 (Codex): исправлен свет туалета по движению. Seed
  `system-toilet-light-motion` теперь включает только основной канал днём;
  добавлены отдельные вечерний seed до 23:00 и ночной seed с 23:00 до
  фактического рассвета. Ночью включается только дополнительный канал,
  через 8 минут оба канала выключаются; все ветки блокируются режимом
  «не дома». Живая конфигурация production приведена к тем же трём
  сценариям, старый дублирующий контроллер Node-RED отключён с резервной
  копией. Dry-run после 23:00: дневная и вечерняя ветки skipped, ночная
  completed и содержит единственный turn_on для дополнительного света.
  Физические команды во время проверки не отправлялись. Профильный тест:
  `tests/test_system_scenario_seeds.py` - 9 passed. Версия и release не
  менялись.

- 2026-08-21 (Codex): HACS `v1.52.133` с планшетной главной и киоском
  опубликован и развёрнут в production. Главная повторяет пять рядов,
  Hero с навигацией комнат, три центральные карточки, сценарии, энергию и
  свет; правая колонка переключается между компактным и подробным режимом
  вместе с rail. Киоск повторяет планшетные пропорции Hero, метрик,
  сценариев, погоды, состояния дома и домофона. Белые кнопки меню сохранены,
  используются только реальные данные HACS. Визуально проверены light/dark,
  1280x800 и 1600x1000, expanded/collapsed и kiosk без overflow и runtime
  errors. Feature commits `e69d482`, `c24b339`; release commit/tag
  `b69d84c`, GitHub Release без assets. Full pytest до выпуска: 1548 passed,
  4 skipped, 984 subtests; staged release-gate: 1546 passed, 4 skipped;
  Actions `32507160930` успешен. Перед deploy создан защищённый full backup
  `a2c07631` в local и KeeneticSSD. После явной установки, успешного
  `check_config` и одного restart installed/latest равны `v1.52.133`, panel
  отвечает 200, все 31 изменённый asset совпадает с release, 10 сущностей
  интеграции доступны, ошибок интеграции нет. Backend, API, contracts,
  Android и storage не менялись.

- 2026-08-21 (Codex): только HACS frontend приведён к переданному светлому
  референсу и тёмному blueprint без изменений backend, API и Android. Hero
  стал крупнее и спокойнее, оставлены только точки карусели; справа собраны
  погода и состояние дома. Климат, свет и безопасность стоят в одном ряду,
  избранные сценарии - в четырёх компактных карточках. Энергия показывает
  механический счётчик с анимацией, целое переданное значение без красного
  десятичного барабана, а обе кнопки теперь светлые и читаемые. Один красный
  attention-блок размещён под энергией, дубли и большой блок ближайших
  событий убраны. Визуально проверены light/dark 1600x1000, 900x1000 и
  640x1000. `python3 -m pytest -q`: 1548 passed, 4 skipped, 984 subtests;
  профиль Dashboard: 118 passed, 82 subtests. Версия, release, push и deploy
  не выполнялись.

- 2026-08-21 (Codex): HACS `v1.52.132` опубликован и установлен в production.
  Release commit/tag `d17ccb0`, GitHub Release без assets. Hero больше не
  выводит техническое стандартное имя `Home Assistant`: оно безопасно
  заменяется на «Дом», пользовательские имена сохраняются. В выпуск также
  вошёл P0 Dashboard (`7d48306`): спокойный Hero без служебных счётчиков,
  единый attention-блок и объединённая карточка климата. Full staged
  release-gate: 1546 passed, 4 skipped. Перед deploy создан full backup
  `c04771be`; после одного restart Core update entity сообщает
  installed/latest `v1.52.132`, панель отвечает 200 с новым cache version,
  10 сущностей интеграции доступны. Ошибок интеграции в журнале нет;
  остаётся одно штатное WARNING о расхождении физической и сервисной
  температуры на 3.5 C, команды не блокируются.

- 2026-08-21 (Codex): P0 визуального аудита Dashboard реализован только в
  HACS frontend, commit `7d48306`. Hero больше не показывает технические
  счётчики комнат и устройств, offline и readiness собраны в один явный
  attention-блок, а климат и управление общей целью объединены в одну
  карточку. Вторичный текст увеличен, мягкий amber используется для attention.
  Проверки: `python3 -m pytest -q tests/test_hausmanhub_panel.py` - 31 passed,
  50 subtests; ключевые UI-сценарии Dashboard зелёные. Вошло в опубликованный
  и развёрнутый HACS `v1.52.132`.

- 2026-08-21 (Codex): HACS `v1.52.131` опубликован и установлен в production.
  Release commit/tag `6094da0`, GitHub Release без assets. Выпуск возвращает
  белые карточки меню в светлой теме, активный пункт остаётся светло-голубым.
  Полный `check_local_release.py` зелёный. Перед deploy создан full backup
  `aba7f6a4`; после одного restart Core update entity сообщает
  installed/latest `v1.52.131`, панель отвечает 200, 10 сущностей интеграции
  доступны, ошибок `custom_components.hausman_hub` в системном журнале нет.

- 2026-08-21 (Codex): commit `8ad91de` возвращает светлой теме белые
  карточки пунктов меню. Активный пункт остаётся светло-голубым. Два
  профильных теста панели зелёные. Релиз, push и deploy не выполнялись.

- 2026-08-21 (Codex): HACS `v1.52.130` опубликован: release commit/tag
  `e8e8d94`, GitHub Release без assets. Карточка ручных показаний энергии
  использует механический индикатор, кнопка «Настройки» имеет читаемый белый
  текст на тёмном синем фоне. Полный `check_local_release.py` пройден до
  публикации; deploy не выполнялся.

- 2026-08-21 (Codex): коммит `40ca8bb` меняет только HACS frontend: карточка
  ручного электросчётчика теперь похожа на физический счётчик - шесть чёрных
  разрядов и красная десятая кВт·ч с анимацией. Неясный текст «Расход цикла
  не определён» заменён понятной подсказкой до следующей передачи. Проверка:
  `pytest -q tests/test_hausmanhub_modal_theme.py` - 18 passed, 32 subtests.
  Backend, API, Android и production не затрагивались.

- 2026-08-21 (Codex): продолжен незавершённый этап 5 паритета только в
  `custom_components/hausman_hub/frontend/`, коммит `cf49af8`. Медиа:
  поиск, фильтры Все/Играют/ТВ/Колонки/Без связи, чипы комнат, карточки
  устройств и правая колонка состояния. Безопасность: быстрые фильтры
  Все/Требует внимания/Доступ/Без связи и полноэкранное предупреждение о
  протечке без физической команды. Целевые media/security тесты панели
  зелёные; полный `test_hausmanhub_panel_settings.py` завис после серии
  успешных кейсов и был остановлен. API, Python, контракты и Android не
  менялись. Следующий UI-пункт: этап 6 (Устройства) либо визуальный прогон
  панели с актуальным Home Assistant.

- 2026-08-21 (Kimi): HACS `1.52.129` - этап 4 паритета (климат как
  планшет): обзорная вкладка - сетка карточек комнат 2 колонки (статус
  «Авто»/«Ручной режим»/«Без связи», крупная цель, строки «Сейчас» и
  «Цели», степперы температуры и влажности на карточке), поиск
  «Найти комнату», фильтры «Все/Авто/Ручной режим/Без связи». Справа
  новая колонка (модуль `hausman-hub-climate-side.js`): «Микроклимат»,
  «Оборудование», «Требует внимания», «История» (заглушка - API истории
  нет). Управление контуром (исключение устройств, ручной режим комнаты)
  переехало в шторку комнаты. panel.js и panel.css не тронуты.
  Ветка влита с main: внутри backend `1.52.121`-`1.52.127` (имена
  устройств в квитанциях, human-приветствие, мультипривязка счётчика).

- 2026-08-21 (Kimi): HACS `1.52.127` - счётчик энергии с несколькими
  источниками (contracts 0.44.0). `settings.sourceDeviceIds` (до 16,
  уникальные) побеждает legacy `sourceDeviceId`, при чтении старых
  документов список дополняется из одиночного id. `source.sources[]`
  несёт по каждому источнику deviceId/name/available/currentTotalKwh/
  state, агрегат суммирует доступные накопительные значения и зеркалит
  первый доступный в `source.deviceId`/`name`. Состав источников входит
  в signature reset_detected. Тесты: test_energy_meter (мультисумма,
  fail-closed валидация, миграция одиночного id).

- 2026-08-21 (Kimi): HACS `1.52.126` - приветствие дома по-человечески
  (contracts 0.43.0). `summaryStyle` human/numbers (human по умолчанию,
  старые документы дополняются при чтении), новые блоки сводки `outdoor`
  (первый доступный `weather.*`) и `low_battery`, до 6 блоков. Длинная
  речь делится на предложения до 180 символов (`split_speech`) и
  произносится частями с паузой 0.6 с. Из индекса убран gitlink
  worktrees/, ломавший boundary check. Тесты: test_voice_greeting
  (human-фразы, outdoor/low_battery, чанкование, миграция summaryStyle).

- 2026-08-20 (Kimi): HACS `1.52.124` - решение владельца «в активности
  видно, что за устройство». Сообщения executor'а включают имя устройства
  из каталога («Люстра кухни: новое состояние подтверждено.»), системные
  сид-сценарии получают `targetName` из живого каталога при создании,
  задвоения имён реестра HA сворачиваются (`_display_device_name`).
  Тесты: test_scenario_executor (тексты квитанций, дедуп имён),
  test_system_scenario_seeds (targetName из каталога).

- 2026-08-20 (Kimi): HACS `1.52.122` - этап 3 паритета (освещение и
  комнаты как планшет): у освещения правая колонка (быстрые сценарии,
  сейчас включено, требует внимания), поиск и фильтры комнат, выключение
  света комнаты с подтверждением; у комнат чипы «Климат/Свет/Офлайн» на
  карточках и колонка «Обзор дома / Комнаты без связи / Быстрый доступ /
  История дома». Новые модули `hausman-hub-lighting-side.js` и
  `hausman-hub-rooms-side.js` подключены из своих разделов, panel.js и
  panel.css не тронуты. По ревью владельца: слайдер цели климата на
  главной стал тонким 1 в 1 с планшетом (значение «25°»), карточка
  «Показания энергии» перекомпонована плитками; лимит energy.css в
  тесте поднят 26→27 КиБ. Патчем `1.52.125`: слайдер цели починен от
  раздувания глобальным `input { min-height:46px }` (градиент на
  дорожке). Backend не затронут.

- 2026-08-20 (Kimi): HACS `1.52.121` - этап 2 паритета (главная как
  планшет): приветствие по времени суток, hero-карусель комнат со стрелками
  и точками, пресеты цели климата 24/25/26, градиентный слайдер цели,
  степпер со скрытыми доступными подписями, лента избранного, модалка
  «События · N», погода с датчиком, «Дом сейчас», лента активности из SSE.
  Ветка ребейзнута на main после backend-релиза 1.52.120. Backend-gap:
  активность не переживает перезагрузку панели (нет API истории событий).

- 2026-08-20 (Kimi): HACS `1.52.120` - ползунки света в диалоге устройства.
  Каталог и executor получили `set_brightness_percent` (0-100%, масштаб в
  HA-native 0-255) и `set_color_temperature` (кельвины, read-back с допуском
  75 на округление mireds). `dashboard_snapshot._light_control_details`
  объявляет контролы по `supported_color_modes`, а не по живому атрибуту:
  выключенная Zigbee-люстра (brightness=None у off) тоже показывает
  «Яркость» и «Температура света». Корень дефекта: люстра тамбура
  `light.0xa4c138784e5cbcd1` умеет color_temp, но details приходили без
  control. Схема `dashboard-snapshot` v1: `deviceRangeControl.actionId`
  расширен до enum (contracts 0.42.0), матрица v1 получила контролы
  `brightness_percent`/`color_temperature` (contracts 0.41.0), vendored
  frontend JS и пиннутые счётчики обновлены синхронно. Тесты: snapshot
  (off-свет с color_temp, 158→«62%», onoff без контролов), executor
  (50%→brightness 128 с read-back, kelvin 3000→3003 confirmed).

- 2026-08-20 (Kimi): HACS `1.52.119` - этап 1 паритета интерфейса панели
  с планшетом (план `docs/design/HACS_TABLET_PARITY_PLAN_2026-08-20.md`,
  ветка `kimi/hacs-tablet-parity-2026-08-20`, только frontend/): палитра
  обеих тем по токенам Android, лёгкие тени, компактные hero, сетки в 2
  колонки, navigation rail со сворачиванием (профиль HA), часы и кнопка
  «Обновить» в шапке, daynight 07:00-22:00. Backend не затронут.

- 2026-08-20 (Kimi): HACS `1.52.115`-`1.52.118` - прогрев каталога после
  старта (1.52.115: повторные refresh через 1/3/8 с, warning при пустом
  каталоге; инцидент 19.08 с молчащими шторами), перенос остатков Node-RED
  в системные сценарии (1.52.116: `application/system_scenario_seeds.py`,
  13 seeds группы system - шторы закат/утро, протечки, свет по движению,
  вытяжки, «не дома»; идемпотентно, правки пользователя не затираются;
  спецификация `docs/migration/NODE_RED_REMAINING_SCENARIOS_2026-08-20.md`
  в workspace) и фикс планировщика (1.52.117: sunset/sunrise без смещения
  ронял перевзвод расписания - None теперь offset 0; контрольная попытка
  сидирования через 5 мин для поздних Zigbee2MQTT датчиков). Живое
  отключение перенесённых flows в Node-RED остаётся за владельцем
  (API :1880 отвечает 401). Владелец снял разделение треков 2026-08-20:
  Kimi делает и backend, и клиент.

- 2026-08-20 (Kimi): HACS `1.52.118` - живой разбор дефектов 1.52.117 на
  HA. Каталог считал сенсор текстовым, пока датчик unavailable после
  рестарта (числовость выводилась из живой строки состояния): «Ванная:
  вытяжка по влажности» не сеялась даже контрольной попыткой. Теперь
  числовость сенсора определяется по атрибутам (state_class
  measurement/total/total_increasing или unit_of_measurement;
  timestamp/date остаются текстовыми) - `scenario_catalog._state_property`.
  Прогрев каталога переведён на `async_create_background_task`, чтобы
  5-минутная контрольная попытка сидирования не блокировала bootstrap
  («Setup timed out for bootstrap waiting on catalog warm-up»). Тесты:
  unavailable-сенсор держит above/below, timestamp остаётся text.

- 2026-08-19 (Kimi): HACS `1.52.113` - брендовая правка без смены
  поведения. Видимый знак шапки панели (`hausman-hub-panel.js`) и
  киоск-панорамы (`hausman-hub-kiosk.js`) переименован из технического
  «HAUSMANHUB» в «HAUSMAN», как в планшетном приложении; проверка киоска в
  `tests/test_hausmanhub_panel_settings.py` обновлена. README переписан:
  вместо накопленных разделов «Что добавляется в версии» - краткое описание
  текущей версии и возможностей; строка `Текущая версия — **X**`
  синхронизирована с manifest.json (fail-closed `sync_readme_version.py`).
  Технические идентификаторы (домен `hausman_hub`, пути API, имена файлов и
  custom element) не менялись. Контрактные строки `safeMessage`
  (`contracts/v1/error-taxonomy.json`) и метки режимов климата в
  `public_climate_values.py` остаются «HausmanHub» - они заморожены
  контрактом v1/v12; переименование - отдельное решение backend-трека.
  Backend не менялся.

- 2026-08-17 (Kimi): HACS `1.52.112` на contracts `0.39.0` (`afd1552`)
  добавляет межсезонный режим кондиционеров: при свежей уличной температуре
  не выше `interseason_outdoor_max_c` (дефолт 22) работающий кондиционер
  выключается при delta ниже `interseason_cooling_start_gap` (дефолт 2.0),
  остановленный не стартует до превышения цели на этот запас; MINIMUM_RUN
  сохраняет MAINTAIN до истечения минимального времени работы. Новые причины
  стабильности `interseason_off`, `interseason_cooling_delayed`,
  `interseason_window_open`; опциональное календарное окно MM-DD с переходом
  через новый год; `interseason_window_open_off` глушит работающий
  кондиционер при открытом окне. Настройки хранятся в climate registry home,
  admin `home-environment` не затирает их у старых клиентов, новый публичный
  `GET/PUT /api/hausman_hub/v1/climate-season-settings` с expectedRevision
  отдаёт документ планшету. Панель: блок «Межсезонье: отдых кондиционеров»
  в карточке сигналов дома. Release commit и tag target `9646977`; GitHub
  Release опубликован без assets. Full pytest: 1494 passed, 4 skipped, 984
  subtests. Deploy на живой дом не выполнялся - шаг Codex; после deploy
  включить `interseason_enabled` и наблюдать soak.

- 2026-08-16 (Kimi): HACS `1.52.111` подключает панель к pagination/retention
  `hausman-hub-pagination-retention v1` (contracts `0.36.0`): новый модуль
  `frontend/hausman-hub-pagination.js` с pinned snapshot матрицы (5
  поверхностей), вендорской копией `contracts/v1/pagination-retention.json`
  и fail-closed тестом `tests/test_frontend_pagination_retention.py`.
  SSE-клиент (fetch-транспорт с Bearer token и `Last-Event-ID`) ведёт cursor
  последнего полностью обработанного события, очередь доставки ограничена
  32 сообщениями с восстановлением через gap flow, backoff reconnect
  ограничен 30 секундами, `hello`/`heartbeat` не попадают в историю.
  Gap flow: один snapshot refresh, новый stream ID, без повтора команд.
  Energy history разбивается на соседние окна `[from, to)` до 31 дня без
  дубля boundary point и без нулей вместо пропусков; operation journal
  читается keyset-pagerом (`before_sequence`, `has_more=false`). Backend не
  менялся.

- 2026-08-16 (Kimi): HACS `1.52.110` подключает панель к correlation ID
  `hausman-hub-correlation-surfaces v1` (contracts `0.35.0`): новый модуль
  `frontend/hausman-hub-correlation.js` с pinned snapshot матрицы (10 command
  surfaces, 5 notification surfaces), вендорской копией
  `contracts/v1/correlation-surfaces.json` и fail-closed parity-тестом
  `tests/test_frontend_correlation_id.py`. Команды панели (`_post`, климат,
  device actions, device maintenance, сценарии run/cancel) отправляют свежий
  неприватный ID `corr.panel.<32 hex>` в поле из матрицы; invalid ID
  блокируется до API-вызова; discovery-уведомления дедуплицируются по ID
  через bounded tracker (256). Backend не менялся.

- 2026-08-16 (Kimi): HACS `1.52.109` подключает панель к device feature
  matrix `hausman-hub-device-feature-matrix v1` (contracts `0.37.0`): новый
  модуль `frontend/hausman-hub-device-features.js` с pinned snapshot матрицы
  (19 типов, 24 control group, 41 binding, 25 уникальных action ID) и
  fail-closed parity-тестом `tests/test_frontend_device_feature_matrix.py`.
  Матрица загружается с `GET /api/hausman_hub/v1/device-features` только при
  объявленных capabilities metadata, иначе pinned snapshot без сетевого
  вызова. Действия в detail sheet - пересечение матрицы с runtime scenario
  catalog, неизвестный тип read-only, неизвестный control скрыт. Backend не
  менялся.

- 2026-08-16 (Kimi): HACS `1.52.108` добавляет общий UI state
  `hausman-hub-ui-state v1` (contracts `0.33.0`): новый модуль
  `frontend/hausman-hub-ui-state.js` с pinned snapshot семи golden fixtures
  (завендорены в `contracts/v1/ui-state/`) и fail-closed parity-тестом
  `tests/test_frontend_ui_state.py`. Панель проецирует loading/stale/offline
  на экран, запрещает физические команды в stale/offline/pending/disabled,
  confirmed принимает только receipt `confirmed=true` с read-back, optional
  slice изолирован от остального dashboard. Backend не менялся.

- 2026-08-16 (Kimi): HACS `1.52.107` переводит панель на canonical error
  taxonomy `hausman-hub-error-taxonomy v1` (contracts `0.34.0`): новый
  модуль `frontend/hausman-hub-error-taxonomy.js` с pinned snapshot
  инвентаря и fail-closed parity-тестом `tests/test_frontend_error_taxonomy.py`.
  Toast, notice и inline-ошибки используют canonical code и safe message,
  raw responseText и unknown details не рендерятся; conflict обновляет
  snapshot, pending читает существующую operation, автоматический повтор
  физических команд не добавляется. Backend не менялся.

- 2026-08-16 (Codex): HACS `1.52.106` завершает copy-release: интеграция
  называется `Hausman for Home Assistant`, а панель и пользовательские
  подсказки используют `Hausman Hub`; технический domain и API сохранены.
  Release commit и tag target `a8c7590`, GitHub Release опубликован без
  assets. Full local gate: 1422 passed, 4 skipped. Перед установкой принят
  config check и запрошен automatic backup. `update.install` явно установил
  `v1.52.106`, installed/latest совпали до restart. После единственного
  restart API Home Assistant не восстановился в контрольное время, поэтому
  post-restart проверки панели, runtime и system log не выполнены. Новые
  изменяющие команды не отправлялись.

- 2026-08-16 (Codex): HACS `1.52.104` добавляет безопасный путь для нового
  компьютера. В разделе «Система» панель открывает или копирует HTTPS-адрес
  текущего Home Assistant, а `docs/secure-local-access.md` объясняет проверку
  SHA-256 и установку только публичного корневого сертификата в Windows.
  HTTP, обход предупреждений браузера и автоматический импорт доверенного
  центра намеренно не добавлены. Release commit и tag target `c06b54b`,
  GitHub Release опубликован без assets. Full local gate: 1422 passed,
  4 skipped за 172.995 s, включая package и repository safety. Home Assistant
  не развёртывался и физические команды не отправлялись. Details:
  `docs/RELEASE_AUDIT_1.52.104.md`.

- 2026-08-15 (Codex): HACS `1.52.103` закреплён на contracts `0.37.0`
  (`643e97e`) и публикует защищённую read-only feature matrix: 19 типов
  устройств, 24 control group, 41 action binding и 25 уникальных action ID.
  Runtime scenario catalog остаётся источником фактических действий,
  неизвестные типы работают read-only, клиент не синтезирует action ID.
  Backend commit `6558af4`, release commit и tag target `23c3209`; GitHub
  Release опубликован. Full pytest: 1423 passed, 4 skipped, 971 subtests;
  staged gate: 1421 tests, 4 skipped; Actions `31887924256` success.
  Production backup `350aa3e0` включает Home Assistant 2026.8.2, базу, три
  папки и десять add-ons без ошибок. После штатных повторов HACS из-за
  медленного GitHub, двух config checks и одного restart installed/latest и
  panel равны `1.52.103`; 9 из 9 сущностей доступны, runtime fresh/managed,
  live matrix совпадает с fixture, system log чист. Физические команды не
  отправлялись. Details: `docs/RELEASE_AUDIT_1.52.103.md`.

- 2026-08-15 (Codex): HACS `1.52.102` закреплён на contracts `0.36.0`
  (`28e1f4e`) и публикует bounded policy для SSE, energy history и operation
  journal. SSE получил restart-safe stream ID, `Last-Event-ID` replay на 128
  событий и queue limit 32; gap требует перечитать snapshot. Journal использует
  exclusive `before_sequence`, page metadata и durable retention 512 без TTL.
  Energy history ограничена окнами `[from, to)` до 31 дня, 128 series и 8928
  points на series, а retention зависит от Recorder. Backend commit `c3b64df`,
  release commit и tag target `5dde51c`; GitHub Release опубликован. Full
  pytest: 1422 passed, 4 skipped; staged gate: 1420 tests, 4 skipped; Actions
  `31885789878` success. Production backup `0037d467` размером 900 915 200
  байт записан локально и на KeeneticSSD, обе копии защищены. После двух config
  checks и одного restart installed/latest и panel равны `1.52.102`; 9 из 9
  сущностей доступны, runtime fresh/managed, journal, energy history и SSE
  подтверждают новые лимиты, system log чист. Физические команды не
  отправлялись. Details: `docs/RELEASE_AUDIT_1.52.102.md`.

- 2026-08-15 (Codex): HACS `1.52.101` закреплён на contracts `0.35.0`
  (`0327f2c`) и проводит один optional correlation ID через публичные команды,
  receipts, SSE, operation journal, dashboard activity, alarms, device
  discovery и metadata сценарных уведомлений. Некорректный ID отклоняется до
  команды, старые клиенты остаются совместимыми. Backend commit `62945bf`,
  release commit и tag target `ae64952`; GitHub Release опубликован. Full gate:
  1417 passed, 4 skipped; Actions `31883493197` success. Production backup
  `87c14426` размером 900 362 240 байт находится локально и на KeeneticSSD,
  включает Home Assistant 2026.8.2, базу, три папки и десять add-ons. После
  установки, двух config checks и одного restart installed/latest и panel
  равны `1.52.101`; 9 из 9 сущностей доступны, runtime fresh/managed, active
  operations и blocked reasons 0, system log чист. Live SSE, dashboard и
  journal подтверждают correlation ID. Физические команды не отправлялись.
  Details: `docs/RELEASE_AUDIT_1.52.101.md`.

- 2026-08-15 (Codex): HACS `1.52.100` enforces the canonical API error
  taxonomy from contracts `0.34.0` (`7d4a2f9`) on strict tablet and climate
  routes. Caller exception text is never returned, unknown codes fail closed,
  request IDs are bounded and details use per-code allowlists. The first
  `1.52.99` production audit found one Home Assistant blocking-call warning
  from lazy packaged JSON loading. `1.52.100` preloads the taxonomy through
  the executor before API registration and the repeated live strict-error
  probe leaves the HausmanHub system log empty. Backend commits `1b1571b` and
  `ef98566`, release commit `43dc778`, tag and GitHub Release are published.
  Full gate: 1415 passed, 4 skipped; Actions `31881355990` success. Full
  rollback backup `b9e79fa4` is on KeeneticSSD, 870.88 MB, includes Home
  Assistant 2026.8.2, the database, three folders and ten add-ons. Final
  installed/latest and admin panel are `1.52.100`; all nine integration
  entities are available, runtime is fresh and managed, active operations and
  blocked reasons are zero. No physical command was sent. Details:
  `docs/RELEASE_AUDIT_1.52.100.md`.

- 2026-08-15 (Codex): HACS `1.52.98` publishes the exact `voice_greeting`
  `POST` test method plus request and receipt contract identities. Consumer pin
  is contracts `0.32.3` (`67acee1`), whose fixture set covers all ten Android
  production screens. The change is additive and older clients may ignore the
  optional metadata. Backend commit `438eadf`, release commit `c00215e`, tag
  and GitHub Release are published. Focused gate: 44 tests and 222 subtests;
  full gate: 1411 passed, 4 skipped and 929 subtests; Actions `31878143953`
  success. Production backup `214c8013` is full, includes the database and is
  stored locally plus on KeeneticSSD; a separate 1.52.97 component archive is
  available. Two config checks and one restart passed. Installed/latest,
  manifest and panel are 1.52.98, 104 cache refs are current and none remain on
  1.52.97. All nine integration entities plus the HACS update entity are
  available, runtime is fresh, active operations and blocked reasons are zero,
  and system log has no HausmanHub entries. No physical command was sent.
  Details: `docs/RELEASE_AUDIT_1.52.98.md`.

- 2026-08-15 (Codex): HACS `1.52.97` stops false curtain runs caused by
  Home Assistant device recovery. A state transition with a missing,
  `unknown` or `unavailable` side no longer matches scenario state triggers,
  while a real `off -> on` transition still does. Cover actions also skip the
  service call when the cover is already `closed` or `closing`. Full gate:
  1409 passed, 4 skipped; Actions `31843302664` success. Release commit
  `57f65dc`, tag and GitHub Release are published. Production backup
  `b5b1c72e` completed, config checks and one restart passed;
  installed/latest and 35 frontend cache markers are `v1.52.97`, all 10
  entities are available and the integration system log is clean. The three
  curtain scenarios remain disabled by the owner. No scenario or physical
  device command was sent. Details: `docs/RELEASE_AUDIT_1.52.97.md`.

- 2026-08-14 (Codex): HACS `1.52.96` makes an explicit device-card
  `turn_off` for a configured air conditioner persist device-level manual
  ownership before the physical command. A rejected action restores the prior
  automatic mode; scenario and contour commands retain their existing path.
  Dashboard cards and climate runtime expose Russian ownership labels, and the
  HACS device card, detail sheet and climate list render the same compact
  status. Consumer pin is contracts `0.32.0` (`9e29af7`). Full gate: 1407
  passed, 4 skipped, 926 subtests; Actions `31835035051` success. Release
  commit `34cf496`, tag and GitHub Release are published. Production backup
  `a8fe37bd` completed, three config checks and one restart passed;
  installed/latest are `v1.52.96`, runtime is fresh and managed. The live
  dashboard exposes four climate ownership cards; the two currently off units
  in Cabinet and Alice's room are shown as manual. Deployment verification
  was read-only and sent no physical command. Details:
  `docs/RELEASE_AUDIT_1.52.96.md`.

- 2026-08-14 (Codex): сценарий `Свет по движению малый коридор` переведён на
  солнечную адаптацию. Движение запускает свет после заката либо до заката при
  целочисленном показании уличного датчика не выше 400 лк. Яркость плавно
  снижается от 100% на фактическом закате до 25% к полуночи и растёт обратно
  до 100% к фактическому восходу. Повторное движение перезапускает 300-секундный
  таймер, затем реле выключается. HACS `1.52.94` добавил `sun.sun`, адаптивную
  команду и рабочие execution modes; `1.52.95` устранил гонку питания между
  последовательными шагами и подтверждает включение до длинной задержки.
  Release commits `56a5425` и `488a2eb`, Actions `31812569136` и
  `31814541329` success. Full gate 1.52.95: 1399 passed, 4 skipped. Production
  обновлён после automatic backups, config checks и по одному restart на
  выпуск; installed/latest и cache marker равны 1.52.95. Три сохранённых
  определения проходят final dry-run, текущий расчёт был 78% при минимуме 25%.
  Физические команды при проверке не отправлялись. Подробности:
  `docs/RELEASE_AUDIT_1.52.95.md`.

- 2026-08-14 (Codex): live-причина отсутствия управления Midea кабинета и
  комнаты Алисы была в родительских комнатах `manual`, хотя сами устройства
  уже имели `automatic`. Два штатных `set_room_mode automatic` вернули комнаты
  в контур без физической команды. Операции
  `57cd1f5512bf94f595b579986fac9776` и
  `20893a4c695e7cac22bc8e07c11bbea7` подтверждены readback. Ближайший managed
  tick применил поддержание к обоим Midea: `cool`, 27 °C, fan `low`, при
  сохранённой комнатной цели 25 °C. В следующем 75-секундном WebSocket-окне
  повторных climate service calls к этой паре не было. Runtime managed,
  commands enabled, authority `hausman_hub`, blockers и pending отсутствуют.
  `synchronize_home` не запускался, код, версия и deploy не менялись.

- 2026-08-14 (Codex): HACS `1.52.93` groups scenario entities by physical
  Home Assistant device and publishes room, type, capability and allowed
  property states. The separate picker has search plus combinable room and
  type quick filters. Motion exposes only `Движение` / `Нет движения`; the
  corridor chandelier is one physical card with `Освещение` and
  `Не беспокоить`. Consumer pin is contracts `0.31.0` (`ac54bf6`). Full gate:
  1392 passed, 4 skipped, 926 subtests; Actions `31794893988` success.
  Production was backed up, checked, installed and restarted once. Installed,
  latest and cache marker are `1.52.93`; the live catalog confirms the exact
  motion and chandelier grouping. Two one-count existing climate shadow/trial
  warnings appeared at startup; there are no scenario errors. No physical
  command was sent. Details: `docs/RELEASE_AUDIT_1.52.93.md`.

- 2026-08-14 (Codex): HACS `1.52.92` binds manual electricity readings to one
  explicit energy device. The projection reads only that source, rejects an
  unknown source, stamps each new history record and migrates old state with a
  nullable source. HACS always shows history and blocks submit/correct until
  the source is saved. Consumer pin is contracts `0.30.0` (`9137aea`). Full
  gate: 1385 passed, 4 skipped; Actions `31788813887` success. Production was
  backed up, checked, installed and restarted once. Installed/latest, admin
  panel and cache marker are `1.52.92`; dashboard has 13 rooms, 82 devices and
  2 energy sources, all 10 entities are available, and system log is clean. No
  physical command was sent. Details: `docs/RELEASE_AUDIT_1.52.92.md`.

- 2026-08-14 (Codex): HACS `1.52.91` replaces the large dashboard energy card
  with compact readings and a separate settings action. Meter and source
  settings live in a dedicated modal; the shorter full-width chart is followed
  by the enlarged device list. The right source column, duplicate summary and
  all-online card no longer consume space. Release commit `0b48307`; Actions
  `31784075602` success; full gate 1382 passed, 4 skipped. Production was backed
  up as `68484589` (825.3 MB), checked, installed and restarted. Installed,
  latest, admin panel and cache marker are `1.52.91`; 10 entities are available
  and the HausmanHub system log is clean. No physical command was sent.

- 2026-08-13 (Kimi implementation, Codex review): HACS `1.52.90` renders
  contract range controls directly in the opened device sheet. The Russian UI
  has a large current value, slider, 48 px step buttons, min/max/step labels
  and explicit Apply. Slider and step buttons only edit a draft; only Apply
  calls the fixed `set_value` action. Invalid bounds, action or opaque target
  fail closed, unavailable and busy devices are disabled, and range values are
  not duplicated as ordinary facts. Release commit `98e724e`; Actions
  `31737373543` success; full gate 1382 passed, 4 skipped. Light and dark real
  Chromium checks passed without overflow. Production was backed up as
  `b01ebb1c` (830.3 MB), config check passed, HACS installed/latest, admin panel
  and cache marker are `1.52.90`; a full restart completed normally. Live API
  has 13 rooms, 82 devices and 37 valid Russian range controls; system log is
  clean. The local and external backup are available and backup manager is
  idle. No physical device command was sent.

- 2026-08-13 (Codex): HACS `1.52.88` added bounded numeric device controls,
  safe `number.set_value` execution with range, step and read-back validation,
  plus Russian labels for the known live device capabilities. Contract pin is
  `hausmanhub-contracts 0.29.0` (`f3565fc`). Release commit `2109501`, tag and
  GitHub Release were published; Actions `31731204933` passed. Production was
  backed up as `3b21195e`, checked, installed and restarted. A live user-view
  audit then found nine remaining English numeric labels, so the closure was
  released as HACS `1.52.89`, commit `eba7c8a`, Actions `31732638754` success.
  Production installed/latest and panel marker are `1.52.89`; Dashboard API
  returns 13 rooms, 79 currently projected devices and 37 range controls with
  valid bounds, step, opaque target and `set_value`. All 19 live range label
  types are Russian and the system log contains no HausmanHub entries. Full
  release gate: 1381 tests passed, 4 skipped. No physical device command was
  sent during deployment or verification.

- 2026-08-13 (Codex): HACS `1.52.87` синхронизирует подписи в открытой
  карточке устройства с Android 1.0.122. Имя физического устройства остаётся
  в заголовке и не повторяется перед каждой командой; дочерняя возможность
  сохраняет собственное имя, полная подпись остаётся в aria-label. API,
  storage и command payload не менялись. Full pytest: 1374 теста, 4 skipped,
  922 subtests; local release gate и Actions `31717482272` зелёные. Release
  commit `ae89b23`, tag и GitHub Release опубликованы. Production обновлён
  после automatic backup и config check: installed/latest и panel marker равны
  `1.52.87`, 10 сущностей доступны, runtime fresh, active operations и blocked
  reasons 0.

- 2026-08-13 (Codex): HACS `1.52.86`, release commit `64aedec`, tag и GitHub
  Release опубликованы, production обновлён. Главная повторяет композицию
  Android-планшета, устройства собраны в одной рабочей области, физические и
  медиа-карточки получили общий читаемый шаблон. Исправлена прокрутка панели к
  пустому экрану при открытии устройства. Основная энергия оставляет значения,
  график и источники, а счётчики, полный список и настройки открывает в окне
  деталей. Климатическая шапка объединяет сводку и группы оборудования. Full
  local release gate: 1371 тест, 4 skipped; Actions `31709902798` success.
  После automatic backup, config check, install и restart installed/latest и
  panel marker равны `1.52.86`; 10 сущностей доступны, runtime fresh, pending
  и blocked reasons 0, system log HausmanHub чист. Физические команды и
  климатические цели при deploy не отправлялись.

- 2026-08-13 (Kimi implementation, Codex review): HACS `1.52.85`, commit
  `e44e9c6`, tag и GitHub Release `v1.52.85` опубликованы. Карточка общей климатической
  цели стала полноценным контролом: значение по центру, шаги `−0,5` и `+0,5`
  по 48 px, capability gate, disabled guard, optimistic revision и receipt
  сохранены. Главная стала компактнее, получила асимметричный верхний ряд,
  единый ритм нижних карточек и отдельную высоту empty-state. Codex self-review
  добавил `align-self:start` для пустых grid-карточек. Full local release gate:
  1370 тестов, 4 skipped, package, Android compatibility и file safety
  зелёные; Actions `31682272937` завершился успешно. Production HA 2026.8.1
  обновлён после automatic backup, config check и restart. Live installed и
  latest равны `v1.52.85`, panel marker `1.52.85`, 10 сущностей HausmanHub
  доступны, runtime fresh, pending операций нет. Dashboard вернул 13 комнат,
  82 устройства и comfort 88 со статусом «Хорошо». Системный журнал не содержит
  записей HausmanHub. Физические команды, климатические цели и автомат 233 не
  затрагивались.

- 2026-08-13 (Codex): HACS `1.52.84`, commit `f5e072d`, опубликован и
  установлен в production с consumer pin contracts `0.28.0` (`f007cfd`).
  Dashboard API v1 теперь публикует server-side `comfort` 0-100, статус и
  качество данных. Full pytest: 1370 passed, 4 skipped, 921 subtests; release
  gate: 1368 tests, 4 skipped. После automatic backup, config check и restart
  installed/latest `1.52.84`, unavailable 0, pending 0. Android 1.0.117 live
  показывает `89 из 100`, `Хорошо`. Физические команды и автомат 233 не
  затрагивались. UI-доработки переданы Kimi в workspace handoff.

- 2026-08-12 (Codex): подготовлена HACS `1.52.83` с единым паттерном
  физических карточек. В карточке крупно видны имя и подтверждённое состояние,
  отдельно показаны комната и тип, статус связи не прячется. Переход к деталям
  не выводит отдельное «Открыть», а команды получают контекст: «Выключить
  Люстра гостиной», «Закрыть Шторы кухни». Кнопки, поля ввода и закрытие
  листа увеличены до 48 px. Figma HMH II дополнен `HausmanHub / Device Card
  v2` и каноном `CANON / Device Card v2 / 2026-08-12` с вариантами Online,
  Attention, Offline и Loading. Проверены panel/settings/release cache tests,
  JSON manifest, diff check и отдельный JS smoke test. Production deploy и
  физические команды не выполнялись.

- 2026-08-12 (Codex): в выпуск `1.52.83` добавлены единые информационные
  шапки всех библиотечных разделов, кроме главной и киоска. Фоновая картинка
  исключена, крупный заголовок, состояние и факты приведены к стилю «Энергии».
  В комнате ручной индикатор перенесён на отдельную строку, температура
  закреплена в отдельной колонке. Следующий шаг: тесты и релиз совместимого
  Android `1.0.117`.

- 2026-08-12 (Codex): HACS `1.52.81` выпущен из commit `2536c73` и
  опубликован как GitHub Release `v1.52.81`. Редактор сценариев получил тип
  «Внешнее событие» для уже существующего backend-триггера `event`: точный
  custom event type и необязательный JSON-фильтр до 12 scalar-полей. UI
  повторяет fail-closed правила backend, поэтому системные HA events,
  вложенные, невалидные и чрезмерные фильтры не сохраняются. Full release
  gate: 1367 тестов, 4 пропущены, package, Android compatibility и safety
  зелёные. Production HA 2026.8.1 обновлён после автоматического backup:
  config check вызван, restart завершён, installed/latest `v1.52.81`, cache
  marker сценариев `1.52.81`, unavailable сущностей нет. Автоматическое
  закрытие воды не создано и не включено: физическое направление реле и
  получатель уведомлений ещё не подтверждены. Сценарии, климатические команды
  и автомат 233 при релизе не запускались.

- 2026-08-12 (Codex): HACS `1.52.80` выпущен из commit `1ffb24e` и
  опубликован как GitHub Release `v1.52.80`. Главная панель собрана в том же
  функциональном порядке, что и Android: климат, цель климата, освещение,
  безопасность, избранные сценарии, энергия, комфорт, внимание, погода,
  состояние дома и активность. В карточке цели добавлены capability-gated
  шаги `±0,5 °C`, переход к деталям и синхронизация, повторные действия
  блокируются pending receipt. Полный local release gate: 1366 тестов,
  4 пропущены, совместимость Android, пакет и безопасность файлов зелёные.
  Production HA 2026.8.1 установлен штатно после автоматического backup:
  config check вызван, restart завершён, installed/latest `v1.52.80`, cache
  marker панели `1.52.80`, unavailable сущностей HausmanHub нет. Физические
  климатические команды и автомат 233 не затрагивались.

- 2026-08-12 (Codex): HACS `1.52.79` опубликован и установлен в production,
  release merge `30d8a32`, GitHub Release `v1.52.79`. Climate overview получил
  capability-gated действие `synchronize_home`; pending блокирует повторный
  POST, confirmed/failed отражаются в notice. Карточка использует semantic
  theme tokens, responsive layout и focus-visible. Полный gate: 1366 тестов,
  4 пропущены; CI, package и repository safety зелёные. Перед deploy создан
  автоматический backup, config check и restart прошли; installed/latest
  `v1.52.79`, runtime fresh, action опубликован, pending operation отсутствует,
  unavailable сущностей HausmanHub нет. Новая физическая sync-команда при
  проверке UI не отправлялась, чтобы не создавать лишние вызовы устройств;
  автомат 233 не затрагивался.

- 2026-08-12 (Codex): выпущена и установлена HACS `1.52.78`, merge
  `f2e56b4`, tag и GitHub Release `v1.52.78`, Actions `31621133966` зелёный;
  consumer contracts `0.27.0`. Managed tick применяет только расходящиеся
  устройства и поля. Restart-safe desired fingerprint запрещает повтор
  одинакового полного call batch до полного выравнивания либо изменения
  цели, даже при нестабильном feedback. Добавлены `synchronize_home` и exact
  local schedule 10:00/22:00 с durable slot latch; ручные комнаты и устройства
  исключаются. Полный gate: 1365 тестов, 4 пропущены. Production HA 2026.8.1
  обновлён после backup `d5feba04`, config check/restart штатные,
  installed/latest `v1.52.78`, 10 служебных сущностей доступны. Live sync
  получил `confirmed`, затем 130 секунд дали 0 climate/humidifier/remote/button
  calls. Node-RED climate execution и morning resync выключены, pending 0,
  authority-гейты legacy оставлены закрытыми. Автомат 233 не затрагивался.

- 2026-08-12 (Codex): выпущена HACS `1.52.76`, commit `bac4105`, tag и
  GitHub Release `v1.52.76`, Actions `31610789045` зелёный. Contracts
  consumer поднят до `0.26.0`; сценарии поддерживают bounded custom Home
  Assistant events с точным scalar matching, а state triggers сохраняют
  защиту от повторного входа. Финальный self-review исправил совместимость с
  read-only `Mapping` и использует официальный `MATCH_ALL` для event bus.
  Frontend получил единые hero/status-pill и карточки устройств с честной
  индикацией связи. Полный local gate: 1354 теста, 4 пропущены; package,
  contract boundary и file safety зелёные. Production deploy не выполнялся.
  По постоянному решению владельца финальный review выполняет Codex по
  окончательному staged diff, профильным тестам и full local release gate;
  ожидание review Kimi удалено из release policy и frozen test.

- 2026-08-11 (Codex): подготовлена HACS `1.52.72`: сохранённые сценарии с
  триггером `device_state` подписываются на события Home Assistant. Равенство
  запускается только при входе в значение, а числовой порог - только при
  пересечении. Покрыто `tests/test_scenario_events.py` и быстрым набором
  сценариев: 55 passed. Полный release gate в отдельном worktree не является
  валидной проверкой: тесты используют канонический checkout с manifest
  `1.52.71`, из-за чего получили 8 version/frontend failures и 77 прежних
  runtime errors. Релиз и deploy не выполнялись. Для физического сценария
  протечки требуется подтверждение семантики обоих реле редукторов воды,
  поскольку их state API не доказывает, что `off` означает «вода закрыта».

- 2026-08-11 (Codex): выпущена и установлена HACS `1.52.71`, commit
  `e20fba5`, tag и GitHub Release `v1.52.71`, Actions `31522965371`
  зелёный; consumer contract `0.25.0` (`5718c69`).
  Климатическую комнату или любое назначенное устройство можно durable
  исключить из автоматики и вернуть действием `set_room_mode` или
  `set_device_mode` без физической команды при переключении. Исключённые
  устройства удаляются из исполнительного contour plan. Исключение датчика
  температуры или влажности переводит всю комнату в ручной режим до возврата
  датчика. Неатрибутированное ручное выключение direct-Wi-Fi кондиционера
  исключает только этот кондиционер и переживает restart. Runtime v1 и Home
  v12 совместимо публикуют device `mode` и `control`. Local admin HACS получил
  доступ к общей typed climate runtime/action поверхности, вкладки режимов,
  явную индикацию, переключатели комнаты и устройств, предупреждение для
  критического датчика. Полный local release gate зелёный: 1341 тест, 4
  пропущены; дополнительная выборка 274 теста и 294 subtests зелёная.
  Production HA Core 2026.8.1: перед установкой создан полный локальный
  backup `781a261e` с базой, но загрузка копии во внешний backup-agent
  завершилась `upload_failed`; локальная защищённая копия сохранена.
  Явная установка, config check и restart прошли, installed/latest,
  runtime и cache marker подтверждают `1.52.71`, все 9 служебных сущностей
  доступны. Все 5 комнат объявляют `set_room_mode`, назначенные устройства
  объявляют `set_device_mode`; ручных исключений в live-состоянии пока нет.
  Физические климатические команды при deploy не отправлялись. Endpoint
  `/api/error_log` вернул 404, поэтому отдельная проверка журнала ошибок не
  зафиксирована как пройденная.

- 2026-08-11 (Codex): выпущена и установлена HACS `1.52.70`, release commit
  `6334594`, tag и GitHub Release `v1.52.70`, Actions `31514091755` зелёный.
  Явный `set_room_target` теперь доступен в automatic contour при выключенном
  расписании: target хранится как durable override до явной очистки, а
  фоновое расписание не включается. Temperature preflight и строгий plan
  проверяют только управляемый кондиционер, поэтому недоступный увлажнитель
  комнаты Игоря больше не скрывает и не блокирует команду. Fail-closed
  сохранён для самого кондиционера, stale state, room authority и pending
  operation. Полный gate: 1336 тестов, 4 пропущены. Production обновлён после
  полного backup `b132eafd` с базой; config check и restart штатные,
  installed/latest и cache marker равны 1.52.70, 10 служебных сущностей
  доступны, system log HausmanHub чист. Live Home и climate runtime объявляют
  `set_room_target` для комнаты Игоря, спальни и кухни; кабинет и комната
  Алисы закрыты по `device_unavailable`. Физическая климатическая команда при
  deploy не отправлялась.

- 2026-08-11 (Codex): выпущена и установлена HACS `1.52.68`, commit
  `14a2204`, tag и GitHub Release `v1.52.68`. Native room control объявляет
  `set_room_target` только когда durable temporary-temperature executor
  действительно готов: runtime свежий, registry согласован, нет pending
  operation, текущий профиль расписания применён и room authority eligible.
  `set_room_mode` остаётся независимо доступен. Повторный независимый review
  одобрил финальный diff; полный gate: 1333 теста, 4 пропущены; GitHub Actions
  `31508446141` зелёный. Production HA Core 2026.8.1: полный backup
  `a4f9c305` с базой, явный `update.install`, config check и restart прошли.
  Installed/latest и cache marker показывают `1.52.68`, 10 служебных сущностей
  доступны, system log HausmanHub чист. Live contour сейчас имеет
  `schedule.enabled=false`, поэтому `set_room_target` закономерно не
  рекламируется и физическая климатическая команда при deploy не отправлялась.

- 2026-08-11 (Kimi): выпущены frontend-релизы `1.52.66` (commit `c37cce2`,
  tag и GitHub Release `v1.52.66`, GitHub Actions `31467651111` зелёный) и
  `1.52.67` (commit `e71e89b`, tag и Release `v1.52.67`, Actions зелёный).
  Работа по handoff `KIMI_HACS_NESTED_WINDOWS_THEME_ENERGY_HANDOFF_2026-08-11.md`
  и HACS-части `KIMI_ENERGY_METER_DEVICE_DISCOVERY_HANDOFF_2026-08-11.md`
  (worktree `worktrees/kimi-hacs-theme-energy`, ветка
  `kimi/nested-windows-energy-2026-08-11`, влита в main fast-forward).
  1.52.66: общие токены `--hmh-modal-*` для всех вложенных окон обеих тем,
  модуль `hausman-hub-modal.js` (Escape только верхнее окно, focus trap,
  возврат фокуса), тематизированный canvas графика энергии с перерисовкой
  без пересоздания DOM, компактная главная энергии (hero + напоминание +
  сводка без внутренних прокруток), модальное окно деталей с
  `hausman-hub-energy-meter.js` (расписание, передача, корректировка,
  expectedRevision, 409 -> повторный GET, reset_detected без нуля).
  1.52.67: модуль `hausman-hub-device-discovery.js` - GET при старте и
  каждом фоновом обновлении, badge на табе «Устройства», карточки новых
  устройств с причинами рекомендаций, действия acknowledge/assign_area/
  add_to_energy/show_on_dashboard с per-action pending и явными текстами
  403/404/409 без общего зелёного fallback. Полный gate: 1328 тестов,
  4 пропущены. Визуальные проверки harness в headless Chrome (обе темы,
  1440/1024): `artifacts/hacs-nested-windows-2026-08-11/`, POST-команд
  устройств не отправлялось. Deploy в production не выполнялся: production
  остаётся на 1.52.65, установка 1.52.67 за релизным процессом Codex.
  Android-часть handoff выпущена отдельно как Android 1.0.111 (см.
  AI_CONTEXT Android-репозитория); совместимая комбинация обновлена в
  `docs/COMPATIBILITY.md` workspace.


- 2026-08-11 (Kimi): frontend панели по handoff
  `docs/migration/KIMI_HACS_NESTED_WINDOWS_THEME_ENERGY_HANDOFF_2026-08-11.md`,
  commit `51d96f9` в ветке `kimi/nested-windows-energy-2026-08-11` (worktree
  `worktrees/kimi-hacs-theme-energy`), без bump версии и без deploy. Добавлены
  общие токены `--hmh-modal-*` (backdrop, surface, raised, border, shadow) в
  `hausman-hub-tokens.css` для обеих тем; все 9 вложенных окон (device,
  climate, lighting, rooms, media, scenarios, energy) переведены на токены,
  жёсткие тёмные цвета вне tokens.css удалены. Новый модуль
  `hausman-hub-modal.js`: Escape закрывает только верхнее окно, клик по
  backdrop, focus trap и возврат фокуса на открывшую карточку. График энергии
  читает палитру из theme-токенов и перерисовывается при смене темы без
  пересоздания DOM. Главная страница энергии компактная: hero, напоминание о
  передаче показаний и сводная карточка без внутренних прокруток; графики,
  источники, счётчик, история и управление питанием перенесены в модальное
  окно деталей (role=dialog, aria-modal) с видами overview/device. Новый
  модуль `hausman-hub-energy-meter.js`: карточка счётчика с расписанием,
  передачей и корректировкой показаний, expectedRevision и обработкой 409
  через повторный GET. Harness получил stubs meter API; node-harness тестов
  грузят новые модули; добавлен `tests/test_hausmanhub_modal_theme.py`.
  Полный gate: 1319 тестов, 4 пропущены. Визуальная проверка на harness в
  headless Chrome (обе темы, 1440 и 1024): скриншоты в
  `artifacts/hacs-nested-windows-2026-08-11/`, POST-команд устройств не
  отправлялось. Следующий шаг: релизный процесс Codex поднимает версию
  (ожидается 1.52.66) и деплоит; Kimi живую панель не обновлял.


- 2026-08-11 (Codex): выпущена и задеплоена `1.52.65`, commit `b78d4c3`,
  tag и GitHub Release опубликованы. Добавлен durable API
  `GET/POST /api/hausman_hub/v1/energy/meter`: ежемесячная дата и напоминание,
  ручная передача показаний, корректировка текущего anchor, история до 60
  операций. Передача начинает новый расчётный цикл без сброса физических
  накопительных HA-сенсоров; корректировка сохраняет начало цикла. Падение
  накопительного источника или смена состава выбранных energy sources даёт
  `reset_detected` и блокирует выдуманный расход до нового anchor. Добавлен
  durable `GET/POST /api/hausman_hub/v1/device-discovery`: первый запуск
  создаёт baseline без старых уведомлений, новые физические HA-устройства
  получают bounded уведомления, варианты комнат и рекомендации использования.
  Raw HA device/entity ID не выходят в публичный документ; assign area требует
  local admin, добавление в энергию или на главную меняет только HA-owned
  preferences. Consumer pin: contracts `0.24.1` (`65eff46`). Полный gate:
  1305 тестов, 4 пропущены; GitHub Actions `31459385921` зелёный. Production HA
  Core 2026.8.1: полный automatic backup `e178bdf7` с базой, явная установка
  `v1.52.65`, config check и два restart прошли штатно. Installed/latest и
  admin panel подтверждают 1.52.65; 10 сущностей интеграции доступны, error log
  HausmanHub чист. Live energy revision 0 оставлен disabled без выдуманных даты
  и показания, источник available с `278.05 kWh`; discovery после второго
  restart сохранил revision 1, initialized true и pendingCount 0.

- 2026-08-10 (Codex): выпущена и задеплоена `1.52.64`, commit `56fca2d`,
  tag и GitHub Release опубликованы. Добавлен durable-контур `requires_on`:
  зависимое устройство получает эффективное состояние `off`, если питающий
  выключатель выключен, и `unknown` или `unavailable`, если источник ещё не
  доступен. Прямые и сценарные команды блокируются до вызова Home Assistant,
  циклические и дублирующиеся связи отклоняются. Admin API:
  `GET/PUT /api/hausman_hub/v1/admin/device-power-dependencies`, optimistic
  revision, storage переживает restart. Dashboard сохраняет сырое состояние
  в `reportedState`, публикует `powerDependency`, убирает недоступные actions
  и считает `activeLights` по эффективным состояниям. Consumer pin обновлён
  на contracts `0.23.0` (`da52e3c`). Полный gate: 1293 теста, 4 пропущены;
  GitHub Actions `31423113622` зелёный. Production HA Core 2026.8.1: полный
  backup `32c65894` на KeeneticSSD, 814,57 МБ, база включена; явная установка
  `v1.52.64`, config check и два restart прошли штатно. Для
  `light.0xa4c138d69d102803` сохранена зависимость от
  `switch.0x603d61fffe75c334_1`. После restart связь осталась revision 1:
  raw `off/on`, dashboard люстры `off`, `reportedState=on`,
  `power_source_off`, actions пусты. Все 9 сущностей интеграции доступны,
  system log HausmanHub чист; физических команд при настройке и проверке не
  отправлялось.
- 2026-08-10 (Codex): выпущена и задеплоена `1.52.63`, commit `2bc0835`.
  Patch-релиз исправляет только канонический consumer pin на
  hausmanhub-contracts `0.22.3` (`4ac4533`); runtime schema и поведение ручного
  режима уже вошли в 1.52.62. Полный gate повторён: 1283 теста, 4 пропущены;
  HACS source parity зелёный, GitHub Actions `31416519754` завершён успешно.
  Перед установкой создан новый полный automatic backup, завершённый
  `2026-08-11T00:02:23.740857+06:00`. Явная установка `v1.52.63`, config check
  без ошибок и restart прошли штатно. Installed/latest, backend API и cache
  references подтверждают 1.52.63; все 10 сущностей доступны, HausmanHub
  system log чист. Кабинет остался automatic с доступным `set_room_mode`,
  Алиса automatic с blocker `device_unavailable`; operation journal содержит
  6 прежних записей и ни одной climate-записи.
- 2026-08-10 (Codex): выпущена и задеплоена `1.52.62`, release commit
  `b8cff61`, tag и GitHub Release опубликованы. Для кондиционеров с
  `control_channel=direct_wifi` долговечная память фиксирует наблюдаемую фазу
  и успешное намерение HausmanHub на 5 минут. Переход active -> off без такого
  намерения переводит комнату в `manual`, поэтому managed-цикл не включает
  прибор обратно. Недоступность устройства, первый baseline после обновления
  и штатный OFF от HausmanHub не создают ручной режим. Существующее действие
  `set_room_mode` подключено к native runtime и сохраняет automatic/manual без
  физической команды. Home v12 синхронизирован с hausmanhub-contracts 0.22.3
  (`4ac4533`); старый Android fixture не изменён. Полный release gate: 1283
  теста, 4 пропущены; GitHub Actions `31414875689` зелёный. Production HA Core
  2026.8.1: полный backup `e125917d`, явная установка `v1.52.62`, config check
  и restart штатные. Installed/latest и 31 cache reference подтверждают
  `1.52.62`, все 10 сущностей доступны, system log HausmanHub чист. Кабинет
  после restart остался `cool`, комната automatic и рекламирует только
  `set_room_mode`; кондиционер Алисы остался unavailable, manual не включён.
  Новых climate-записей в operation journal и физических команд при проверке
  не было. Следующая безопасная живая проверка: владелец выключает Wi-Fi AC
  кабинета или Алисы физически, затем runtime должен показать `manual`; Codex
  сам активный кондиционер для теста не выключал.
- 2026-08-10 (Codex): выпущена и задеплоена `1.52.61`, release commit
  `82ed32c`, tag и GitHub Release опубликованы. Причина пустой «Последней
  активности» была в backend: dashboard v1 всегда возвращал `events: []` и
  capability `events: false`, хотя Android уже корректно читает это поле.
  Dashboard теперь проецирует до 100 newest-first записей из durable operation
  journal, не раскрывая correlation ID, HA entity ID и target устройства.
  Полный release gate: 1275 тестов, 4 пропущены; GitHub Actions
  `31410771320` зелёный. Production HA Core 2026.8.1: полный backup
  `d782fb26`, явная установка `v1.52.61`, config check и restart штатные.
  Installed/latest и 31 cache reference подтверждают `1.52.61`, все 10
  сущностей доступны, system log чист. Живой dashboard отдаёт две записи
  `Сценарий` из сохранённого journal и `capabilities.events=true`. Новых
  физических команд для проверки не отправлялось; Android менять не пришлось.
- 2026-08-10 (Codex): выпущена и задеплоена `1.52.60`, commit `bc193ca`,
  tag и GitHub Release опубликованы. Исправлена совместимость scenario API с
  Home Assistant Core 2026.8: `HomeAssistantView.json()` получает HTTP-статус
  через `status_code`, поэтому выполненная команда больше не заканчивается
  ложным `HTTP 500`. Предыдущий release `1.52.59` (`282004e`) перенёс read-back
  нескольких устройств в одно параллельное восьмисекундное окно, сохранив
  порядок отправки команд и честный признак `confirmed`. Полный release gate
  обеих версий: 1273 теста, 4 пропущены; GitHub Actions `31407759337` и
  `31409172442` зелёные. Production HA Core 2026.8.1: полный backup
  `e8dddcff`, явная установка `v1.52.60`, config check и restart штатные.
  Installed/latest, API и статика подтверждают `1.52.60`, 10 сущностей
  доступны, system log чист. Живой запуск «Закрыть шторы» вернул HTTP 200 за
  0,285 с, обе квитанции `confirmed` со state `closed`; durable journal хранит
  подтверждённый `scenario_run` с correlation ID.
- 2026-08-10 (Codex): выпущена и задеплоена `1.52.58`, release commit
  `981bd26`, tag и GitHub Release опубликованы. Добавлен ограниченный 512
  записями durable journal квитанций устройств, климата, сценариев и голоса с
  единым `correlation_id`; storage переживает restart, а local-admin endpoint
  `GET /api/hausman_hub/v1/admin/operations` поддерживает фильтры `limit`,
  `source`, `correlation_id` и не сохраняет приватные target/entity IDs.
  Интеграция закреплена на contracts `0.22.0` (`9d7a84b`). Полный release
  gate: 1271 тест, 4 пропущены; GitHub Actions run `31404286660` зелёный.
  Production HA Core 2026.8.1: перед установкой создан полный автоматический
  backup `21369a49`, `update.install` с явной версией `v1.52.58`, config check
  и restart прошли штатно. Installed/latest и статика подтверждают `1.52.58`,
  10 сущностей доступны, новый journal отвечает contract v1, после первого
  запуска sequence 0 и records пусты. Команды устройствам при проверке не
  отправлялись.
- 2026-08-10 (Codex): выпущена и задеплоена `1.52.57`, release commit
  `63b8557`, tag и GitHub Release опубликованы. В состав вошли: трактовка
  `hvac_action: idle` кондиционера как поддержания без команды off; отдельное
  зимнее отключение увлажнителя при открытом окне без общей паузы климата;
  каталог сценариев с валидной сохранённой `mdi:*` иконкой по contracts
  `0.21.0` (`340097f`); restart-safe ledger и паритет публичных climate
  blockers с нативным runtime; панель «Ближайшие события» из Kimi-коммита.
  Полный release-check: 1266 тестов, 4 пропущены, ошибок нет. GitHub Actions
  run `31391527202` зелёный. Production: backup `7de326be`, явная установка
  `v1.52.57`, config check и restart штатные; installed/latest и статика
  панели показывают `1.52.57`, 10 сущностей доступны, system log по
  HausmanHub чист, живой каталог возвращает `mdi:curtains`. К комнатам с
  увлажнителями привязаны физические датчики окна комнаты Игоря и балконной
  двери спальни; при открытой двери летняя проверка сохранила AC `maintain`
  и не включила общую паузу. Принудительные команды устройствам не выполнялись.
  Публичные ручные climate actions честно заблокированы
  `action_unsupported`, как и нативная admin projection; автоматический
  managed runtime остаётся активным. Следующий клиентский шаг: Kimi читает
  поле `icon` каталога на планшете. SSH с Linux по-прежнему отклонён
  `publickey`, эксплуатационная проверка выполнена через REST и WebSocket.
- 2026-08-09 (Kimi): выпущена и задеплоена `1.52.56` — движок расписаний
  сценариев (триггеры time/sunrise/sunset, `scenario_schedule.py`, contract
  pin 0.20.1), публичные API ближайших событий
  (`/api/hausman_hub/v1/scenarios/upcoming`, `/upcoming/cancel` для отмены
  одного запуска), домофон из браузерной панели (локальный admin, импульс
  15 сек с автовыключением), тема панели день/ночь по времени суток
  (светлая 6:00-22:00, выбор в настройках интерфейса). Деплой: installed/latest
  `v1.52.56` подтверждены API, статика панели `1.52.56`, 10 сущностей
  `hausman_hub` доступны, журнал без ошибок, `/scenarios/upcoming` отвечает
  живым событием (sunset-запуск «Закрыть шторы»).
- 2026-08-09 (Kimi): выпущена и задеплоена `1.52.55` — у комнатных датчиков
  температуры/влажности своё окно свежести 3 часа
  (`MAX_ROOM_SENSOR_STATE_AGE_MS`); пассивные zigbee-датчики с провалами
  репортов больше не роняют комнату в `stale` и не блокируют apply/reobserve.
  Backend-долг freshness закрыт. Внутреннее расписание термоголовки гостиной
  отключено в z2m (все дни плоские 16 °C) по решению владельца.
- 2026-08-08 (Kimi, по явному поручению владельца вместо Codex): выпущены и
  задеплоены `1.52.48` (room-scoped contour apply через optional `room_ids`,
  contracts 0.19.0), `1.52.49` (stop-действие термоголовки переводится в
  `climate.set_temperature` 10 °C; свежесть наблюдений по `last_reported`),
  `1.52.50` (`quiet=False` не лимит), `1.52.51`-`1.52.53` (диагностические
  warning-логи apply) и `1.52.54` (фикс: `hvac_action` как StrEnum
  `HVACAction` больше не выбрасывается фильтром атрибутов state view;
  без него TRV в режиме `heat` всегда наблюдалась `heating`, квитанция не
  подтверждалась, а managed-цикл дожимал план комнаты каждую минуту).
  Гостиная: заявка `admin-gostinaia-managed-2026-08-08-07` завершилась
  `confirmed`, комната aligned, лишние IR-команды прекращены. Подробности:
  workspace `docs/migration/CURRENT_STATE.md` (секция 2026-08-08) и
  `LLM_WIKI/Sessions/2026-08-08-kimi-living-room-managed-b.md`.
- Read-only production climate audit tool is prepared on branch
  `codex/climate-production-audit`, stacked on `codex/climate-api-backend`:
  - `tools/audit_production_climate.py` performs HTTP GET requests only
    against a live HACS: `/api/config`, `/capabilities`,
    `admin/climate-mode` (rollout and cutover), `admin/climate-readiness`,
    `admin/climate-registry`, `admin/climate-device-bindings`,
    `admin/climate-shadow-comparison` and `admin/climate-shadow-window`.
  - Admin access is read at runtime from a JSON file outside the workspace
    (default `/home/ivsh/.config/hausmanhub/ha_admin_access.json`, keys
    `base_url` and `token`). The token is never printed or persisted.
  - Raw responses contain private entity IDs and are saved outside the
    repository (`<access dir>/audit/<UTC timestamp>/`, mode 0700/0600);
    stdout shows only a sanitized summary without entity IDs.
  - Exit codes: 2 missing/invalid access file, 3 authorization failure,
    4 unreachable endpoint or non-JSON response.
  - `tests/test_audit_production_climate.py`: 11 tests; full suite 1202
    passed, 4 skipped; `tools/check_local_release.py` passed.
  - Blocked only by the external access file: the HA long-lived admin token
    must be created by the owner in the Home Assistant UI.
- Tablet climate API backend is prepared on branch
  `codex/climate-api-backend`, based on integration `1.52.40`:
  - Contract pin: `hausmanhub-contracts` `0.18.0`, commit `b2e4c8b`.
  - New local tablet routes: `GET /api/hausman_hub/v1/climate/runtime`,
    `POST /api/hausman_hub/v1/climate/actions`, and
    `GET /api/hausman_hub/v1/climate/operations/{operation_id}`.
  - The public request boundary accepts only stable room IDs and typed climate
    parameters. Home Assistant entity IDs and arbitrary service targets are
    rejected.
  - Operation identity is persisted before native execution. Duplicate retries
    return the same receipt and never repeat a physical command after restart.
  - Runtime snapshots expose disabled, shadow/readiness, canary, managed and
    stale states without giving the tablet control before existing server gates
    allow it.
  - A follow-up closes the shadow projection gap: general `shadow` mode can
    read native observations while the climate writer stays disabled. The
    tablet sees `legacy_climate_core`, no actions and `climate_shadow_only`.
  - The runtime now publishes room ranges, active profile, temporary override,
    device control scope and the latest confirmed operation. A persisted
    pending operation can become confirmed through a later read-only status
    check; no command method is called during this re-observation.
  - Final local release gate passed: 1194 tests, 4 skipped, all fixture,
    Android compatibility, HACS package, naming and repository safety checks
    passed. The checker builds contract `0.18.0` action envelopes.
  - No files under `frontend/` were changed. Manifest version and frontend cache
    references stay at `1.52.40` until the Kimi client handoff is accepted and a
    coordinated release is assembled.
  - Next: commit and push the backend branch, then verify production bindings
    and collect shadow evidence without sending device commands.

- Release 1.27.0 (wizard IR-code learning for universal IR contours)
  is RELEASED and DEPLOYED, 2026-07-28:
  - Release commit `f22df48` on `origin/main` (30 files, +4021/-57); tag
    `v1.27.0` pushed; GitHub Actions run `30352650893` success; public
    release:
    https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.27.0.
  - Contents: wizard `code_source` step AFTER contour save plus resumable
    `Настроить IR-коды` entry on the saved contour card; strict source
    priority SmartIR DB -> Broadlink `.storage` -> manual learn with
    explicit `replace: true` (409); codes in HausmanHub versioned Store
    keyed by contour device_id; canonical keys `ac.off`,
    `ac.cool.<temp>`/`ac.heat.<temp>`, `humidifier.on|off`; runtime typed
    `remote.send_command {entity_id, device, command}` through the strict
    executor; missing code -> `ir_command_not_learned`; endpoint
    `GET /api/hausman_hub/v1/admin/ir-codes/bindings`; 422 on unknown
    device/remote mismatch; v1 current-setup contract unchanged;
    IRCodeService behind protocols (`ir_code_ports.py`), HA adapters outer
    (`ir_code_gateway.py`).
  - Oracle (gpt-5.6-sol) REJECTED the first implementation; one fix
    program in 3 delegated passes (session ses_057e6b30affe0BV6WgVQntepEC)
    closed all 4 blockers + majors. No second review round per policy.
  - Gates: 887 passed, 4 skipped, 732 subtests;
    `tools/check_local_release.py` passed (version bump touched manifest
    plus 3 version references in tests); panel budget 230 KiB kept.
  - Built in worktree `hausmanhub_hasc-1270` (branch
    `ui-1.27.0-ir-learning`). Deploy: HACS
    `update.hausman_hub_hasc_update` installed explicit `v1.27.0`, HA
    restarted, verified live (read-only): `integration_version: 1.27.0`.
  - The main checkout `/home/ivsh/projects/hausmanhub_hasc` still holds
    the superseded first 1.27.0 WIP + untracked DESIGN.md rev 4 and is
    behind origin/main; do not build on it - reset or rebase before any
    further work there.
  - Next candidates: roadmap 39 (per-room schedules/profiles), roadmap 40
    (standalone activation), then 2.0 line (41-50).
- Release 1.26.2 (prominent roomless-device warning in the wizard)
  is RELEASED and DEPLOYED, 2026-07-28:
  - Release commit `083b4c7` on `origin/main`; tag `v1.26.2` resolves to
    `083b4c733d16fee806104a7760320b68cab798fc`; GitHub Actions run
    `30341142526` success; public release:
    https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.26.2.
  - Contents: `.wizard-warning` amber banner (reuses the
    `--warning-color` tonal styling of `.candidate-room-warning`). Rooms
    step shows the roomless count; room step lists the first 5 roomless
    device names with "и ещё N" and offers both fixes (HausmanHub-only
    binding in the `Устройства без комнаты` section, or HA area assignment
    + refresh). Banners render only when roomless candidates exist.
  - Built in worktree `hausmanhub_hasc-1262` (branch
    `ui-1.26.2-roomless-warning`); tests in
    `tests/test_hausmanhub_panel_wizard.py` cover names, 5-name
    truncation, rooms-step counter, and the absent-when-empty state.
  - Gates: full suite 829 passed, 4 skipped, 732 subtests;
    `tools/check_local_release.py` passed (version bump touched manifest
    plus 3 version references in tests).
  - Deploy: HACS `update.hausman_hub_hasc_update` installed explicit
    `v1.26.2`, HA restarted, verified live (read-only):
    `integration_version: 1.26.2` at `/api/hausman_hub/v1/admin/panel`,
    panel JS 212368 bytes with the new warning strings.
  - Figma (HMH--HA) batch 2 is done the same day: the banner pattern and
    all DESIGN.md section-4 screens now exist in dark+light (details in
    the `УД-hasc` project AI_CONTEXT.md).
  - Next: 1.27.0 wizard IR-learning vertical ("2 lite") in the main
    checkout `/home/ivsh/projects/hausmanhub_hasc` (uncommitted WIP with
    failing tests): restore a fixed `code_source` step, SmartIR code DB
    scan, Broadlink `.storage` codes, `remote.learn_command` last.
- Release 1.26.1 (light theme + theme switcher, wizard select CSS fix)
  is RELEASED, 2026-07-27:
  - Release commit `0d215c4` on `origin/main`; tag `v1.26.1`; GitHub Actions
    run `30288293165` passed; public release:
    https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.26.1.
  - Contents: light theme via `:host(.theme-light)` tokens and a session-only
    theme switcher (auto/light/dark; auto follows `hass.themes.darkMode`,
    no localStorage per the panel contract test); wizard fix: the
    `Канал управления` select no longer collapses, `.device-card-options
    label.form-field` switches to a single-column grid with a full-width
    select.
  - DESIGN.md revision 3 (main checkout only, untracked) documents the
    modes and the light palette; light-theme `--warning-color` is `#9A5F0B`
    (measured contrast 4.62-5.23:1) after Oracle flagged `#B06F14` at
    4.09:1 (< WCAG 4.5:1). One fix iteration, then accepted.
  - Gates: full suite 827 passed, 4 skipped, 732 subtests;
    `tools/check_local_release.py` passed after the version bump
    (manifest plus 3 version references in tests).
  - Broadlink AC finding (user report "SmartIR AC missing from wizard"):
    not a code bug. Live `GET climate-drafts` payload contains
    `candidate_0001 "Komanchi Living SmartIR"` (can_add true, available)
    with `room_id ""` and `suggested_room_id null` (reason
    `unassigned_room`) because the SmartIR climate entity has NO area in
    HA (template check: area_id/area_name/device_id all None). First-run
    wizard shows it in the collapsed `Устройства без комнаты` section;
    the contour editor hides roomless devices with a warning by design.
    User-side fix: assign an area to `climate.komanchi_living_smartir`.
  - The release was built in the `hausmanhub_hasc-1261` worktree (branch
    `ui-1.26.1-theme`); the 1.27.0 IR-learning WIP stays uncommitted in
    the main checkout `/home/ivsh/projects/hausmanhub_hasc`.
  - Next: 1.27.0 wizard IR-learning vertical ("2 lite"): restore a fixed
    `code_source` step, SmartIR code DB scan, Broadlink `.storage` codes,
    `remote.learn_command` last; fix the WIP test failures.
- Release 1.26.0 (panel redesign per HMH-II + selectable unavailable devices)
  is RELEASED, 2026-07-27:
  - Release commit `3ab7584` on `origin/main`; tag `v1.26.0`; GitHub Actions
    run `30282626657` passed; public release:
    https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.26.0.
  - Contents: dark HMH-II palette with inline SVG icons (per approved DESIGN.md
    revision 2, which overrides the historical neumorphism section 2 tokens);
    comfort defaults day 25.0/53%, night 25.5/50%, bounds 24.5-27, humidity
    step 1; unavailable devices are selectable with the `Сейчас недоступно`
    badge and a post-selection warning; backend `can_accept` covers
    available+unavailable and validation returns a `device_unavailable`
    warning instead of blocking (`save_allowed` true).
  - Gates: full suite 825 passed, 4 skipped, 732 subtests;
    `tools/check_local_release.py` passed after the version bump (829 tests
    plus fixture/naming/safety checks); headless Chrome visual QA at
    1224/420/360 px for wizard, room step, and configured overview.
  - Oracle review (gpt-5.6-sol) returned REJECT with 2 majors; one fix
    iteration: the `message` field in `climate-draft-validation.schema.json`
    now allows the dynamic `device_unavailable` warning via anyOf
    enum+pattern (was enum-only, rejected the real backend response). The
    second finding (hard `--hmh-*` palette vs `--hh-*` section-2 tokens) was
    dismissed as based on revision-1 history: DESIGN.md revision 2 explicitly
    takes priority and mandates the fixed dark HMH-II palette with radii
    12/16/20.
  - The 1.27.0 IR-learning WIP stays uncommitted in the main checkout
    `/home/ivsh/projects/hausmanhub_hasc`; the release was built in the
    `hausmanhub_hasc-1260` worktree from clean `main`.
  - Next: 1.27.0 wizard IR-learning vertical ("2 lite"): restore a fixed
    `code_source` step, SmartIR code DB scan, Broadlink `.storage` codes,
    `remote.learn_command` last; fix the WIP test failures.
- Release 1.25.3 (first-run wizard device-catalog rework) is RELEASED, 2026-07-27:
  - Release commit `f3cb4e7` on `origin/main`; tag `v1.25.3`; GitHub Actions
    run `30251991310` passed; public release:
    https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.25.3.
  - Full local gate in a clean worktree from the release commit: 825 tests
    passed, 4 skipped, 732 subtests passed; `tools/check_local_release.py`
    passed (829 tests OK plus fixture, naming, and repo-safety checks).
  - For this release the unfinished 1.26.0 `code_source` wizard step
    (IR-learning WIP reading nonexistent `state.choices`) was removed from
    the panel: IR constants, the step-list entry, the home→validation
    transition, the dispatch line, and `_renderFirstRunCodeSource`. The
    wizard flow is again home → validation.
  - `hausman-hub-panel.js` keeps unavailable in-room candidates visible as
    disabled checkbox rows with status/reason badges and a Home Assistant
    refresh hint. It adds the disabled similar-room climate-device group,
    the per-room `Показать все устройства` catalog (including a disabled
    `Тип не определён` pseudo-row for candidates with empty
    `suggested_types`), and an in-step refresh that merges newly discovered
    candidates without discarding valid selections or control channels.
  - Oracle review (gpt-5.6-sol) returned 4 blockers; one fix iteration closed
    all of them plus the medium issues:
    - Backend: every candidate now carries a stable opaque `candidate_key`
      (`ckey_<sha256(source_id)[:12]>`) in `climate_device_candidates` and
      `climate_setup_options`; both v1 JSON schemas and both fixtures require
      it. The UI merges selection state by `candidate_key` (fallback
      `candidate_id`) and resolves the current positional `candidate_id` at
      draft-build time, so refresh renumbering can no longer move a selection
      to the wrong physical device.
    - Frontend: new candidates start `selected: false` (explicit selection
      requirement); room-name matching tries the full normalized room name
      before the >=4-char stripped root (`Ванная`/`Зал` now match); a
      successful refresh invalidates stale per-room reports, validRooms, the
      draft, and validation while preserving selections.
    - Classification: `_unbound_suggested_kinds` now falls back to TRV name
      markers when `hvac_modes` is non-empty but uninformative
      (`("off", "auto")`); `("heat", "cool")` stays an air conditioner.
  - `tests/test_hausmanhub_panel_wizard.py` now has 21 tests (was 14). The
    refresh test models real positional renumbering (same `candidate_key`,
    different `candidate_id`) and asserts the draft posts the refreshed id.
    New backend tests: TRV marker with `("off", "auto")`, `("heat", "cool")`
    as AC, candidate-key stability across renumbering.
  - `tests/test_hausmanhub_panel.py` byte budget raised 200 -> 210 KiB (panel
    grew legitimately with the wizard rework, 205.7 KiB now).
  - Gates before commit: wizard file 21/21; dirty-tree suite 861 passed, 4
    skipped, 728 subtests passed, with 11 failures only in the pre-existing
    1.26.0 IR-learning WIP (`test_ir_code_storage`, raw remote endpoint,
    read-only skeleton, local-summary boundary); our diff adds none. The
    1.26.0 WIP files stay uncommitted: `__init__.py`, `climate_api.py`,
    `application/climate_ha_adapters.py`, `domain/climate_ha_calls.py`,
    untracked `ir_code_*.py`, `tests/test_ir_code_*.py`.
  - Next: 1.26.0 wizard IR-learning vertical ("2 lite"): restore a fixed
    `code_source` step, SmartIR code DB scan, Broadlink `.storage` codes,
    `remote.learn_command` last; fix the WIP test failures so the release
    gate runs green in the plain working tree.
- Release 1.25.2 (wizard device-selection fix) is RELEASED and DEPLOYED:
  - Release commit `3eb8ffe` on `origin/main`; tag `v1.25.2`; GitHub Actions
    run `30219220629` passed; public Latest release at
    https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.25.2.
    Full local gate in clean worktree: 812 tests passed, 4 skipped, plus
    check_local_release.py.
  - Root cause of the live "не выбирается устройство" bug: the entity catalog
    in `climate_ha_state_view.py` read `supported_features` with the strict
    guard `type(x) is int`. Real HA stores it as a `ClimateEntityFeature`
    IntFlag, so the guard zeroed it for every climate entity, command_types
    collapsed to `(climate.set_hvac_mode,)`, and every air-conditioner
    candidate failed validation with "device is missing required
    capabilities: power, target_temperature". The guard existed since 09aea13
    (native discovery, 1.21.0); tests and JSON dumps always carried plain
    ints, which hid the bug. Fix: `isinstance` check plus `int()`
    normalization, regression test `test_catalog_accepts_intflag_supported_features`.
  - Proven end-to-end before release: clean tag 1.25.1 fed IntFlag features
    reproduced the exact live error; fed plain ints it returned `ready`.
  - Deployed to live HA via the HACS update entity (explicit version v1.25.2)
    plus an HA restart. Verified live: `installed_version: v1.25.2`; draft
    validation for гостиная returns `status: ready`, `save_allowed: true`,
    `issues: []`; snapshot_revision `239926551809926` matches the local
    clean-tag reconstruction exactly. Four of five AC candidates validate
    `ready`; candidate_0030 (Electrolux air purifier) is honestly blocked on
    missing `target_temperature`, which is correct behaviour.
  - 1.25.1 (commit `4d15037`, tag `v1.25.1`) shipped the `detail` field in
    `unsupported_device_set` issues, which pinpointed the failure stage
    (import) on live without server logs.
  - Next development: 1.26.0 wizard IR-learning vertical (SmartIR code DB
    scan, Broadlink `.storage` codes, `remote.learn_command`) per the
    approved "2 lite" design. WIP files stay uncommitted in the working tree.
- Release 1.25.0 (universal IR-AC, approved variant 1) is RELEASED and DEPLOYED:
  - Release commit `82b29c6` on `origin/main`; tag `v1.25.0`; GitHub Actions
    run `30195356712` passed.
  - Contents: channel is an honest transport label (climate facades translate
    for any channel; `unsupported_control_channel` only for raw `remote.*`
    endpoints); bounded private-id-free `ir_remotes` in setup options; wizard
    "Устройства без комнаты" binding group, SmartIR hint, honest channel copy.
- Phase 3 (1.24.0) released: 9-tab panel, scenario engine, and connection settings.
- Commit `97547ad` is on `main`; tag `v1.24.0` points to it.
- The tag initially had no GitHub Release, so HACS could only offer `1.23.0`.
  On 2026-07-26 the public non-draft release `HausmanHub 1.24.0` was
  published: https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.24.0.
  GitHub now marks it Latest; HACS only needs to refresh its repository data.
- Full test suite 808 OK (4 skipped); local release check passed.

## Project state

- Version 1.20.1 is published as the latest stable GitHub release. It fixes the
  first contour bootstrap by projecting Home Assistant areas and resolving an
  entity's explicit area before its device's inherited area. The page now uses
  responsive field grids, ordered checkbox rows, clearly disabled buttons,
  explicit save guidance, and a room/device refresh action. The complete staged
  release gate passed 670 tests plus all fixture, package, version, naming, and
  repository-safety checks; Chrome verification passed at desktop and mobile
  widths. Kimi primary and proit review attempts failed on monthly-quota HTTP
  403 (`ses_06cec922fffeRdUNA2pb0SgMjm`,
  `ses_06cec3df7ffeYzFBwMleiAJRNs`); the ivsh profile was stopped after it
  returned no review (`ses_06cec3e2affeQF4t8pgI0kR7GI`). The exact unchanged
  staged hash then passed the bounded read-only OpenAI Oracle review in
  `ses_06ce3405dffeI59okayEMK5gMp` (root supervision session
  `ses_06ce9deeeffem2CDMesJGSVEXj`). Release commit
  `91ee1909ecdeacd2374c542723e8e316fcefd70a` passed GitHub Actions run
  `30077032795`; stable release `v1.20.1` is published at
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.20.1, and the
  remote tag resolves exactly to the release commit. No live Home Assistant
  change occurred. Next: refresh HACS, install `1.20.1`, restart Home
  Assistant, and hard-refresh the browser.

- Product and Home Assistant integration name: **HausmanHub**. **HACS** is only
  the installation/update mechanism and is never the product name. The old
  temporary four-letter product label must not return to UI, contracts, docs,
  tests, or GitHub presentation.
- Repository: `shumkiiv/hausmanhub_hacs` (public, MIT, `main`).
- Local checkout remains at the legacy filesystem path
  `/home/ivsh/projects/hausmanhub_hasc`; this local folder name is not a public
  product identifier and must not be copied into documentation or URLs.
- Version 1.7.7 performs the one-time naming correction before the Android app
  has a live decoder: display/package name `HausmanHub`, repository
  `shumkiiv/hausmanhub_hacs`, and public contracts `hausman-hub-*`. HACS is
  documented only as the installer. The HA domain `hausman_hub`, config-entry
  unique ID, and entity unique IDs remain unchanged so existing installations
  upgrade in place. New suggested entity IDs no longer contain the old label;
  HA retains registry names for existing entities with the same unique IDs.
  A release check prevents the old public names from returning. The change
  passed 355 local tests and disposable Core 2026.6.4/2026.7.0. Kimi k2p7
  failed before review (`ses_08191e6cdffeZFO3U7kFPu1jNi`, `err_7c5c5f07`)
  and Kimi k3 timed out; the independent fallback review passed in OpenCode
  session `ses_0818e4910ffeNoKwAj0QEiB030`.
- Version 1.7.8 completes roadmap item 18. A local-admin POST at
  `/api/hausman_hub/v1/admin/climate-profiles` accepts an exact versioned list
  of day/night profiles for every configured room. It uses the full saved
  `setup_revision` as an optimistic lock and rejects stale forms with HTTP 409
  before persistence. The operation cannot change rooms, device bindings,
  contour mode, active profile, or temporary temperature. It performs one
  contour-store write and no bridge read or command. If the existing schedule
  is enabled, its last-applied marker is cleared so the normal next schedule
  check can apply the edited active profile when managed control is enabled;
  the strict receipt exposes that pending automatic effect explicitly and does
  not claim it in disabled or shadow mode. The final staged tree passed 361 local
  tests and disposable Home Assistant Core 2026.6.4/2026.7.0. The independent
  read-only OpenCode reviews passed in sessions
  `ses_0816d871effeB7Q1hFv1K1D6DN` and `ses_081692737ffevvtAdFcTaAETTR`
  with no substantial findings.
- Version 1.7.9 completes roadmap item 19. A local-admin POST at
  `/api/hausman_hub/v1/admin/climate-schedule` configures the exact day/night
  wall-clock times and either arms or disarms automatic profile switching.
  Arming requires an automatic contour, managed bridge mode, explicit consent,
  and a current full `setup_revision`; saving itself performs one contour-store
  write and no bridge read or command. Disarming remains deliberately available
  in disabled, shadow, and canary modes, while re-arming there is rejected.
  Changed times or disarming clear temporary overrides because their former
  schedule boundary is no longer valid; an unchanged armed schedule preserves
  them and the applied-period marker. The final staged tree passed 370 local
  tests and disposable Home Assistant Core 2026.6.4/2026.7.0. The independent
  read-only review passed after this canary rule was made explicit and tested in
  OpenCode session `ses_08147ba73ffe8CaWSkrw20gsOX`.
- Version 1.7.10 completes roadmap item 20. Contour settings application,
  scheduled profile application, temporary room temperature, and return to the
  active schedule now emit the same strict
  `hausman-hub-climate-control-receipt` v1. Its action block contains a stable
  action code plus Russian name and, only for room actions, the public room ID
  and effective target temperature. Status and bounded reasons each include a
  stable code and Russian explanation. No source/entity/device binding,
  backend command, service, fingerprint, or bridge address is exposed. Request
  idempotency now binds both desired-state fingerprint and exact action context.
  The final staged tree passed 373 local tests and disposable Home Assistant
  Core 2026.6.4/2026.7.0. Independent read-only review passed in OpenCode
  session `ses_08137d64cffe1UgIfPWWcyU09Q`.
- Version 1.8.0 completes roadmap item 21. HausmanHub packages a frozen,
  redacted reference suite derived read-only from working climate revision
  `0bf681c4278f14f1ad7808b5fe0726b199bcdccc`: 30 cases cover cooling,
  heating, humidity, policy priority, freshness, timing, device availability,
  execution guards, and explicit limitations; 31 protections preserve the
  decision and executor safety boundaries. Each case contains normalized
  observations, the expected decision, abstract device intents, blockers, and
  exact source-test provenance, never live entity/source/device IDs, service
  calls, or addresses. The packaged JSON is strict-schema validated, bound to
  source Git blobs, and locked by a reviewed SHA-256. Its mode is permanently
  `reference_only` with commands disabled. The source climate module and
  Android repository were not changed. Roadmap item 22 must build HausmanHub's
  internal observation model against this fixed corpus; it must not weaken or
  rewrite the reference to fit a new implementation. The final staged tree
  passed 378 local tests, HACS/package/boundary/Android checks, and disposable
  Home Assistant Core 2026.6.4/2026.7.0. Independent read-only review passed
  in OpenCode session `ses_0811f930fffeb7iYWFxEvPAST5`.
- Version 1.8.1 completes roadmap item 22. The pure
  `ClimateObservationSnapshot` boundary represents home, controller, room, and
  logical-device facts using only stable HausmanHub room/device IDs. It makes
  freshness, missing rooms, unavailable or missing devices, normalized
  activity, targets, window state, timing, and physical feedback explicit;
  contradictory, mutable, non-finite, out-of-range, or cross-room values fail
  validation. `build_climate_observation_snapshot` is the only adapter that
  consumes a registry's private `source_id`, solely to look up imported state;
  the resulting model has no source/entity IDs, endpoints, services,
  transports, or commands. The command-free native preview now consumes this
  model and resolves devices by stable HausmanHub ID. A separate reference
  adapter proves that all 30 immutable version-1.8.0 cases fit the same model
  without changing the frozen corpus. The source climate module and Android
  repository remain unchanged. Roadmap item 23 must calculate room temperature
  and humidity targets on this boundary without adding execution authority.
  The final staged tree passed 386 local tests, HACS/package/boundary/Android
  checks, and disposable Home Assistant Core 2026.6.4/2026.7.0. Independent
  read-only review passed in OpenCode session
  `ses_0810846a3ffe8paQjkDJAIZrfr`; its only non-blocking future-kind note was
  closed by making reference display-name coverage complete.
- Version 1.8.2 completes roadmap item 23. The pure climate-target layer
  resolves each contour room from its saved day/night profile, keeping the
  selected profile temperature separate from the effective temperature. An
  explicit temporary override replaces temperature only; humidity and strategy
  remain those of the active profile. Each result carries the internal
  observation's fresh/stale/unavailable status, but missing observations never
  erase the user's saved comfort configuration. Target snapshots contain only
  stable HausmanHub contour/room IDs and cannot select equipment, build an
  intent, call Home Assistant, or authorize execution. The runtime exposes a
  read-only seam for the configured contour and never posts from it. All 30
  frozen reference cases resolve the exact recorded target temperature and
  humidity. Roadmap item 24 must determine heating, cooling, and humidifying
  demand from these targets without adding execution authority. The source
  climate module and Android repository remain unchanged. The final staged
  tree passed 394 local tests, HACS/package/boundary/Android checks, and
  disposable Home Assistant Core 2026.6.4/2026.7.0. Independent read-only
  review passed in OpenCode session `ses_080f72f34ffeKIcMnGgNggCfaW`; all four
  nonblocking precision notes were closed before commit by tightening docs,
  covering stale data, avoiding evidence-ledger mutation, and ignoring retained
  cache in disabled mode.
- Version 1.8.3 completes roadmap item 24. The pure demand layer combines one
  internal observation with the resolved room target and reports heating,
  cooling, and humidifying as independent required/not-required/unavailable
  channels. New cooling uses the working core's exact inclusive 0.7 C start
  gap; heating retains the native preview's strict 0.5 C comfort band; and
  humidifying is required only when humidity is more than five points below
  target. Stale observations, missing values, and suspect temperature never
  become required demand, while temperature and humidity availability remain
  isolated. This is raw comfort demand only: season conflicts, running-device
  hysteresis, equipment policy, safety priority, intents, and commands are not
  part of the layer. The runtime reads observation, targets, and demand once
  without evidence mutation and ignores retained state when disabled. All 30
  frozen cases map to deterministic raw demand, including the exact 25.7/25.6
  cooling boundary and the dry-room humidity anchor. Roadmap item 25 must
  resolve heating/cooling conflicts without adding execution authority. The
  source climate module and Android repository remain unchanged. The final
  staged tree passed 402 local tests, HACS/package/boundary/Android checks, and
  disposable Home Assistant Core 2026.6.4/2026.7.0. Independent read-only
  review passed with no substantial findings in OpenCode session
  `ses_080dc1768ffekUBX1A0v30rMvR`.
- Version 1.8.4 completes roadmap item 25. A pure thermal-resolution layer
  combines raw heating/cooling demand with the observed home season,
  occupancy mode, and central-heating state. Away-safe-off has first priority,
  away-keep observes, and invalid thermal data is unavailable at home. Winter
  or explicitly active central heating blocks opposing cooling; summer blocks
  heating; an unknown season preserves current-core compatibility by allowing
  cooling but holding heating until a heating mode is known. The result is one
  immutable heating/cooling/hold/observe/safe-off/unavailable state with a
  stable reason. Humidity remains an independent raw demand. The layer has no
  equipment, HA entity, intent, service, command, or execution authority. The
  runtime derives observation, targets, demands, and resolution through one
  non-evidence-mutating read and ignores retained state while disabled. All 30
  frozen cases resolve deterministically, while device policy remains roadmap
  item 26. The source climate module and Android repository remain unchanged.
  The final staged tree passed 412 local tests, HACS/package/boundary/Android
  checks, and disposable Home Assistant Core 2026.6.4/2026.7.0. Independent
  read-only review passed with no substantial findings in OpenCode session
  `ses_080cb528cffeu1SP2f12E7oY4I`. Its nonblocking source-normalization note is
  carried into item 26: the future native adapter must explicitly map the old
  `hvacMode == heating` and active-heating facts into HausmanHub's normalized
  heating-mode observation before external-source removal.
- Version 1.8.5 completes roadmap item 26. A pure equipment-policy layer maps
  the resolved thermal direction only to thermal devices explicitly selected
  in each contour room. Generic air-conditioner profiles match the frozen
  core's soft/normal/aggressive setpoint, fan, and quiet choices. Radiator
  thermostats retain the frozen 19 C day, 17 C night, below -10 C cold
  adjustment, and above 18 C daytime heat-load adjustment, while unknown
  heating/period data fail closed to observation. Floor heating has an
  explicitly documented new HausmanHub rule because the frozen module had no
  complete floor policy. Unavailable devices clear all proposed settings;
  stale and mixed snapshots cannot create a setting. The immutable plans keep
  stable HausmanHub IDs only, expose `commands_enabled=False`, and contain no
  HA entity, private source, service, intent, command, or execution authority.
  Runtime derives the plan through one non-evidence-mutating read and never
  posts. The transitional Climate API still does not supply ordinary runtime
  observations with all home period/heating/weather facts, so live TRV plans
  remain observe-only until a native HA observation adapter supplies them;
  this limitation is explicit and must be closed before removing the external
  module. Item 27 owns running-device hysteresis, timing, and short-cycle
  protection. The source climate module and Android repository remain
  unchanged. The final staged tree passed 424 local tests,
  HACS/package/boundary/Android checks, and disposable Home Assistant Core
  2026.6.4/2026.7.0. Two read-only OpenCode/Kimi k3 attempts inspected the
  staged tree and frozen source but returned no terminal report, so neither is
  counted as PASS: `ses_080b2d8a4ffeFX0o9wFoBmaVH0` and
  `ses_080aa6566ffelElJG2iyoowP71`.
- Version 1.8.6 completes roadmap item 27. A pure stability layer applies the
  frozen working-core start boundary, running hysteresis, gradual 27 C/low-fan
  softening, hard-off override, minimum run/off windows, and humidifier
  hysteresis only to devices selected in each contour room. Default AC timing
  is 8 minutes running and 6 minutes off; confirmed fast cooling uses 5/8,
  confirmed slow cooling uses 10/5, and confirmed short cycles add at most two
  off minutes up to a 10-minute ceiling. Exact interval boundaries release the
  protection, while an active window exposes bounded remaining seconds.
  Confirmed weak cooling escalates only after the preserved day/night dwell,
  first from low to medium fan and then from 26 C to the room target; stale or
  unconfirmed physical feedback cannot authorize escalation. The
  humidifier thresholds are expressed relative to the configured target so
  the frozen 45 percent target gives 39/44 normally and 40/45 during active
  cooling or heat load at least 26 C. An unconfirmed-closed window selects
  humidifier off before missing humidity is considered. The immutable result
  rejects forged actions, thresholds, remaining times, contradictory inputs,
  differing observation timestamps, and mutable collections; it has stable
  HausmanHub IDs only and
  `commands_enabled=False`. Runtime derives the protected plan through one
  non-evidence-mutating read and never posts. All 30 frozen cases are
  deterministic, with exact anchors for timing and humidity. The transitional
  Climate API still does not supply physical transition timestamps, confirmed
  short-cycle history, or reliable window state; the policy accepts these
  facts but does not invent them. Native acquisition and restart restoration
  remain items 33 and 30, so this result must not be wired directly to an
  executor yet. Item 28 owns manual mode, final priority ordering, and safe
  stop. The source climate module and Android repository remain unchanged.
  A read-only OpenCode/Kimi k3 audit inspected the staged implementation in
  session `ses_0808c1139ffeGfZVhX1YG2h6Um` but was interrupted before a final
  top-level PASS/FAIL and is not counted as PASS. Its completed research branch
  correctly identified that deterministic execution alone was weaker than
  direct reference comparison and that observation provenance needed an
  explicit boundary. Before commit, HausmanHub therefore binds target and base
  equipment plans to the exact observation timestamp, rejects a timestamp
  mismatch, compares all 11 timing/cooling reference anchors directly with
  frozen expected action/setpoint/fan/quiet fields, and adds direct night-dwell,
  stale-feedback, heat-load-boundary, unknown-window, and forged-humidity tests.
  The final staged tree passed 441 local tests, HACS/package/boundary/Android
  checks, and disposable Home Assistant Core 2026.6.4/2026.7.0.
- Version 1.8.7 completes roadmap item 28. A pure final policy layer preserves
  the frozen priority ladder: away, safety lockout, freshness guard,
  forced-auto-only, manual, auto, then direct-device requests as an external
  last fallback which is not admitted to the internal plan. Manual mode and a
  room-scoped manual request produce observation with no automatic device
  plans; forced automation rejects that request and keeps the automatic plan.
  Away-safe-off, open or unknown windows, missing temperature, and explicit
  cooling/heating denial produce a selected-device-only safe-stop result.
  Running or unknown AC/humidifier/floor activity needs a safe stop, confirmed
  stopped devices suppress the redundant stop, unavailable devices remain
  explicit, and radiator thermostats stay observe-only rather than receiving
  an invented safety setpoint. Stale state, suspect temperature, and stale
  delayed work observe with an empty device plan. All 30 frozen cases match
  expected policy, room action, and ordered blockers exactly. Control requests
  and execution guards are scoped to one stable room id. The immutable result
  rejects forged output, mutable collections, mixed device plans, and
  observation-time mismatches; it has `commands_enabled=False` and no HA
  entity, service, transport, or private source binding. Runtime derives the
  final policy from one non-evidence-mutating read and never posts. Item 29
  owns failure isolation between rooms and devices. The source climate module
  and Android repository remain unchanged. The final staged tree passed 454
  local tests, HACS/package/boundary/Android checks, and disposable Home
  Assistant Core 2026.6.4/2026.7.0.
- Version 1.8.8 completes roadmap item 29. The full strict climate pipeline now
  runs independently per configured room. A missing room input, no retained
  device, or a bounded local calculation violation produces a failed result
  only for that room and does not erase neighbouring policies. A configured
  device absent from the observation is removed only from that room's effective
  calculation and reported by stable HausmanHub id; `missing` and `unavailable`
  placeholders remain explicit while healthy devices in the same room keep
  their plans. Each immutable room result is `ready`, `degraded`, `unavailable`,
  or `failed` with fixed ordered reasons. The snapshot rejects forged states,
  mutable ids, mixed observation times, and private bindings; it always has
  `commands_enabled=False`. Runtime obtains one observation without evidence
  mutation or POST. Item 30 owns restoration of state and protective delays
  after restart. The source climate module and Android repository remain
   unchanged. The final staged tree passed 463 local tests,
   HACS/package/boundary/Android checks, and disposable Home Assistant Core
   2026.6.4/2026.7.0.
- Version 1.8.9 completes roadmap item 30. Confirmed climate transition facts
  now survive a Home Assistant restart. For every configured air conditioner a
  versioned per-entry Home Assistant store persists only the normalized phase,
  the last confirmed start and stop times, and the bounded confirmed
  short-cycle count, keyed by stable HausmanHub ids with no private bindings,
  sources, services, or command authority. On startup the memory is reconciled
  against the current registry: unbound or moved devices are dropped and
  future-dated memory after a clock change is reset. After a restart with
  retained memory the protection rearms once conservatively from fresh
  observations and then continues normally, so protective delays are not
  silently restarted from zero. A storage failure fails the climate
  calculation closed with no partial state and no commands;
  `commands_enabled` remains `False`. The source climate module and Android
  repository remain unchanged. The independent Kimi review passed after one
  fix iteration that made the stored version check strictly typed. The final
  staged tree passed 471 local tests, the HACS/package/boundary/Android
  checks, and disposable Home Assistant Core 2026.6.4/2026.7.0.
- Version 1.9.0 completes roadmap item 31. A strict command-free comparison
  layer now states, for every configured room and selected device, whether the
  observed state of the working climate module agrees with the native
  HausmanHub plan. Each room and device is `aligned`, `diverged`, or
  `not_comparable` with a fixed ordered reason list: stale observation,
  missing room policy, unavailable room data, manual observe, planned observe,
  unobserved or unavailable device, unknown activity, unobserved settings,
  activity mismatch, or settings mismatch. A stale observation short-circuits
  all rooms; manual mode and deliberate observe are honestly not comparable;
  an already stopped device needs no repeated stop. The comparison uses only
  stable HausmanHub ids and approved codes, always has
  `commands_enabled=False`, and the runtime accessor reads one observation
  without writes or POSTs. The source climate module and Android repository
  remain unchanged. The final staged tree passed 485 local tests, the
  HACS/package/boundary/Android checks, and disposable Home Assistant Core
  2026.6.4/2026.7.0.
- Version 1.9.1 completes roadmap item 32. Decision comparison is now proven
  on all 30 frozen reference scenarios: for each case the module's frozen
  decision is expressed as its post-decision observed state and the comparison
  verdict is locked in an exact table. 19 scenarios align exactly; 8 are
  honestly not comparable (manual mode, deliberate observe, stale data, and
  the thermostat activity the module never exposes); 3 execution-guard
  scenarios confirm a bounded fan-stage divergence — the frozen module
  escalates to medium while the ported stability layer does not escalate
  without confirmed feedback and elapsed run time. The divergence is frozen
  as the expected verdict, not hidden. Automatic rooms with no room-level
  action (for example a thermostat-only adjustment) now compare per-device
  plans instead of a blanket not-comparable. The comparison still creates no
  commands, carries no private bindings, and always has
  `commands_enabled=False`. The source climate module and Android repository
  remain unchanged. The final staged tree passed 491 local tests, the
  HACS/package/boundary/Android checks, and disposable Home Assistant Core
  2026.6.4/2026.7.0.
- Version 1.9.2 completes roadmap item 33. Strict Home Assistant device
  adapters now translate each proven final device plan into an exact call
  list from a closed service whitelist: `climate.set_hvac_mode` (cool/heat/
  off), `climate.set_temperature`, `climate.set_fan_mode`, and humidifier
  power. Calls name one validated registry control entity and bounded values
  (temperature 10–35, humidity 0–100, approved modes only); arbitrary fields
  are impossible. Translation stops honestly with bounded limits: missing
  control endpoint, missing capability, unsupported action, observe, hold,
  nothing to translate, or the quiet setting that has no strict call. A
  missing fan capability blocks the whole device translation rather than
  silently dropping part of the plan. This is translation only:
  `commands_enabled` is always `False` and nothing is executed. The source
  climate module and Android repository remain unchanged. The final staged
  tree passed 504 local tests, the HACS/package/boundary/Android checks, and
  disposable Home Assistant Core 2026.6.4/2026.7.0. The independent Kimi
  review initially stopped the set (FAIL) for an inconsistent floor-heating
  hvac call and a missing `HVAC_MODE` capability requirement; one fix
  iteration closed both with new tests. The same iteration fixed the
  floor-heating policy: in the heating season it now yields a strict
  set-temperature action instead of tripping the final-plan invariant and
  failing the room (no frozen reference case covers floor heating, so frozen
  parity is untouched). HVAC-mode calls now require the declared `HVAC_MODE`
  capability.
- Version 1.9.3 completes roadmap item 34. HausmanHub can now physically
  control the climate itself — exactly one explicitly configured trial room
  and only with every guard agreeing. A one-minute tick requires CANARY
  bridge mode, an automatic contour, a fresh observation, a ready room, a
  decisive comparison, trial-scoped devices with HausmanHub control
  endpoints, and a complete translation. It acts only when the native plan
  diverges from the observed state; alignment is honestly skipped as
  up-to-date, uncertainty denies without a single call. Execution uses only
  the strict adapter whitelist in order and stops at the first error. The
  redacted receipt keeps only the stable room id, a bounded status
  (applied/up_to_date/denied/failed), bounded reasons, and call counts. The
  enforced execution boundary now allows HA service calls only in the trial
  executor module and the legacy canary switch; the skeleton test locks
  this. The operator must remove the trial room from the external module's
  rooms to avoid double control. The source climate module and Android
  repository remain unchanged. The final staged tree passed 513 local tests,
  the HACS/package/boundary/Android checks, and disposable Home Assistant
  Core   2026.6.4/2026.7.0.
- Version 1.19.0 completes the second step of the full-panel phase: the
  sidebar page is now the full climate configuration UI. The plain-JS panel
  grew from status-only to sections for the control mode switch
  (disabled/managed with the 1.18.0 API and a double-control warning),
  day/night profile editing per room, the schedule editor (arm + HH:MM),
  home signals with candidate dropdowns, and per-room window bindings, while
  preserving the existing readiness/rooms/apply/temporary-temperature
  behavior and the combined panel contract v2 (sections use separate strict
  GETs). Per-section dirty flags stop the 30-second refresh from clobbering
  edited inputs, including across temporary panel GET failures; the window
  save has a busy guard; blank numeric fields are rejected client-side
  before any POST. Twelve executed-JavaScript tests pin exact POST payloads
  and the disabled, not-configured, configured, conflict-409, dirty-refresh,
  GET-failure-recovery, double-click, and blank-field states. Oracle review
  returned FAIL (dirty forms destroyed by a transient GET failure, missing
  busy guard on window save, `Number("")` becoming 0); one fix iteration
  resolved all three with regression tests. The final staged gate passes
  654 local tests and all package checks. Release commit `56f4a45` was
  pushed, GitHub Actions run `30031179629` passed, and stable release
  `v1.19.0` is published; its remote tag resolves exactly to
  `56f4a45ff4d602af3c0a9dea89a0a1a42d11ff71`. No live HA change occurred.
  Live read-only diagnostics confirmed HACS installed `v1.18.0` serving the
  old panel and the 1.18.0 `climate-mode` API healthy, with
  `contour_configured: false`. Next: 1.20.0 page contour wizard, then
  roadmap 39/40; the live contour still needs initial setup before managed
  mode can be enabled.

- Version 1.18.0 is a fully gated local release candidate (first step of the
  user-approved "full panel configuration" phase, plan
  `.omo/plans/2026-07-23-full-panel-configuration.md`). Three strict local
  admin APIs close the setup gaps that required config-flow wizards or raw
  JSON: `GET/POST /api/hausman_hub/v1/admin/climate-mode` (disabled/managed
  switch with explicit consent, configured-contour requirement, and an
  authoritative saved-options optimistic lock written through the normalized
  `create_options` path), `GET/POST /admin/home-environment` (home signals
  and lockout thresholds with the 1.17.0 selector domain rules plus bounded
  candidate catalogs), and `GET/POST /admin/climate-room-signals` (per-room
  window binding, previously unreachable in any wizard). The pure validation
  module `application/climate_signal_settings.py` rejects malformed shapes,
  wrong domains, unknown entities, bad thresholds (incl. OverflowError/NaN),
  and stale mode expectations; runtime gained `async_update_room_window`,
  `async_climate_mode_status`, `signal_entity_known`, and
  `async_signal_catalog`, and the HA state view gained
  `signal_entity_catalog`. Oracle review returned FAIL (stale-runtime
  expected_mode race, unhandled float overflow, missing POST guard tests);
  one fix iteration resolved all three, including a real options-corruption
  bug the new race test exposed (naive data+options merge wrote disallowed
  keys).   The final staged gate passes 642 local tests and all package,
  version, naming, and repository-safety checks. Release commit `525ac40`
  was pushed, GitHub Actions run `30026297975` passed, stable release
  `v1.18.0` is published, and its remote tag resolves exactly to
  `525ac40e4bfe32a45de8c482f3f0a5fcadd1dff8`. Publication context commit
  `7fcdca5` passed check run `30026542474`. Next: 1.19.0 panel page
  settings sections consuming these APIs, then 1.20.0 page contour wizard.

- Version 1.17.0 local release candidate complete (settings page rework, user request
  2026-07-23: best-practice design, reorganize, push, release). Plan:
  `.omo/plans/2026-07-23-settings-page-home-environment.md`. Scope:
  expose item 38 home signals in the options UI (previously only raw
  JSON editor) and convert navigation selects to native HA menus.
   `config_flow.py` has a `home_environment` options step with three optional
   entity pickers and two required box NumberSelectors. Both heating lockout
   thresholds use the registry range -40..60 °C and require `low < high`.
   `ClimateRuntime.async_update_home_environment()` replaces only the home
   block under the runtime lock, preserving concurrent room/device changes.
   The initial and advanced screens use `async_show_menu`; translations, unit
   tests, Core smoke navigation, README, manifest 1.17.0, changelog, and HACS
   validation are updated. `check_local_release.py` passed through a temporary
   Git index with 618 tests. Disposable Core checks were skipped because their
   expected Python environments are absent. A manual Home Assistant test archive
   was built from the
   current working tree at
   `/home/ivsh/projects/УД-hasc/releases/HausmanHub-1.17.0-test.zip`.
   It contains only `custom_components/hausman_hub`, includes the sidebar panel,
   passes `unzip -t`, and has SHA-256
   `82f5f8d4a5dc43d642be3d6e4fa9339970ff91e30478890fcb78053153f56b45`.
   The release code was committed as `909ae3d`, pushed to `origin/main`, and
   published as the latest GitHub Release `v1.17.0`:
   https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.17.0. The remote
   tag resolves exactly to `909ae3d`; its manifest declares `1.17.0` and the
   sidebar panel asset is present. The only GitHub check job concluded
   successfully:
   https://github.com/shumkiiv/hausmanhub_hacs/actions/runs/29991423859/job/89154943431.
   No live Home Assistant action occurred. Next: refresh the custom repository
   in HACS, install `1.17.0`, restart Home Assistant, and provision disposable
   Core environments for the deferred smoke check.
   Post-install read-only diagnostics against Home Assistant Core 2026.7.3
   confirmed that `hausman_hub` is loaded, the panel JavaScript returns HTTP
   200 and exactly matches the `v1.17.0` Git blob (SHA-256
   `4f796a24e4147a73ee3673a6568401dc0425feeafe61aad5b7cdb37acdb59a3f`), and
   the admin panel API exists (HTTP 403 for the intentionally read-only
   diagnostic token). WebSocket `get_panels` succeeds but omits
   `hausman-hub` for that non-admin user. This proved only that the static asset
   and authorization boundary were active; the later administrator screenshot
   proved that the panel itself had never been registered. No live state was
   changed.
- Version 1.17.1 is the sidebar registration hotfix. The administrator's
  sidebar editor screenshot proved that `hausman-hub` was absent rather than
  merely hidden. Home Assistant Core 2026.7.3 defines
  `panel_custom.async_register_panel` as an async function, but HausmanHub
  called it without `await`; the static asset registered first, explaining the
  live HTTP 200, while the panel coroutine never executed. `panel.py` now
  awaits registration, and the panel tests use an async mock so the former bug
  fails deterministically. The final staged package passed 618 local tests,
  HACS/package checks, version checks, and repository-safety checks. All three
  configured Kimi profiles stopped before review with the same monthly-quota
  HTTP 403 (`ses_071c8bf52ffeCp7AD0sQb6RwH1`,
  `ses_071c86d9cffe2MGwrMXqnx7l3d`, and
  `ses_071c827f4ffeQHSh5t41p627GO`); this is not a Kimi PASS. The direct
  read-only OpenAI fallback review passed with no substantial findings in
  OpenCode session `ses_071c619cfffeCD4QTUh6eD5vsI`. Release commit
  `d0efbd4` was pushed to `origin/main`; GitHub Actions run `29994143599`
  passed. Stable release `v1.17.1` was published at
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.17.1, and its
  remote tag resolves exactly to `d0efbd4`. No live Home Assistant write,
  update, or restart occurred. Next: refresh the custom repository in HACS,
  install `1.17.1`, restart Home Assistant, and hard-refresh the administrator
  browser.
- Version 1.17.2 fixes the next administrator-visible panel failure found
  after installing 1.17.1: the page registered and loaded, but its combined
  API returned HTTP 503 whenever climate control was still disabled because
  `async_public_snapshot()` treated that default state as unavailable. The
  combined panel contract is now version 2 and may return `snapshot: null`
  only for the narrow `ClimateSnapshotUnavailable` condition while still
  returning readiness. The frontend then renders the truthful disabled or
  temporarily unobservable status without rooms, contours, or action buttons.
  Other `ClimateRuntimeUnavailable` failures, including protection-memory
  faults, remain HTTP 503. Disabled, managed-unobservable, internal-failure,
  and executed JavaScript render regressions are covered. The staged release
  gate passed 622 tests plus HACS/package, version, naming, and repository
  safety checks. Kimi could not start its review because the provider returned
  monthly-quota HTTP 403 in session `ses_0718c6a51ffeOEOqbCDUJoHS0M`; no Kimi
  PASS is claimed. A first fallback review found the exception-width,
  contract-version, and test-depth gaps in session
  `ses_0718c1edaffeElMNdo58oqRoi3`; after all three were fixed, the final
  direct read-only OpenAI fallback review returned PASS in OpenCode session
  `ses_071864f04ffe3MwHXRFVF4Mm5X`. Release commit `1618e0b` was pushed to
  `origin/main`, GitHub Actions run `29998820030` passed, and stable release
  `v1.17.2` was published at
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.17.2. The remote
  tag resolves exactly to `1618e0b`; its manifest declares `1.17.2`, its panel
  API contract declares version 2, and the tagged frontend guards the absent
  snapshot. No live Home Assistant write, update, or restart occurred. Next:
  refresh HACS, install `1.17.2`, restart Home Assistant, and hard-refresh the
  administrator browser.
- Post-install live read-only diagnostics for 1.17.2 confirmed Home Assistant
  Core 2026.7.3, a loaded `hausman_hub` config entry, HACS installed/latest
  version `v1.17.2`, and a served panel JavaScript SHA-256
  `a936204bc586563d2ffaa4f91ae2ff2301736f16c09c5d7bd966d11918d412f0`
  that exactly matches tagged `v1.17.2` and contains the null-snapshot guard.
  Almost all live states were recreated at 11:01-11:02 UTC, consistent with a
  Home Assistant restart after installation. The red banner therefore comes
  from the admin backend request, not stale frontend code. The only stored
  diagnostic token is non-admin with no groups; the admin panel, readiness,
  system log, and local summary boundaries all reject it with 403, so it
  could not distinguish the administrator request's local-policy 403 from a
  runtime 503. A
  follow-up filename and credential-key search across project and user config
  directories found no separate Home Assistant administrator access file:
  `/home/ivsh/projects/УД-hasc/ha_read_access.json` is the only HA token file;
  the other credential stores belong to Codex/Figma and are unrelated. No live
  Home Assistant state was changed. The safe handoff is to create a temporary
  long-lived token from the administrator's own Home Assistant profile under
  Security, save it once as mode-600
  `/home/ivsh/projects/УД-hasc/ha_admin_access.json` with `base_url` and
  `access_token`, use it only for read-only diagnosis, then revoke it. On
  2026-07-23 the file was created with correct mode `600`, but its
  `access_token` value was only 9 characters and the HA authorization probe
  returned HTTP 400. This is still a placeholder, not a usable Home Assistant
  long-lived token. After the user retried, the file still had correct mode
  `600` but `access_token` was empty (0 characters). A validated hidden-input
  method was then used successfully. The final direct read-only check
  authenticated the token owner as a non-system administrator and owner.
  WebSocket `get_panels` contains `hausman-hub` with `require_admin: true`;
  the combined admin panel and readiness routes both return HTTP 200 from
  `http://172.30.0.92:8123`. The panel contract is version 2 with
  `snapshot: null`; readiness truthfully reports `status: disabled`,
  `bridge_mode: disabled`, and reason `bridge_disabled`. The Home Assistant
  system log contains no HausmanHub entries. This rules out a stale release,
  missing panel, and runtime 503. The remaining screenshot banner is caused
  by the route's intentional local-address guard when the browser reaches HA
  through an external URL or proxy; direct local HA access should work. No
  live state changed. Revoke the temporary admin token after diagnosis.
- A full-path follow-up on 2026-07-23 used the dedicated diagnostic SSH key to
  inspect the main Windows PC `172.30.0.37` without changing its user data.
  The PC has a working direct TCP route to `172.30.0.92:8123`, and the same
  authenticated panel/readiness requests return HTTP 200 from source
  `172.30.0.37`. Edge, Chrome, and Firefox history and origin-storage checks
  contain no Home Assistant `:8123` visit, `hassTokens` key, or HausmanHub
  origin; no Home Assistant Windows app is installed, and no active browser
  connection to HA exists. Therefore the reported screenshot was not produced
  by a normal browser profile on that PC. An isolated real Chrome/HA frontend
  run from the private network loaded `/hausman-hub` successfully: document
  title `HausmanHub – Home Assistant`, panel module HTTP 200, admin panel API
  HTTP 200, no loading failure or JavaScript exception, no generic red banner,
  and the expected `Управление климатом выключено` status. This proves that
  version 1.17.2 renders correctly in a fresh authenticated admin session.
  The unresolved failing client is a different device/profile/session; do not
  relax the intentional local-only boundary without an explicit security
  decision. All temporary local and Windows browser profiles were removed;
  no HA state, existing browser profile, or repository source was changed.
- Version 1.16.0 completes roadmap item 38. Windows, presence,
  outdoor temperature, and sensor quality now shape climate decisions.
  An open or unreadable configured window hard-locks its room into
  safe-off on the next tick, bypassing stability debounce, while an
  unbound window stays neutral; suspect or unknown room temperature
  also forces safe-off with computed targets kept for diagnostics.
  Absence applies a soft setback of minus two degrees heating and plus
  two degrees cooling, clamped to limits and never inverting demand,
  only in automatic modes and never on temporary or manual targets;
  unreadable presence changes no target, and auto-humidifying is
  denied for both absent and unknown presence. A weather lockout
  blocks automatic heating from 18 degrees outdoor and re-enables it
  from 16, keeping the previous explicit home-level permission inside
  the band (first observation there fails closed); cooling is
  untouched, manual and temporary targets bypass it, and both
  thresholds are registry options (16/18 defaults). Central heating
  now distinguishes unbound (neutral) from bound-but-off-or-unavailable
  (hydraulic radiators blocked and driven to safe-off); electric
  floors, heat pumps, and air conditioners are unaffected. The frozen
  reference suite was re-fingerprinted for the suspect-temperature and
  central-heating behavior changes, and 26 new acceptance tests cover
  the setback, lockout hysteresis, gates, and force safe-off
  semantics. Independent review returned FAIL (activity-based
  hysteresis was not a sound latch; away-setback formula was
  questioned); one fix iteration replaced the activity latch with
  explicit home-level weather-heating state, and a probe confirmed the
  away-setback formula matches its ±2 invariant on the exact
  questioned cases. The final tree passed 618 local tests, the full
  release gate, and disposable Core 2026.6.4 and 2026.7.0.
  Remaining roadmap: 39 (per-room schedules and profiles), 40
  (standalone climate release), then 41-50 (HausmanHub 2.0 platform).
- Version 1.15.0 completes roadmap item 37. A safe migration wizard
  imports existing legacy climate settings into an empty native
  contour: a one-shot GET-only read of the old API with private-address
  validation (address never persisted), an explicit per-device entity
  mapping confirmed from the native HA catalog (ordinal form tokens,
  skip only for passive sensors), and one atomic write with a stored
  receipt. Rooms and day/night comfort profiles are copied (targets
  into both), `auto`/`forced_auto_only` become `managed`, `manual`
  becomes `disabled`; schedule, live state, authority, history, and the
  API address are never moved. The safe rollback removes exactly the
  migrated setup only when the full registry+contour fingerprint still
  matches, and is blocked after any manual change. The confirm step
  rebuilds the draft against a fresh catalog before writing, and the
  receipt is saved before the setup write with compensation on failure.
  Independent review returned FAIL (rollback compared only ID sets,
  orphaned import on receipt failure, stale catalog at confirm, unbounded
  GET, loose receipt schema, skip on active devices, raw private ids in
  form fields); one fix iteration resolved all seven with regression
  tests, and the follow-up self-review confirmed the exact-fingerprint
  rollback and receipt-first ordering. The final tree passed 591 local
  tests, the full release gate, and disposable Core 2026.6.4 and
  2026.7.0 with a real migration import plus rollback on a blank Core.
  Remaining roadmap: 38 (windows/presence/outdoor/sensor quality), 39
  (per-room schedules and profiles), 40 (standalone climate release),
  then 41-50 (HausmanHub 2.0 platform).
- Version 1.14.0 completes roadmap item 36 entirely. The external
  climate module is retired: `ClimateControlMode` is now only
  `disabled` or fully native `managed`; legacy shadow/canary entries
  migrate once via `async_migrate_entry` (config entry version 2) to
  disabled with contours switched to `ContourMode.OBSERVE`, saved
  bridge target and canary room fields removed, and the old evidence
  store key deleted best-effort. The legacy routes
  (`/actions`, `/operations`, admin shadow-evidence and
  canary-preflight) now answer 404, and all bridge code is deleted
  (`climate_bridge.py`, `climate_commands.py`, `climate_evidence.py`,
  `climate_evidence_storage.py`, `climate_canary_preflight.py`,
  `android_climate.py`, `climate_operations.py`, and the
  `climate_import.py` parser, whose dataclasses live in the neutral
  `application/climate_discovery.py` while the parser itself survives
  only as `tests/climate_bridge_fixture.py`). Bridge-bound devices
  without a CONTROL endpoint are quarantined: excluded from
  observation, projections, ownership, trial, and apply, and surfaced
  as the `needs_reimport` readiness reason. The Android room-control
  block honestly advertises no executable action with bounded reasons.
  Independent review returned FAIL (migration not persisted, duplicate
  panel views, parser left in production); one fix iteration resolved
  all three and the follow-up passed. The final tree passed 578 local
  tests, the full release gate, and disposable Core 2026.6.4 and
  2026.7.0 with a managed end-to-end scenario that performs zero
  bridge reads. Remaining roadmap: 37 (safe migration wizard for
  existing settings), 38 (windows/presence/outdoor/sensor quality),
  39 (per-room schedules and profiles), 40 (standalone climate
  release), then 41-50 (HausmanHub 2.0 platform).
- Version 1.13.0 completes roadmap item 36 sub-step 36f3. Startup in
  MANAGED and DISABLED never reads the external module; the bridge
  client is constructed only for SHADOW and CANARY (their evidence
  purpose), and `_require_client` now enforces that in every path, so
  legacy shadow-evidence, canary-preflight, and canary-action routes
  cannot touch the bridge outside those modes. The bridge target is
  optional for MANAGED (a legacy saved target is accepted but unused)
  and required for SHADOW/CANARY. The contour wizard tries native
  discovery first and falls back to the one-time bridge address form
  only when native discovery is unavailable; saving a contour without
  a bridge target is allowed. Disabled-mode admin wizards observe
  natively (explicit discovery) while the disabled control pipeline
  keeps its no-observe gate. Review returned FAIL (disabled wizard
  fell back to the bridge form; `_require_client` allowed managed
  bridge contact); one fix iteration resolved both with poison
  regressions and the follow-up passed. The final tree passed 630
  local tests, the full release gate, and disposable Core 2026.6.4 and
  2026.7.0. Remaining: 36g retires shadow/canary, the legacy actions
  route, and the bridge itself.
- Version 1.12.0 completes roadmap item 36 sub-step 36f2. All climate
  setup wizards (setup options, current setup, contour draft
  create/validate/save, registry import snapshot) now build their
  discovery snapshot from the native Home Assistant catalog in every
  mode; the bridge is never touched, locked by poison tests. Unassigned
  entities are honestly roomless: the contour wizard assigns them on
  the room step, the import wizard asks for a room, and saving requires
  an explicit assignment. New devices receive a fresh private
  `hausmanhub-native-<entity_id>` source id (never the entity id) plus
  control/observation endpoints, so a saved contour runs natively at
  once; bound devices keep their private source id and endpoints
  through re-imports. Draft save stays forbidden in CANARY and is
  atomic elsewhere. Review returned FAIL (endpoint loss on re-import,
  duplicate room assignment, multi-room assignment dead-end, loose
  override parameters); one fix iteration resolved all four with
  regression tests and the follow-up passed. The final tree passed 624
  local tests, the full release gate, and disposable Core 2026.6.4 and
  2026.7.0 with a migrated end-to-end wizard scenario. Remaining
  sub-steps: 36f3 (startup and bridge lifecycle semantics) and 36g
  (shadow/canary retirement with the legacy actions route).
- Version 1.11.0 completes roadmap item 36 sub-step 36f1 (the Oracle
  split of 36f is 36f1 native discovery, 36f2 wizard cutover, 36f3
  mode/bridge lifecycle). The new pure
  `application/climate_native_setup.py` enumerates climate-relevant Home
  Assistant entities (climate, humidifier, temperature/humidity
  sensors) through `HomeAssistantClimateStateView.entity_catalog()` and
  builds the existing `ClimateImportSnapshot` wizard shape natively:
  rooms come from the registry plus native observation, bound devices
  keep their private `source_id` (matched via `endpoints[].entity_id`,
  all endpoints excluded from unbound candidates), unbound entities
  become candidates with `source_id = entity_id` and the locked
  unassigned sentinel `room_id = ""`. Classification is conservative
  (domain + device_class + supported_features intersected with the
  strict vocabulary). Identity option A was locked: the setup payload
  keeps its current contract version, and a native candidate's
  `source_id` never migrates into the private registry `source_id`.
  Wizard cutover (including accepting unassigned candidates with an
  explicit room choice and allowing draft save in MANAGED with atomic
  rebuild) is 36f2; startup/bridge lifecycle is 36f3. Independent
  review returned FAIL (multi-endpoint duplication, negative
  supported_features, empty-state availability, wizard-chain test
  gaps); one fix iteration resolved all four and the follow-up passed.
  The final tree passed 615 local tests and the full release gate.
- Version 1.10.0 completes roadmap item 37. HausmanHub now has its own
  admin page in the Home Assistant sidebar (`panel_custom` registration,
  `require_admin=True`, `config_panel_domain` for the settings gear).
  The plain-JS webcomponent
  `custom_components/hausman_hub/frontend/hausman-hub-panel.js` (no
  build step, no external URLs, Russian UI, 30-second polling) reads a
  combined admin payload and offers the everyday actions: apply saved
  contour settings and set/clear per-room temporary temperature. Three
  new admin-gated routes serve it:
  `GET /api/hausman_hub/v1/admin/panel`,
  `POST .../admin/panel/apply`, `POST .../admin/panel/temporary-temperature`;
  tablet and read-only users get 403, malformed bodies get 400, and
  unexpected exceptions propagate instead of masquerading as 503.
  Registration in `panel.py` is idempotent (static paths once per
  server lifetime under a separate `hass.data` key, panel skipped when
  already present) and the panel is removed on entry unload. Registry
  and setup editing deliberately stay in the config-flow wizards.
  Independent review initially returned FAIL (non-idempotent
  registration, exception mapping, missing lifecycle tests); one fix
  iteration resolved all three and the follow-up passed. The final
  tree passed 610 local tests, the full release gate, and disposable
  Home Assistant Core 2026.6.4 and 2026.7.0 including the new routes.
- Version 1.9.10 completes roadmap item 36 sub-step 36e2. In MANAGED
  mode all five read projections (Android public snapshot, contours
  snapshot, apply preview, readiness, administrator snapshot) are
  served by the native builders from 1.9.9; the runtime never touches
  the bridge for them, locked by poison-bridge acceptance tests
  (`NativeProjectionSwitchTest`: zero bridge calls in managed and
  disabled, fail-closed mapping without a state view, shadow still
  reads the bridge deliberately). SHADOW and CANARY projections keep
  bridge reads because their purpose is migration evidence and canary
  comparison (36g). DISABLED keeps its no-observe behavior. The apply
  preview now reports the native strict HA plan call count instead of
  legacy bridge command counts (three legacy tests updated to the new
  number). Presentation helpers moved to the neutral
  `application/android_climate_values.py` shared by both builders,
  resolving the 36e1 review finding. Independent review passed with
  three LOW findings: the module docstring was updated, the
  no-state-view fail-closed test was added, and a shadow/canary
  projection matrix remains assigned to 36g. The final tree passed 601
  local tests and the full release gate. Remaining bridge usage:
  startup refresh (36f), registry import and setup/discovery wizards
  (36f), shadow evidence and the legacy canary route (36g).
- Version 1.9.9 completes roadmap item 36 sub-step 36e1. The new pure
  module `application/climate_native_projections.py` builds the five
  external projection payloads (Android tablet contract v12, contour
  snapshot, contour apply preview, readiness, administrator snapshot)
  from the native `ClimateObservationSnapshot` and the version-2
  registry only, with no bridge contact possible by construction.
  Production consumers are NOT switched yet; that is sub-step 36e2,
  which will also need a neutral home for the presentation helpers the
  module currently imports from `android_climate.py` (review finding,
  deliberately deferred). Golden and parity tests
  (`tests/test_climate_native_projections.py`) lock byte-identical
  payloads against the legacy builders for the same physical situation
  plus the documented semantic differences: native reconciliation
  covers configured devices only (bridge-only devices no longer count
  as unregistered), the native apply preview reports the real strict
  HA plan call count instead of legacy bridge command counts, and
  integral floats versus JSON ints are normalized in comparison.
  Independent review initially returned FAIL; one fix iteration added
  readiness hardening (full room-observation coverage and device
  availability required), room-mismatch checks in the contour room
  status, an authority gate for settings apply availability, a positive
  CANARY control-gate baseline with single-mutation closures, and
  serialization stability goldens. The follow-up review passed. The
  final tree passed 599 local tests, the HACS/package/boundary/Android
  checks, and the staged-version check; the disposable Core smoke was
  skipped because no runtime path changed. The external module still
  serves the registry import and setup/discovery wizards (36f) and the
  shadow evidence and legacy canary route (36g).
- Version 1.9.8 completes roadmap item 36 sub-step 36d. Settings
  application no longer uses the external Climate API: manual contour
  apply, scheduled day/night switching, temporary temperature, and return
  to schedule run the native chain "persist desired contour state → native
  HA observation → native plan → all-or-nothing scope preflight → strict HA
  calls through the single strict executor → bounded (about two seconds)
  observation verification". The pure planner lives in
  `application/climate_application.py` with models in
  `climate_application_models.py`; the trial executor boundary is
  generalized to `ClimateStrictHaCallExecutor`, and the skeleton still
  finds HA service calls only in `switch.py` and `climate_ha_executor.py`.
  Apply and schedule require every active contour room fully managed and
  ready (one blocked room cancels every call); temporary temperature checks
  only its own room. Disabled, shadow, and canary reject before reading
  native state. Persistence order: apply writes nothing, schedule saves
  profiles and the period marker first (a denied transition is not retried
  by the timer), temporary set/clear saves the override change first.
  Receipt v1 is unchanged: confirmed/pending/partial/unavailable with
  bounded reasons; a duplicate request id only re-observes and may promote
  to confirmed. The independent one-minute managed controller stays
  unchanged and may later reapply a divergent plan. The external module
  still serves the Android public snapshot, apply preview, readiness,
  setup wizards, shadow evidence, and the legacy canary `/actions` route
  (sub-steps 36e-36g). The final tree passed 571 local tests; release
  gate, disposable Core checks, and independent review are recorded in
  the final report of the 36d session.
- Version 1.9.7 completes roadmap item 36 sub-step 36c. The whole internal
  climate pipeline (preview, targets, demands, resolutions, equipment,
  stability, policy, isolation, comparison, call translation, trial and
  managed rooms) now reads only the native Home Assistant observation from
  1.9.6; the external Climate API is no longer touched by the internal
  contour. The new `HomeAssistantClimateStateView` boundary exposes bounded
  immutable states with a strict attribute whitelist; a broken state source
  fails the observation closed with no bridge fallback and no cross-system
  fact mixing. Disabled mode still does not observe. Comparison now checks
  the native plan against actual HA state: alignment suppresses redundant
  calls, divergence permits action, incomparability denies. The external
  module still serves the Android public snapshot, settings application,
  readiness, and setup wizards (sub-steps 36d-36f). The final staged tree
  passed 549 local tests, the HACS/package/boundary/Android checks, and
  disposable Home Assistant Core 2026.6.4/2026.7.0. The independent
  read-only review initially stopped the staged tree (FAIL): an absent
  native state view still fell back to the external bridge in the preview
  and shared observation paths, and preview/managed coverage was missing.
  One fix iteration removed the fallback (no state view now yields an
  unavailable observation, never a bridge read), added poison-bridge
  preview/managed tests, and migrated 22 legacy tests to the native
  observation path. The follow-up review passed in OpenCode session
  `ses_07cd1c3ffffeM9Isuzx4UKkMk7`; the suite now has 551 local tests.
- Version 1.9.6 completes roadmap item 36 sub-step 36b. The pure
  `application/climate_ha_observations.py` adapter builds the internal
  observation snapshot directly from Home Assistant states through the
  version-2 registry bindings: room temperature/humidity from passive
  sensor endpoints (with a climate-entity `current_temperature` fallback),
  window from the room binding, mode and observed targets from the local
  contour, device activity from state plus `hvac_action`, AC transitions
  and short-cycle counts from restart protection memory, outdoor
  temperature (also feeding the heat-load rule), presence, and central
  heating from the home bindings. The day period comes from the local
  contour schedule; season stays honestly unknown. Missing, unavailable,
  stale, or non-numeric values stay unknown and never become permissive.
  The adapter consumes an abstract `ClimateHaStateView` and imports no
  Home Assistant code; the HA wrapper arrives with the runtime switch in
  36c. Execution behavior is unchanged. The final staged tree passed 539
  local tests, the HACS/package/boundary/Android checks, and disposable
  Home Assistant Core 2026.6.4/2026.7.0.
- Version 1.9.5 completes roadmap item 36 sub-step 36a. The climate registry
  moves to schema version 2 with HausmanHub's own Home Assistant observation
  bindings: a room may hold an optional window binary sensor, a passive
  sensor may hold one observation endpoint strictly matching its kind, and a
  new home environment block holds optional outdoor-temperature, presence,
  and central-heating entities with strict domain validation. Stored version
  1 registries migrate once to version 2 with every new binding absent, so
  an old configuration never becomes permissive. Execution behavior is
  unchanged: observation and commands still use the external Climate API
  path; sub-steps 36b-36g (native observation adapter, runtime switch, local
  desired-state application, native projections, bridge-independent control
  mode, poisoned-bridge acceptance) are recorded in the roadmap. The final
  staged tree passed 527 local tests, the HACS/package/boundary/Android
  checks, and disposable Home Assistant Core 2026.6.4/2026.7.0.
- Version 1.9.4 completes roadmap item 35. Ownership now expands one verified
  room at a time. A strict promotion operation moves one room to HausmanHub
  management only with every guard agreeing: the room is in the contour,
  bridge mode is `canary` or `managed`, the contour is automatic, the
  observation is fresh, the room is ready, and the comparison is aligned
  (verified parity). Every room device must already hold a HausmanHub control
  endpoint; a partially transferred room is denied. Promotion is atomic: the
  registry is saved whole, a storage failure keeps the previous registry and
  yields an honest failure receipt, and re-promotion answers
  already-managed. Managed rooms run on the same one-minute guard chain as
  the trial room: act only on divergence, skip on alignment, deny on
  uncertainty, execute only the strict whitelist with fail-closed order.
  Ownership receipts are redacted (stable room id, bounded status and
  reasons, device counts). The source climate module and Android repository
  remain unchanged. The final staged tree passed 518 local tests, the
  HACS/package/boundary/Android checks, and disposable Home Assistant Core
  2026.6.4/2026.7.0. The same iteration aligned contour binding validation
  with the trial design: CANARY-scoped active devices now count as
  engine-managed alongside MANAGED, so a trial room no longer starts with a
  binding error. Passive temperature and humidity sensors legitimately stay
  observed: they neither block their room's promotion nor need a control
  endpoint.
- Workspace boundary: this thread may change only HausmanHub and its integration
  wrapper. The Android application is developed separately in
  `/home/ivsh/projects/УД-android`; it may be inspected only read-only for
  contract compatibility. Never edit, format, generate files, build, commit,
  push, or otherwise mutate that directory or its repository from this thread.
- The existing climate contour/module is also strictly read-only for this
  thread: never edit its source, Node-RED flows, configuration, repository, or
  live runtime. The current bridge may call only its fixed Climate API. The
  final product must reimplement the proven climate behavior and device
  adapters entirely inside HausmanHub, verify parity without double commands, and
  then remove the external module as an installation requirement.
- Home Assistant baseline: Core 2026.6.4 or newer.
- Version 1.0.0 established the product as a platform of automatic contours.
  Climate is the first contour. The ordinary Russian options flow chooses
  several rooms/devices; old registry/bridge/native-preview and helper-canary
  tools are hidden under advanced settings.
- The current 1.6 climate contour deliberately reuses the existing
  `hausman-climate` algorithm and executor while the public HausmanHub surface is
  stabilized. This is a migration bridge, not the final architecture. Roadmap
  points 21–40 capture the behavior, build the internal engine and strict Home
  Assistant device adapters, compare both implementations, transfer control
  room by room, and finally remove the external API dependency. Private
  registry plus public contour storage already save atomically.
- Public `GET /api/hausman_hub/v1/contours` returns strict
  `hausman-hub-contours` v1 state without source/entity IDs. Automatic status
  requires fresh engine state, auto mode, authority, device availability, and
  matching targets. Version 1.0.0 sends no climate POST and does not sync
  parameters into the engine; mismatches are explicit `attention`. See the
  [1.0.0 contour decision](LLM_WIKI/Manual/2026-07-18-hausmanhub-v1-0-0-universal-contours.md).
- Version 1.1.0 adds the first normal contour-settings execution path while
  keeping the existing `hausman-climate` algorithm and executor. A saved
  automatic contour uses a distinct `managed` bridge mode; legacy `shadow`
  remains strictly no-POST and legacy one-room `canary` remains separate.
  Explicit confirmation can apply only typed room strategy, temperature, and
  automatic mode, in that order. A bounded in-memory idempotency ledger
  reserves the request before the first POST, never resubmits ambiguous or
  duplicate requests, and rereads Climate API state before reporting
  confirmation. Room humidity is declared unsupported for apply because the
  current engine has no shared room-humidity command. Contour contract v2 adds
  observed strategy and apply capability; local tablet preview/apply routes
  expose no private binding or backend payload. See the
  [1.1.0 apply decision](LLM_WIKI/Manual/2026-07-19-hausmanhub-v1-1-0-confirmed-contour-apply.md).
- Version 1.2.0 replaced the shared
  multi-room comfort fields with one short parameters step per selected room.
  Each room stores its own validated temperature, humidity, and strategy using
  the existing contour registry and Android contour v2 shapes, so no persisted
  data migration or contract bump is needed. Editing preselects only a fully
  validated saved contour and uses its saved values even when current engine
  targets differ. Every selected room must have a selected device; exact
  per-room keys prevent incomplete or hidden inputs. The review screen lists
  public room names and their targets. Setup/save remain zero-command; the
  separate confirmed 1.1 apply path is unchanged, including unsupported
  humidity. See the
  [1.2.0 room-parameters decision](LLM_WIKI/Manual/2026-07-19-hausmanhub-v1-2-0-room-parameters.md).
- Version 1.3.0 gave every contour room
  exact `day` and `night` comfort bundles and an approved active profile.
  Existing v1 contour storage is migrated once to storage v2 by copying the
  former targets into both profiles with `day` active, so installation or
  migration changes no effective target and sends no command. The ordinary
  Russian options flow separately configures both profiles, selects one
  profile for all rooms, and then reuses the existing explicit apply preview
  and confirmation. Configuring or selecting a profile only atomically saves
  HausmanHub state; only the apply step may call the existing `hausman-climate`
  executor. Ordinary contour editing updates the active bundle and preserves
  the inactive bundle. Public contour contract v3 exposes active/day/night
  comfort values without private bindings. See the
  [1.3.0 profile decision](LLM_WIKI/Manual/2026-07-19-hausmanhub-v1-3-0-day-night-profiles.md).
- Version 1.4.0 established the first options page and
  ordinary climate workflow use plain Russian labels, with `strings.json` and
  the English-locale fallback intentionally mirroring Russian so a locale
  mismatch cannot produce a half-English UI. The visible sections are
  Climate, Home information, and Diagnostics/maintenance. Contour/device
  internals remain stable but are hidden from the ordinary language.
  An explicitly confirmed local-time schedule can now switch every room
  between the saved day/night profiles. A one-minute HA clock adapter is
  always registered; the runtime performs no bridge I/O unless the schedule
  is enabled, the contour is automatic, the bridge is managed, and the desired
  profile differs. It atomically persists the new active profile before
  reusing the same typed, idempotent contour executor. It never retries on the
  next minute because the selected profile is already persisted. Storage v3
  migrates v1/v2 with a disabled 07:00/23:00 schedule. Tablet contour contract
  v4 exposes only enabled/day/night times. See the
  [1.4.0 schedule decision](LLM_WIKI/Manual/2026-07-19-hausmanhub-v1-4-0-russian-schedule.md).
- Version 1.5.0 is the published HausmanHub release. One room may receive a
  temporary 18–28 °C target in 0.5 °C steps while an automatic schedule is
  armed for the current local-time period. The override is stored separately
  from the saved day/night bundles, persists before the first POST, and is
  applied only through the existing typed `hausman-climate` executor for the
  selected room. It clears on the next day/night transition or through a
  separate confirmed early-return action. Ambiguous command results are never
  automatically reposted. Storage v4 migrates v1–v3 with no override; public
  contour contract v5 and the strict local tablet temporary-temperature route
  expose no private bindings. See the
  [1.5.0 temporary-temperature decision](LLM_WIKI/Manual/2026-07-19-hausmanhub-v1-5-0-temporary-temperature.md).
- The 1.5.0 release candidate passed 289 local tests, isolated Home Assistant
  2026.6.4 and 2026.7.0 checks, and a final read-only Kimi review with no
  significant findings (session `ses_084f948c2ffee4C3vSqj22zKaT`).
- Version 1.6.0 completed the first HausmanHub-only roadmap item. It adds
  `GET /api/hausman_hub/v1/capabilities`, a local-tablet discovery
  response containing only installed HausmanHub features, public paths, and contract
  versions. It is independent of current climate command readiness and exposes
  no home data, private binding, or climate-module address.
- Version 1.6.1 completed the second HausmanHub-only roadmap item. It advances
  `hausman-hub-home` to v5 and embeds the
  public contour projection in the same response as live rooms and devices.
  Both projections use one imported Climate API snapshot; the legacy
  `/contours` route remains available. Android and the climate module are not
  changed. The final staged review passed after fixture reachability was made
  explicit (Kimi session `ses_084b63f0bffeaYv70SAOrV4Jqu`).
- Version 1.6.2 completed the third HausmanHub-only roadmap item. Public home and
  contour contracts v6 carry one immutable Russian `display_names` catalog.
  Private engine room modes and arbitrary device states are normalized to a
  bounded set of HausmanHub codes before projection; unknown external text is never
  echoed to the tablet. The catalog also covers every bounded room-control and
  contour reason, and the schema allow-lists all device capability codes. The
  final read-only Kimi review passed after those completeness checks were added
  (session `ses_0849e5c55ffesSAtzPiPLoPqe2`).
- Version 1.6.3 completed the fourth HausmanHub-only roadmap item. Home contract v7
  gives every registered room an explicit factual `actual` block with current,
  stale, or unavailable data status, temperature, humidity, and normalized
  engine mode. Missing source data stays null/unknown; legacy flat fields remain
  temporarily for Android compatibility. The final read-only Kimi review passed
  (session `ses_084881905ffeftJ53HfmP6RXTu`).
- Version 1.6.4 completed the fifth HausmanHub-only roadmap item. Home contract v8
  separates the imported engine `active_target` from HausmanHub `saved_profiles` for
  day and night. An unconfigured contour keeps all saved profile values null
  instead of copying the current engine target. The final read-only Kimi review
  passed (session `ses_0847a2b90ffe5Z4brgW45Gy2m2`).
- Version 1.6.5 completed the sixth HausmanHub-only roadmap item. Home contract v9
  and contour contract v7 expose the exact next real local schedule transition
  and the exact end of an active temporary temperature. Production projection
  uses Home Assistant local time, and the schedule calculation follows real UTC
  minutes across daylight-saving changes. The final read-only Kimi review
  passed (session `ses_0824c7fa7ffe02CSROzGL3CO5h`).
- Version 1.6.6 completed the seventh HausmanHub-only roadmap item. Home contract v10
  adds `allowed_actions` to every room. Existing `actions` remain the device's
  supported controls, while `allowed_actions` contains only commands executable
  now for that exact room. Runtime and schema both require aggregate
  `commands_enabled` to match whether at least one room has an allowed action.
  Both configured OpenCode review profiles failed before review with token
  refresh `401`; the final Codex audit found no remaining issue after adding the
  strict aggregate-to-room schema relation.
- Version 1.6.7 completed the eighth HausmanHub-only roadmap item. Home contract v11
  adds `action_availability` for every advertised room action. Each entry has an
  exact allowed flag and bounded blocked-reason codes whose Russian labels are
  supplied by the existing `display_names` catalog. The schema requires action,
  permission, and reason lists to stay consistent. OpenCode review again failed
  before reading the change because token refresh returned `401`; the final
  Codex audit found no remaining issue.
- Version 1.6.8 completed the ninth HausmanHub-only roadmap item. Home contract v12
  adds a deterministic JSON-safe integer `state_revision` over all public home
  content except `generated_at`. Equal public content keeps the same revision;
  any visible state, configuration, or permission change produces a new opaque
  value. Clients compare equality only; the value is not monotonic. OpenCode
  review again stopped before reading the change because token refresh returned
  `401`; the final Codex audit found no remaining issue.
- Version 1.6.9 completed the tenth HausmanHub-only roadmap item. A repository-local
  compatibility check decodes the v12 home fixture into the scalar and
  collection types audited read-only in the existing Android `HomeRoom`,
  `HomeDevice`, and `HomeAction` models. It also constructs strict HausmanHub action
  requests, enforces Android `Long` and exact JSON-number limits, device-domain
  mappings, and Russian blocked-reason labels. The check reads and changes only
  HausmanHub files in CI. It proves model-level compatibility, not that the current
  Android application already has a live HausmanHub v12 network decoder. The final
  staged tree passed 314 local tests and both supported Home Assistant Core
  checks. OpenCode stopped before review with token-refresh `401` in session
  `ses_08227e465ffekdVIgv90Up8d7b`; the final Codex audit added exact coverage
  between all HausmanHub device kinds and Android domain mappings and found no
  remaining issue.
- Version 1.7.0 completed the eleventh HausmanHub-only roadmap item. The new strict
  `hausman-hub-climate-rooms` v1 contract projects the union of discovered and
  configured rooms using only stable HausmanHub IDs. It sorts deterministically,
  preserves the configured HausmanHub name, disables all selection for stale data,
  and keeps a configured-but-missing room visible and unselectable. Fixed
  Russian status labels ship in the payload. The contract contains no bridge
  origin, source device ID, entity ID, or command. It is intentionally an
  application contract only in this point; the administrative draft HTTP route
  belongs to roadmap item 14. The final staged tree passed 319 local tests and
  both supported Home Assistant Core checks. OpenCode stopped before review
  with token-refresh `401` in session `ses_0821dbbf9ffe3R4Ym2RCx1eFzu`; the
  final Codex audit added a schema rule forbidding stale per-room status inside
  a current snapshot and found no remaining issue.
- Version 1.7.1 completed the twelfth HausmanHub-only roadmap item. The strict
  `hausman-hub-climate-device-candidates` v1 contract projects discovered and
  configured devices without source IDs, entity IDs, backend commands, or
  bridge details. It carries bounded HausmanHub kind codes with Russian names,
  response-local `candidate_0001` references, and an opaque JSON-safe snapshot
  revision that changes when private candidate bindings change. Freshness,
  current availability, already-configured, unsupported, missing-source, and
  registry-mismatch states fail closed. Configured-but-missing devices remain
  visible. This is still an application contract only; item 14 will expose the
  administrative draft route. The candidate revision ignores read time alone
  but changes with private binding or candidate state. The final staged tree
  passed 326 local tests and both supported Home Assistant Core checks.
  OpenCode stopped before review with token-refresh `401` in session
  `ses_082149289ffetmVMUsPAlvHXps`; the final Codex audit corrected unavailable
  configured-device status and timestamp-only revision churn and found no
  remaining issue.
- Version 1.7.2 completed the thirteenth HausmanHub-only roadmap item. The new strict
  `hausman-hub-climate-room-suggestions` v1 contract links response-local
  candidate references to rooms only through the explicit fresh source room
  relation. It never guesses from device names and never assigns or saves.
  Every suggestion requires confirmation and has fixed Russian confidence and
  reason labels. Stale data removes all room suggestions; missing sources and
  registry mismatches remain suggestion-free; unavailable or unsupported
  devices may explain their detected room but cannot be accepted. The format
  shares the candidate snapshot revision and remains internal until the item
  14 administrative draft route. The final staged tree passed 331 local tests
  and both supported Home Assistant Core checks. OpenCode stopped before review
  with token-refresh `401` in session `ses_0820c85d8ffesZEl7NOXnUq5NF`; the
  final Codex audit strengthened schema relations among status, reason,
  confidence, suggested room, and acceptance and found no remaining issue.
- Version 1.7.3 completed the fourteenth HausmanHub-only roadmap item. One fixed
  local-admin route, `/api/hausman_hub/v1/admin/climate-drafts`, now exposes
  strict setup choices through GET and creates a deterministic unsaved draft
  through POST. The request binds response-local candidate references to an
  exact JSON-safe snapshot revision, rejects changed or stale discovery data,
  validates per-room comfort ranges and detected device kinds, and never
  exposes source or entity IDs. GET and POST perform only a bridge state read:
  they save neither registry nor contours, send no commands, and do not even
  advance in-memory shadow-readiness evidence. The response explicitly keeps
  `save_allowed` false and `validation_required` true for item 15. The final
  staged tree passed 340 local tests, the HACS/package/boundary checks, Android
  model compatibility, and Home Assistant Core 2026.6.4 and 2026.7.0. The
  independent Kimi review could not start because provider session
  `ses_081f59898ffeL2TSbbZKMf8fYg` returned `Unexpected server error`
  (`err_26c09fac`). The final Codex audit added the missing GET surface needed
  to obtain candidate references and prevented setup reads from changing
  shadow evidence; it found no remaining issue.
- Version 1.7.4 completed the fifteenth HausmanHub-only roadmap item. A local-admin
  POST at `/api/hausman_hub/v1/admin/climate-drafts/validate` accepts the exact
  draft response, re-creates it against one fresh discovery snapshot, rejects
  stale candidate revisions and any material draft change, and resolves
  private source bindings only after those checks. Deep validation preserves
  an explicitly selected suggested device kind, requires a controllable device
  in every room, and verifies that imported capabilities can construct the
  existing HausmanHub registry and contour model. Its strict Russian result is either
  `ready` with future `save_allowed`, or `blocked` with bounded issue codes;
  `command_allowed` is always false. Validation performs no persistence,
  command, or shadow-evidence update. Setup bodies have a separate 256 KiB
  bound while ordinary commands remain at 16 KiB. The final staged tree passed
  346 local tests, package/boundary/Android checks, and Home Assistant Core
  2026.6.4 and 2026.7.0. Kimi provider session
  `ses_081e7a57fffegiNPU9QW3CfaTQ` failed before review with server reference
  `err_6718dd9d`. The final Codex audit strengthened blocked-result schema
  consistency and explicit request-size regression coverage and found no
  remaining issue.
- Version 1.7.5 completed the sixteenth HausmanHub-only roadmap item. A local-admin
  POST at `/api/hausman_hub/v1/admin/climate-drafts/save` refreshes discovery,
  deeply revalidates the exact unchanged draft, resolves private device
  bindings only after validation, and builds the existing HausmanHub climate
  registry and `existing_climate_core` contour model under one runtime lock.
  Registry, contour, and shadow-evidence stores use the existing
  rollback-protected setup transaction: a failed later write restores the
  prior working configuration, while rollback failure remains explicitly
  unavailable instead of reporting success. The strict private-id-free receipt
  says `saved`, `commands_sent: false`, and `restart_required: false`. The route
  is local-admin-only, has the separate 256 KiB setup limit, sends no device
  command, and maps a stale snapshot to HTTP 409 without persistence. The final
  staged tree passed 349 local tests, package/boundary/Android checks, and Home
  Assistant Core 2026.6.4 and 2026.7.0. Kimi provider session
  `ses_081d77549ffe5piZVGFmOgJuGd` failed before review with server reference
  `err_4169f40f`. The final Codex audit added direct stale-save and
  blocked-draft regression checks and found no remaining issue.
- Version 1.7.6 completed the seventeenth HausmanHub-only roadmap item. A local-admin
  GET at `/api/hausman_hub/v1/admin/climate-drafts/current` projects the exact
  saved climate contour into a strict private-id-free editor model. It keeps
  per-room day and night profiles, active profile, temporary temperature,
  schedule, mode, and assigned device kinds separate, so transient engine or
  override values cannot replace saved comfort settings. `setup_revision`
  fingerprints the complete stored registry and contour while
  `snapshot_revision` fingerprints current device bindings. Missing, stale,
  unavailable, or mismatched devices remain visible but set
  `editing_allowed: false` with a fixed Russian reason; an absent contour is an
  explicit `not_configured` result. Reading refreshes discovery without
  persistence, commands, or shadow-evidence changes. The final staged tree
  passed 352 local tests, package/boundary/Android checks, and Home Assistant
  Core 2026.6.4 and 2026.7.0. Kimi provider session
  `ses_081c8eff3ffe1X2SVwve701Amw` failed before review with server reference
  `err_9d1c65ec`. The final Codex audit bound every issue code to its exact
  Russian message and correct global/device scope and found no remaining issue.
- The final architecture was clarified on 2026-07-20: HausmanHub must ultimately
  contain the complete currently working climate algorithm. During migration,
  the existing module remains read-only and serves as a behavior oracle through
  its fixed API. After parity, HausmanHub must work from its own selected Home
  Assistant devices and the separate climate module must no longer be required.
  Progress is tracked in the
  [50-item HausmanHub roadmap](LLM_WIKI/Manual/2026-07-19-hausmanhub-50-point-roadmap.md).
- Version 0.4.0 was committed as `2e8cda3` and pushed to `origin/main` after
  its 153 tests, disposable Core 2026.6.4/2026.7.0 checks, and final Kimi
  review passed. This source push did not create a tag, release, HACS
  publication, deployment, or live-home change. The boundary is recorded in
  the [0.4.0 canary note](LLM_WIKI/Manual/2026-07-17-hausmanhub-v0-4-0-input-boolean-canary-control.md).
- Version 0.5.0 implements the first complete climate facade in
  HausmanHub. It adds a versioned logical Device Registry for rooms, ACs, TRVs,
  humidifiers, floor heating, sensors, private endpoint roles, capabilities,
  control owner, and observed/canary/managed scope. Import from the current
  `hausman-climate` v1 state is read-only and never auto-registers a device.
- The Android-facing HausmanHub contract exposes only stable HausmanHub IDs and provides
  local authenticated state/actions routes. Separate local-admin routes expose
  private import candidates and atomically replace the registry. Android never
  receives raw HA entity IDs, Climate API source IDs, Node-RED details, vendor
  transport, or backend command payloads.
- The climate bridge has `disabled`, `shadow`, and one-room `canary` stages.
  It accepts only a literal private HTTP(S) origin and two fixed Climate API
  v1 paths. Shadow translates but never posts. Canary requires a fresh state,
  exact room/binding, current climate-core authority readiness, configured
  capability, climate-core ownership, and canary/managed device scope before
  POST. The current climate-core remains responsible for auto/manual policy,
  cooldown, safety, authority, physical feedback, and actual execution.
- Typed HausmanHub intents now cover room target/mode/minimum/strategy/off and
  device power/temperature/humidity/HVAC/fan contracts for AC, TRV,
  humidifier, and floor-heating kinds. No generic proxy, caller-provided
  service, private source/entity ID, backend type, arbitrary URL, or payload is
  accepted. The architecture and rollout are in
  [the climate guide](docs/climate-control-architecture.md) and the durable
  [0.5.0 decision](LLM_WIKI/Manual/2026-07-17-hausmanhub-v0-5-0-climate-facade.md).
- Version 0.5.0 was committed as `5ac09c5` and pushed to `origin/main` after
  it passed 191 local tests, the HACS/version/repository
  safety checks, and disposable Home Assistant Core 2026.6.4 and 2026.7.0
  lifecycles on Python 3.14.3. The Core check also exercised all four climate
  routes through real loopback HTTP authentication in the disabled rollback
  state. Kimi model `kimi-for-coding/k2p7` completed the final read-only staged
  review in session `ses_09070e1c2ffeeTgDvZ3A3kiLUu` with no substantial
  findings. The verified `cc04029` tree was published as the non-prerelease
  latest GitHub Release `v0.5.0`; its tag resolves to that exact commit and
  both GitHub source archives were reachable. Publication did not deploy HausmanHub
  to a live home, enable either canary, or modify the Android repository.
- Version 0.5.1 implements the first operator-ready HausmanHub climate workflow.
  Home Assistant options now contain a guided local-admin draft for rooms and
  typed devices, a separate preview/reconciliation step, and explicit atomic
  save confirmation. An advanced JSON editor remains optional. Eight JSON
  Schema v1 files ship inside the integration for the Android and admin
  contracts.
- Android climate actions in 0.5.1 require a bounded public `request_id` and
  return a bounded versioned receipt with an opaque HausmanHub `operation_id`.
  Identical retries return the same receipt without another GET or POST;
  conflicting reuse is rejected. Canary HTTP acceptance is only `pending`,
  an explicit negative backend answer is terminal `rejected`, and transport
  ambiguity remains unavailable. HTTP acceptance is never physical success.
  Only an observable later state can become
  `confirmed`; a room cannot have two pending HausmanHub canary submissions.
- The disposable Core check now includes a temporary loopback Climate API and
  real Home Assistant owner/tablet authentication. It previews and saves a
  synthetic registry, reads the Android home contract, retries a shadow
  action, queries its receipt, and asserts a measured zero command POST count
  before restoring `disabled` and removing only the temporary registry.
- HausmanHub 0.5.1 was published from `494ae94` as the non-prerelease GitHub Release
  `v0.5.1` after 204 local tests, disposable Core 2026.6.4/2026.7.0 checks,
  successful GitHub Actions, and a final Kimi review with no findings. HACS
  installed it on the live Core 2026.6.4 home and the owner completed the
  required restart. Post-restart evidence showed installed/latest `v0.5.1`,
  the new operation contract v1 loaded, an unknown receipt fully redacted, and
  the admin readiness route closed to the non-admin verification account. The
  live climate bridge remained `disabled`, with no target or canary room; the
  fail-closed action check returned unavailable before execution. No physical
  climate canary or device command was run.
- Version 0.5.2 persists a
  redacted rolling 24-hour evidence window with a five-minute sample interval,
  bounded matched/missing/moved/stale observations and rejected/translated
  intents. The window stores only timestamps, public HausmanHub room IDs, and
  approved action labels; it is bound to the exact registry fingerprint and
  resets on any registry change. Private origin, source/entity identifiers,
  payloads, backend responses, and tokens never enter its Store or API shape.
- A local administrator can evaluate one candidate room through the guided
  options flow or the versioned climate-shadow-evidence route. `ready` requires
  fresh exact registered bindings, current climate-core authority, three
  spaced matching samples, successful shadow translation of room target and
  room off, and no candidate anomaly. A configured canary remains fail-closed
  before POST until this persisted result is ready, and 0.5.2 limits the first
  executable climate canary scope to those two room actions. This code does
  not authorize or activate a live physical canary; live deployment must keep
  the climate bridge `disabled`. The durable decision is in
  [the 0.5.2 evidence note](LLM_WIKI/Manual/2026-07-17-hausmanhub-v0-5-2-shadow-evidence.md).
- HausmanHub 0.5.2 was committed as `f3ec8ad`, passed 212 local tests, disposable
  Core 2026.6.4/2026.7.0 checks, two final Kimi reviews, GitHub Actions, and was
  published as the non-prerelease release `v0.5.2`. HACS installed it on the
  live Core 2026.6.4 home. After the owner restarted Core, installed/latest
  both reported `v0.5.2`, the new shadow-evidence admin route was present and
  correctly forbidden to the non-admin verification account, and climate
  home/action remained unavailable because the bridge was still `disabled`.
  No physical command or canary was attempted.
- Version 0.5.3 added the HausmanHub-only operator-import workflow. The options
  wizard obtains fresh Climate API candidates read-only and exposes only
  ephemeral `candidate_NNN` selector values plus device/room labels. The
  private source ID is neither displayed nor accepted from the form. The
  operator supplies a public HausmanHub ID and chooses a control entity with Home
  Assistant's native selector; HausmanHub re-reads the snapshot, rejects drift, and
  infers only capabilities supported by the candidate's typed command list.
  The selected room/device remain in an unsaved draft until the existing
  preview and separate atomic confirmation. It never auto-imports another
  candidate, deletes a registry record, or sends a Climate API command POST.
  The package passed 217 local tests plus release/file-safety checks
  and the full options-flow lifecycle on disposable Core 2026.6.4 and
  2026.7.0 with an exact two-device registry and zero command POSTs. Kimi model
  `kimi-for-coding/k2p7` completed the final read-only staged review in session
  `ses_08e986dbaffe6gCgi4wPgxStqP` with no substantial findings. Commit
  `eb05bce` was pushed and published as the non-prerelease latest release
  `v0.5.3` after successful GitHub Actions. HACS installed it on the live
  Core 2026.6.4 home; after the owner restarted Core, installed/latest both
  reported `v0.5.3` and the new candidate-import translation keys were loaded.
  Climate home/action still returned unavailable because the live bridge
  remained `disabled`; no physical command or canary was attempted.
  A non-activating supervised one-room checklist is documented in
  [the rollout checklist](docs/climate-canary-rollout-checklist.md).
- Version 0.5.4 adds the HausmanHub-only one-room preflight. Its guided options
  flow selects one room strictly from the saved registry and combines exact
  reconciliation, redacted shadow evidence, the fixed `set_room_target` plus
  `turn_room_off` scope, per-room pending state, and disabled rollback
  readiness. Only complete evidence in `shadow` can produce
  `ready_for_authorization`; the result always keeps
  `activation.allowed=false` and requires separate owner authorization. It
  performs no climate command POST, does not enable canary, and does not save
  options or registry. The prepared package passed 224 local tests, the full
  release/file-safety checks, and disposable Core 2026.6.4/2026.7.0. Kimi
  model `kimi-for-coding/k2p7` completed the final read-only staged review in
  session `ses_08ca230b5ffe4LBnH7j2hMTROH` with PASS and no substantial
  findings. Commit `2435c7f` was pushed and published as the latest stable
  release `v0.5.4` after successful GitHub Actions. HACS installed it on the
  live Core 2026.6.4 home; after the owner restart, installed/latest both
  reported `v0.5.4`, the new preflight steps and fields were loaded, and
  climate home/action remained unavailable because the bridge stayed
  `disabled`. No physical command or canary was attempted. The
  implementation decision is recorded in
  [the 0.5.4 preflight note](LLM_WIKI/Manual/2026-07-18-hausmanhub-v0-5-4-canary-preflight.md).
- Version 0.5.5 exposed the canonical
  saved-room preflight through one local-admin-only POST route and adds
  explicit checked/generated/valid-until freshness timestamps. Expired state
  blocks readiness independently of saved evidence. Two installed JSON
  Schemas define the exact query and response; activation remains structurally
  false, the tablet role is forbidden, and no options, registry, canary mode,
  or command POST can be changed. The final staged package passed 226 local
  tests, the full release/file-safety checks, and disposable Core
  2026.6.4/2026.7.0. Kimi model `kimi-for-coding/k2p7` completed the read-only
  staged review in session `ses_08b9a95d1ffe9AVm46wQzzPqZQ` with PASS and no
  substantial findings. Commit `23aa3f8` was pushed and published as the
  latest stable release `v0.5.5` after successful GitHub Actions. HACS
  installed it on the live Core 2026.6.4 home; after the owner restart,
  installed/latest both reported `v0.5.5`, the new admin preflight route was
  present and forbidden to the non-admin verification account, and climate
  home/action remained unavailable because the bridge stayed `disabled`. No
  physical command or canary was attempted. The decision is recorded in
  [the 0.5.5 contract note](LLM_WIKI/Manual/2026-07-18-hausmanhub-v0-5-5-preflight-admin-contract.md).
- Version 0.5.6 published the tablet home contract v2. It
  explicitly v2 and adds one public `control` result per room: whether commands
  are enabled, the evidence-qualified target/off actions, and a closed set of
  normalized blocked reasons. It derives availability from the same canary,
  freshness, binding, authority, device availability, evidence, and pending
  gates used by runtime. The old home v1 schema remains installed; a new strict
  v2 schema and synthetic fixture define the added shape. Command planning now
  also rejects a device marked unavailable. Android code, live registry,
  bridge activation, and physical commands remain out of scope. The final
  staged package passed 229 local tests, release/package/file-safety checks,
  and disposable Core 2026.6.4/2026.7.0. Kimi
  `kimi-for-coding/k2p7` session `ses_08b7a860affeOVomxNvxlvfWbi` completed
  the staged review and follow-up with PASS and no substantial findings.
  Commit `b62f1d7` was pushed, passed GitHub Actions, and was published as the
  latest stable release `v0.5.6`. HACS installed it on the live home while the
  climate bridge and action path stayed closed; the owner restart remained
  pending when the next development slice began. The decision is recorded in
  [the 0.5.6 room-control note](LLM_WIKI/Manual/2026-07-18-hausmanhub-v0-5-6-android-room-control.md).
- Version 0.5.7 replaced mixed Russian
  and internal English operator text with plain Russian names, descriptions,
  errors, statuses, reasons, actions, and room names. Fixed selectors now pass
  string values plus translation keys instead of explicit English labels that
  could override frontend translations. Unknown result codes stay hidden.
  The repository README, GitHub workflow labels, and public GitHub About
  description are Russian. This slice changed no climate contract or authority
  and deployed with the bridge disabled. It passed 231 local tests,
  disposable Core 2026.6.4/2026.7.0, final Kimi review, and GitHub Actions.
  Commit `979c4c5` was published as latest stable release `v0.5.7`; HACS
  installed it on the live home without configuring a registry or enabling
  the bridge. See the
  [0.5.7 Russian interface note](LLM_WIKI/Manual/2026-07-18-hausmanhub-v0-5-7-russian-interface.md).
- Version 0.5.8 was released on 2026-07-18. Android home contract v3
  keeps the v2 room action and blocked-reason shape and adds `action_inputs`.
  The target-temperature input is numeric and required, with an exact public
  range of 18–28 °C and a 0.5 °C step. The command validator and public
  projection use the same constants; an unsupported target action does not
  advertise input metadata. Strict v3 schema/fixture are added while v1/v2
  remain packaged. This is contract preparation only: it does not change the
  Android repository, live registry, bridge state, or physical authority. The
  prepared package passed 232 local tests, release/package/file-safety checks,
  and disposable Core 2026.6.4/2026.7.0 with zero climate command POSTs. Kimi
  model `kimi-for-coding/k2p7` completed the final read-only staged review in
  session `ses_08b312059ffedrMEVGxBLevcNI` with PASS and no substantial
  findings. See
  the [0.5.8 input-contract note](LLM_WIKI/Manual/2026-07-18-hausmanhub-v0-5-8-action-inputs.md).
- Version 0.5.9 was published and installed through HACS. Android home contract v4
  adds `action_presentations` for the two initial room actions. Each advertised
  action has fixed Russian title and description; the target-temperature field
  has its own title and explanation; room off requires user confirmation while
  target adjustment does not. Presentation keys must exactly follow advertised
  actions, and strict v4 schema/fixture enforce the copy and confirmation rule.
  Earlier v1-v3 home schemas remain packaged. This is still client-contract
  preparation only: it changes no Android repository, live registry, bridge
  state, or physical authority. All 232 local tests, the release/package/file
  safety checks, and disposable Core 2026.6.4/2026.7.0 passed with measured
  zero climate command POSTs. Kimi model `kimi-for-coding/k2p7` completed the
  final read-only staged review in session `ses_08a6b28e4ffeLp6u9BYpGw1F4O`
  with PASS and no substantial findings. See the
  [0.5.9 action presentation note](LLM_WIKI/Manual/2026-07-18-hausmanhub-v0-5-9-action-presentations.md).
- Version 0.5.10 was published and installed through HACS. The single nine-field
  options form is replaced by a one-choice settings menu with four separate
  areas: rooms/devices, climate-controller connection, aggregate information,
  and a clearly non-climate service switch test. The connection flow asks for
  its mode first, then an address only for check/trial modes, and a room only
  for one-room trial control. Saving one area preserves the other validated
  areas; choosing disabled still removes the private address and room. Russian
  labels and repository instructions describe the resulting user path in
  ordinary language. This changes no Android contract or runtime authority,
  keeps the live bridge disabled, and sends no physical commands. The 233
  local tests and disposable Core 2026.6.4/2026.7.0 checks pass. Kimi model
  `kimi-for-coding/k2p7` completed the final staged read-only review in session
  `ses_08a36c03bffeXybMbvHK4IPj8g` with PASS and no substantial findings.
  Commit, publication, and HACS installation completed; the owner has not yet
  confirmed the post-install Home Assistant restart. See the
  [0.5.10 settings-menu note](LLM_WIKI/Manual/2026-07-18-hausmanhub-v0-5-10-simple-settings-menu.md).
- Version 0.6.0 started moving climate
  policy into HausmanHub. A validated one-room policy stores temperature and humidity
  targets. A pure decision engine uses fresh transitional Climate API state,
  fixed ±0.5 °C/±5% deadbands, registered device kinds, and availability to
  report heating, cooling, humidifying, hold, stale, or unavailable. A fifth
  Russian options area previews that decision and requires separate target
  confirmation. Execution is structurally `preview_only` with commands always
  false; a disabled bridge performs no state I/O. Existing installations
  default to the disabled native policy. Existing climate-core remains the
  transitional observation/execution adapter while native HausmanHub observation,
  planning, cooldown, manual override, and later separately authorized
  execution are developed. All 244 local tests, the release checks, and the
  disposable Core 2026.6.4/2026.7.0 checks pass. The staged implementation and
  the final fail-closed delta received Kimi PASS reviews with no substantial
  findings. Commit `a765cc7` was pushed, release `v0.6.0` was published, and
  HACS reports installed/latest `v0.6.0`; the owner still needs to restart Home
  Assistant before using the new fifth settings area. See the
  [0.6.0 native preview note](LLM_WIKI/Manual/2026-07-18-hausmanhub-v0-6-0-native-climate-preview.md).
- The owner clarified the end product after 0.6.0: HausmanHub is a platform of
  autonomous contours, not a technical climate bridge or a collection of
  manual device controls. A user adds a contour, assigns rooms, observations,
  and actuator devices, sets comfort parameters and safety limits, then HausmanHub
  continuously owns its decisions and operation. Climate is the first contour;
  later contours reuse a shared device registry, lifecycle, status, override,
  and conflict model. Transitional climate-core, shadow, canary, private
  bindings, and migration details must move out of the ordinary user path.
  The 0.6.0 one-room preview is an internal foundation, not the target UX. See
  the [contour-platform product direction](LLM_WIKI/Manual/2026-07-18-hausmanhub-contour-platform-direction.md).
- Further HausmanHub-only development is tracked in the
  [post-0.5 roadmap](LLM_WIKI/Manual/2026-07-17-hausmanhub-post-v0-5-0-roadmap.md):
  the operator registry, formal Android contract, measurable shadow, command
  receipts, confirmation, and non-activating one-room preflight now exist.
  Physical canary execution remains a separate explicitly authorized phase.
- A public `custom_components/hausman_hub/` observation foundation with the
  local 0.4.0 helper-canary addition is present. It may be added manually as an
  HACS custom repository; it is not in the public HACS catalog.
- The skeleton contains a local square `brand/icon.png`, so Home Assistant can
  show its original icon without relying on an external brand asset.
- A Russian safe-check guide is available at
  `docs/home-assistant-safe-check.md`. It guides HACS refresh, installation,
  visual confirmation, the local aggregate diagnostic summary, and the
  isolated helper-canary check; it still
  explicitly excludes sharing diagnostics archives, configuration files, home
  addresses, credentials, names, identifiers, and device data.
- The optional local-viewer guide now ends with a simple choice: when the page
  is not needed, no action is required; when Home Assistant does not offer the
  exact read-only role, do not configure that optional page or edit internal
  files. Ordinary nine counts and diagnostics still work. Kimi found no issue
  in this wording or its focused local test; see the [local access guidance
  review](LLM_WIKI/Manual/2026-07-16-kimi-local-access-guidance-review.md).
- The Home Assistant setup screen uses plain labels: `Только чтение` and
  `Проверка без изменений` in Russian, with matching English labels. Its text
  no longer describes this public repository as private, and a local test
  guards all setup, options, error, and selector text.
- The skeleton passed isolated runtime smoke checks in Home Assistant Core
  2026.6.4 and 2026.7.0 on Python 3.14.3. They used disposable empty
  configurations only; no device, Node-RED, Home Assistant service, or live
  API work was performed. The smoke check also loads the installed diagnostics
  adapter and verifies its fixed redacted report after each approved mode
  change. It also reserves one HausmanHub-like sensor name only in that temporary
  configuration, then proves that a new HausmanHub setup keeps all nine count
  sensors, does not overwrite the occupied name, and leaves the other eight
  protected names unchanged. After HausmanHub is removed, that temporary external
  record must still be unchanged. The same isolated check then creates and
  removes HausmanHub once more, requiring the same nine sensors and the unchanged
  external record again. It requires no HausmanHub device record and requires each of
  the nine HausmanHub sensors to remain unattached to a device. It also requires Home
  Assistant to refuse a second HausmanHub setup while keeping the existing setup
  unchanged and limited to its nine sensors. After each HausmanHub removal, an
  authenticated temporary exact read-only user must receive only an unavailable
  response from the retained local summary route, with none of the nine counts.
  It also requires every removed HausmanHub count state to be absent from the
  temporary state machine. After the final removal, a third empty Home
  Assistant instance uses the same temporary configuration and must not restore
  any HausmanHub setup, object, state, runtime data, or local route; the unrelated
  temporary external record must still be unchanged. Only after that absence
  proof, the third instance creates a fresh `read-only` HausmanHub setup with a new
  entry identifier, exactly nine count sensors, unchanged safe diagnostics,
  unchanged external record, and a newly authenticated local route. That fresh
  setup is removed too, its route immediately fails closed, and a fourth empty
  Home Assistant instance must again contain no HausmanHub data while preserving the
  external record.
- Version 0.3.5 clears the current state values of only the nine HausmanHub count
  sensors after a successful HausmanHub unload. A deactivation therefore no longer
  leaves old aggregate values in memory; reactivation restores only the same
  nine counts. It does not alter a device, service, external state, or
  home-control boundary.
- Version 0.3.6 keeps the options screen safe even when old saved settings are
  broken: it shows the neutral `read-only` default instead of an unapproved
  saved mode, without repairing, saving, or otherwise changing that setting.
  It does not add a device, service, home-data path, or home-control boundary.
- Version 0.3.7 fails closed if a damaged saved configuration contains more
  than one HausmanHub entry, including a user-deactivated one. If another saved
  entry appears while HausmanHub is already working, it first closes the active
  summary and ordinarily unloads the existing HausmanHub display before it clears
  only the captured HausmanHub entries' stale count records. The retained local
  route then returns only unavailable, never counts. Both saved records remain
  for manual repair; HausmanHub never chooses, deletes, or activates one
  automatically. A disposable Core lifecycle covers both an enabled pair and
  an enabled plus user-deactivated pair, before and after restart: after
  removal, a remaining enabled entry requires an explicit reload, while a
  remaining disabled entry requires explicit activation before it can recreate
  exactly nine safe counts. If every saved duplicate is already
  user-deactivated, Core does not start HausmanHub at all, so no count state or page
  exists; its disabled registry rows remain until the owner repairs the saved
  pair.
- Version 0.3.8 closes diagnostics on the same boundary. It returns only the
  fixed unavailable status, without calling the local home-summary reader,
  unless exactly one saved HausmanHub entry is currently loaded and safely
  configured. The isolated Core check covers ordinary unload, user
  deactivation before and after restart, removal through a stale object, and
  both malformed duplicate pairs. It patches the temporary diagnostics reader
  to fail if a closed report attempts to observe the home.
- Versions 0.3.8 and 0.3.9 keep diagnostics and the local summary page closed
  if they ever encounter a saved setting that is unsafe. Those defensive
  boundaries remain even though version 0.3.13 now closes the whole HausmanHub
  display immediately after such a saved change.
- Version 0.3.10 also requires the authenticated local page to find exactly
  one saved HausmanHub entry that Home Assistant still reports as loaded. A stale
  in-memory page pointer after an ordinary stop therefore returns only
  unavailable and does not read the nine-count summary. The disposable Core
  check deliberately restores that stale pointer only after the ordinary stop,
  replaces the reader with a failing function, and requires 503 with no count
  keys.
- Version 0.3.11 applies the same complete saved-setting check before the
  options screen chooses its visible default. A damaged main setting or mode
  option therefore shows neutral `read-only`, even if an isolated mode field
  says `shadow`. Opening that screen leaves both saved mappings unchanged;
  the disposable Core check covers every damaged main-setting and option
  variant before it closes the entry for manual repair.
- Version 0.3.12 validates the complete saved configuration before every
  scheduled nine-count refresh. Its coordinator boundary remains a second
  safety net if an unsafe setting somehow reaches a running display.
- Version 0.3.13 uses Home Assistant's standard saved-setting listener after
  the nine sensors and local page are safely registered. A permitted mode
  change reloads only the same HausmanHub entry and takes effect immediately. An
  unsafe saved main setting or mode choice automatically unloads that HausmanHub
  display, clears its nine count states and its HausmanHub-only registry records, and
  rejects setup before any home-summary reader can run. The disposable Core
  check covers all five unsafe main-setting variants and both unsafe
  mode-choice variants, verifies the closed diagnostics and local page, and
  records exactly one reload of the same HausmanHub entry for a normal safe mode
  change. Before each unsafe save, it replaces the sensor, diagnostics, and
  local-page home readers with a failure, so any read during the automatic
  closing interval fails the Core check. A saved entry that failed setup
  remains available for manual repair; because no running HausmanHub remains to
  listen, its owner then explicitly reloads HausmanHub after correcting it.
- The disposable Core lifecycle now changes one safe HausmanHub setting twice:
  `read-only` to `shadow` and back to `read-only`. Each save must reload only
  that one HausmanHub entry exactly once, retain exactly nine aggregate sensors and
  one authenticated GET-only local page, and preserve blocked direct
  execution. Every later stop, reactivation, and restart assertion expects the
  final `read-only` choice. Kimi found no remaining issue in the final review;
  see the [safe mode cycle review
  note](LLM_WIKI/Manual/2026-07-16-kimi-safe-mode-cycle-review.md).
- The disposable Core lifecycle also saves `shadow` while HausmanHub is ordinarily
  stopped but still user-enabled. That save must neither reload HausmanHub nor read
  a home summary, and its nine values, diagnostics, and local page stay
  closed. Only an explicit start restores the same nine sensors and safe
  `shadow` diagnostics. Kimi found no issue; see the [stopped safe-options
  review note](LLM_WIKI/Manual/2026-07-16-kimi-stopped-safe-options-review.md).
- The same disposable lifecycle also saves `read-only` while HausmanHub is
  deliberately disabled by its user. It remains disabled and not loaded: no
  home summary is read, no reload occurs, and its nine values, diagnostics,
  and local page stay closed. Only the user's explicit activation restores the
  same nine sensors with the saved `read-only` mode. Kimi found no issue; see
  the [user-deactivated safe-options review
  note](LLM_WIKI/Manual/2026-07-16-kimi-user-deactivated-safe-options-review.md).
- After a full temporary Home Assistant restart, the same user-disabled HausmanHub
  setup may also save `shadow` without starting itself. It still has no runtime
  data, page, or count values, and it cannot read a home summary or reload
  HausmanHub. Only the user's explicit activation restores the same nine sensors in
  the newly saved `shadow` mode. Kimi found no issue; see the [disabled
  restart safe-options review
  note](LLM_WIKI/Manual/2026-07-16-kimi-disabled-restart-safe-options-review.md).
- A separate disposable check now gives a user-disabled HausmanHub setup a deliberately
  unsafe saved `proxy` option and then attempts explicit user activation. Home
  Assistant rejects the activation, leaves HausmanHub closed with a setup error, and
  keeps direct execution blocked. The broken option remains only for manual
  repair; no home summary is read and no count values, diagnostics, or local
  page become available. The check then removes the temporary setup and proves
  it stays absent after an empty restart. Kimi found no issue; see the [unsafe
  user-activation review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-user-activation-review.md).
- After that rejected proxy-mode activation, a separate disposable repair
  restores the exact safe options. The correction cannot read the home or
  start HausmanHub by itself; only one explicit reload returns the same nine counts,
  fixed diagnostics, and guarded page with direct execution blocked. Kimi
  found no issue; see the [unsafe proxy-option repair review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-proxy-option-repair-review.md).
- The same user-activation and manual-repair safety path separately rejects an
  otherwise safe-looking `shadow` option with an extra unmodelled field. The
  exact safe options still require one explicit reload before the nine-count
  display returns. Kimi found no issue; see the [unsafe extra-field option
  repair review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-extra-field-option-repair-review.md).
- The same disposable activation check now separately uses damaged main data
  whose direct-execution marker says `allowed`. The user activation is still
  rejected before any home read; HausmanHub stays in a setup-error state with no
  counts, diagnostics, local page, service, device, or execution surface. The
  deliberately bad data remains only for manual repair. Kimi found no issue;
  see the [unsafe direct-execution activation review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-direct-execution-activation-review.md).
- A user-disabled HausmanHub entry whose main data lacks the required execution
  block follows the same safe manual-repair path. It cannot start or read the
  home during correction; one explicit reload restores the exact safe data,
  same nine counts, and direct-execution block. Kimi found no issue; see the
  [unsafe missing-execution-block repair review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-missing-execution-block-repair-review.md).
- A user-disabled HausmanHub entry whose main data lacks the required safe mode also
  remains closed. Safe options cannot fill the missing main value; only a
  manual exact repair followed by one explicit reload restores the same nine
  counts. Kimi found no issue; see the [unsafe missing-mode repair review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-missing-mode-repair-review.md).
- A user-disabled HausmanHub entry whose main data has an unknown extra field also
  remains closed. The entry needs a manual exact repair and one explicit
  reload before the same nine counts can return. Kimi found no issue; see the
  [unsafe extra-field data repair review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-extra-field-data-repair-review.md).
- A user-disabled HausmanHub entry whose main data asks for prohibited proxy mode
  also remains closed. It can return only after a manual exact repair and one
  explicit reload, without enabling proxy. Kimi found no issue; see the
  [unsafe proxy-data repair review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-proxy-data-repair-review.md).
- A user-disabled HausmanHub entry whose main data attempts to unblock direct
  execution remains closed even without an intervening Home Assistant restart.
  Manual exact repair and one explicit reload restore only the same nine
  counts with direct execution still blocked. Kimi found no issue; see the
  [unsafe direct-execution repair review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-direct-execution-repair-review.md).
- A user-disabled HausmanHub entry with both an unblocked direct-execution marker
  and a prohibited proxy option remains closed after only one part is repaired.
  It cannot reload or read the home until the remaining part is repaired and
  the owner explicitly reloads HausmanHub. Repeated partial recovery is explicitly
  rejected. Kimi found no issue after an independent review found and closed
  that edge case; see the [unsafe partial-repair review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-partial-repair-review.md).
- The disposable local-page check also confirms that an existing temporary
  local token loses access immediately when its user changes from Home
  Assistant's exact read-only group to the ordinary group. That request must
  return only an access refusal, without any of the nine counts or a home
  summary read. Kimi found no issue; see the [local access-revocation review
  note](LLM_WIKI/Manual/2026-07-16-kimi-local-access-revocation-review.md).
- Version 0.3.14 tells browsers not to store every JSON response generated by
  HausmanHub's local nine-count page: the allowed summary, HausmanHub's access refusal,
  and HausmanHub's unavailable response. It deliberately does not alter `401`,
  `405`, stopping, or other responses created by Home Assistant outside that
  page. Empty Core 2026.6.4 and 2026.7.0 checks confirm the header on the
  approved and closed paths. Kimi found no issue; see the [local no-store
  review note](LLM_WIKI/Manual/2026-07-16-kimi-local-summary-no-store-review.md).
- On 2026-07-17 the owner explicitly authorized a push. The accumulated
  0.3.15–0.3.18 work was committed as `a032303` and pushed to `origin/main`.
  This was a source push only: no tag, GitHub Release, HACS release
  publication, deployment, or live-home change was performed.
- Work included in version 0.3.18 closes the unspecified local
  origins 0.0.0.0, ::, and IPv4-mapped ::ffff:0.0.0.0 before any nine-count
  read. The same nine approved rows now have only fixed ordinary visual icons;
  the disposable Core check proves the icon for each row without adding data or
  an action. It also proves in disposable Core 2026.6.4 and 2026.7.0 that the
  guarded page accepts only GET: HEAD, POST, PUT, PATCH, DELETE, and TRACE
  return 405; CONNECT does not reach the route and returns 404; OPTIONS returns
  Home Assistant's safe 403 before any home read. Its one fixed address has no
  alternate URL: even the same path with a trailing slash or added query data
  is a closed 404 before a home read and without count names. The real route
  registration must contain GET plus only that safe, closed Home Assistant
  OPTIONS response. The local Core check also requires every rejected method
  response, guest response, and administrator response to omit all nine count
  names. The combined working tree passed 139 local tests and both disposable
  Core checks. The later mixed-diff Kimi review cycle is recorded with version
  0.3.16 below.
- Work included in version 0.3.18 adds only the exact boolean
  `local_summary_enabled` option. It lets the owner close or restore the
  already-approved optional local nine-count page without adding a URL, data,
  command, service, device, proxy, or execution right. With the page closed,
  the existing nine HausmanHub count rows and fixed diagnostics intentionally remain
  available and may refresh the same approved aggregates; a request to the
  closed old page itself fails before it can read them. After a full temporary
  Home Assistant restart while closed, neither HausmanHub page runtime data nor its
  route is registered. Strings, numbers, and other truth-like values are
  rejected. The disposable lifecycle now also changes this boolean while HausmanHub
  is ordinarily stopped, user-disabled, and user-disabled after a restart. Each
  save must leave HausmanHub `NOT_LOADED`, record no reload, and fail immediately if
  any HausmanHub home-summary reader runs. Only the following explicit setup or user
  activation may apply the saved page choice; the after-restart case performs a
  real `True` to `False` change and then keeps the page runtime and route absent
  through activation, ordinary unload, another restart, and removal. The final
  local diff passed 139 fast tests, the complete local release check, and
  disposable Core 2026.6.4 and 2026.7.0 checks. A first temporary fallback
  review found two test-only weaknesses: broad source-string assertions and no
  after-restart boolean change. Both were corrected, and the final independent
  OpenCode fallback review found no remaining issue; see the [inactive local
  page options review](LLM_WIKI/Manual/2026-07-17-opencode-inactive-local-page-options-review.md).
  After the provider quota renewed, Kimi reviewed the complete mixed diff and
  raised one potential frontend-serialization risk for the strict boolean
  selector. Both supported Core versions already serialize the inherited type
  as the native `boolean`; the contract is now explicit, the unit adapter test
  guards it, and the disposable Core harness checks the real serialized form.
  Both Core checks, all 139 fast tests, and the complete local release check
  passed again. The Kimi follow-up found no remaining issue; see the [0.3.15
  and 0.3.16 Kimi review cycle](LLM_WIKI/Manual/2026-07-17-kimi-v0-3-15-v0-3-16-review.md).
  These local reviews do not themselves authorize a commit, push, release,
  deployment, or publication.
- Work included in version 0.3.18 adds only one fixed refresh choice
  for the same nine diagnostic count sensors: the established `5m` default or
  the slower `15m` and `30m` choices. Exact validation rejects faster,
  arbitrary, numeric, and missing submitted values. Old entries whose options
  do not contain the new field still use `5m`; saved entry data is unchanged.
  The one coordinator shared by all nine rows receives the selected interval.
  No new count, data, entity, route, service, device, command, proxy, execution
  path, or authority is added, and the optional authenticated local GET page
  remains immediate per request. The disposable lifecycle covers active
  changes, a real legacy empty-options restart, ordinary unload/restart,
  stopped and user-disabled saves without reload or home reads, and later
  explicit activation. Fast tests, the complete local release check, and Core
  2026.6.4/2026.7.0 results are recorded with the [0.3.17 Kimi review
  cycle](LLM_WIKI/Manual/2026-07-17-kimi-v0-3-17-summary-interval-review.md).
  No review authorizes a commit, push, release, deployment, publication, or
  live-home change.
- Version 0.3.18 adds only the effective validated
  HausmanHub settings to the existing redacted diagnostics `entry_summary`: safe
  mode, the optional local-page boolean, and the exact `5m`, `15m`, or `30m`
  nine-count refresh choice. It never copies raw entry data or options. Legacy
  empty options report the safe enabled-page and `5m` defaults. Unsafe,
  inactive, removed, and ambiguous setups still return only the fixed
  unavailable response before any home-summary read. No count, home datum,
  entity, route, service, device, command, proxy, execution path, automatic
  repair, or authority is added. All 144 fast tests, the complete local
  release check, and disposable Core 2026.6.4/2026.7.0 checks passed. The
  implementation boundary and verification record are in the [0.3.18 safe
  settings diagnostics note](LLM_WIKI/Manual/2026-07-17-hausmanhub-v0-3-18-safe-settings-diagnostics.md).
  A bounded Kimi `k2p7` review of the 0.3.18 delta returned `NO FINDINGS` after
  its completed child session was resumed with the Kimi model explicitly
  pinned. The review itself did not authorize a commit, push, release,
  deployment, publication, or live-home change; the later source push was
  explicitly authorized by the owner.
- The same accumulated version now accepts the local nine-count page only from
  loopback, RFC 1918 IPv4, unique-local IPv6, or an IPv4-mapped form of the
  same approved IPv4 range. Test, link-local, carrier-grade, public, and other
  special addresses fail closed before the summary reader runs. The local fast
  and disposable Core checks cover both exact range boundaries and those
  refusals without using a live home.
- The same local page now also fails closed when its approved nine-count reader
  unexpectedly raises: it returns only the fixed unavailable response, with no
  partial count or error detail. Fast and disposable Core checks use a failing
  temporary reader to prove this without accessing a live home.
- That unsafe direct-execution activation check also has a separate full
  temporary restart between saving the bad data and the user's activation
  attempt. The saved setup remains user-disabled and unloaded with no runtime
  data or local page after the restart; activation is still rejected and the
  damaged data stays for manual repair. Kimi found no issue; see the [unsafe
  direct-execution restart review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-direct-execution-restart-review.md).
- After that rejected activation, a separate temporary recovery check restores
  the exact original safe data and explicitly reloads HausmanHub. It returns only
  the same nine safe counts, fixed diagnostics, and guarded local page with
  direct execution blocked; it creates no service or device. The saved repair
  itself cannot read the home or start HausmanHub before the explicit reload. Kimi
  found no issue; see the [unsafe direct-execution recovery review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-direct-execution-recovery-review.md).
- The same disposable recovery then deliberately receives the unsafe
  direct-execution marker once more. The restored saved-setting guard closes
  HausmanHub again before any home read: it clears all nine counts, diagnostics, and
  the local page, while retaining the bad saved value for a future manual
  repair. Kimi found no issue; see the [unsafe direct-execution repeat-closure
  review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-direct-execution-repeat-closure-review.md).
- A second exact safe manual repair after that repeat closure also remains
  closed until one separate explicit reload. It cannot read the home or
  restart HausmanHub while the saved value is being corrected; the explicit reload
  restores only the same nine counts and safe display. Kimi found no issue;
  see the [unsafe direct-execution repeat-repair review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-direct-execution-repeat-repair-review.md).
- A full empty restart after that second repair preserves only the exact safe
  HausmanHub entry and its same nine counts, fixed diagnostics, and guarded page.
  The direct-execution block remains saved and no control surface appears.
  Kimi found no issue; see the [unsafe direct-execution repeat-repair restart
  review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-direct-execution-repeat-repair-restart-review.md).
- After that restart, another unsafe direct-execution marker still causes an
  immediate closure before any home read. The restarted guard clears all nine
  counts, diagnostics, and the local page while retaining the bad saved value
  only for manual repair. Kimi found no issue; see the [unsafe direct-
  execution repeat-repair restart-closure review
  note](LLM_WIKI/Manual/2026-07-16-kimi-unsafe-direct-execution-repeat-repair-restart-closure-review.md).
- Kimi independently reviewed the automatic saved-setting reload and closure.
  Its first review requested an explicit no-read check during the closing
  interval; the follow-up review found no remaining issues. See the
  [automatic settings reload review
  note](LLM_WIKI/Manual/2026-07-16-kimi-automatic-settings-reload-review.md).
- Kimi independently reviewed the live count-refresh closure with no
  findings. See the [live count-refresh review
  note](LLM_WIKI/Manual/2026-07-15-kimi-live-summary-refresh-review.md).
- Kimi independently reviewed the closed diagnostics change with no findings.
  See the [closed diagnostics review
  note](LLM_WIKI/Manual/2026-07-15-kimi-closed-diagnostics-review.md).
- Kimi independently reviewed this before-reload diagnostics closure with no
  findings. See the [invalid saved-settings diagnostics review
  note](LLM_WIKI/Manual/2026-07-15-kimi-invalid-settings-diagnostics-review.md).
- Kimi independently reviewed the local summary before-reload closure with no
  findings. See the [local summary unsafe-settings review
  note](LLM_WIKI/Manual/2026-07-15-kimi-local-summary-unsafe-settings-review.md).
- Kimi independently reviewed the stale local-summary pointer closure with no
  findings. See the [stale local-summary pointer review
  note](LLM_WIKI/Manual/2026-07-15-kimi-stale-local-summary-pointer-review.md).
- Kimi independently reviewed the options-screen closure for damaged saved
  settings with no findings. It confirmed that the selected default now uses
  the complete saved configuration, keeps manual repair possible, and neither
  writes nor expands HausmanHub's read-only/shadow boundary. See the [damaged
  options-screen review
  note](LLM_WIKI/Manual/2026-07-15-kimi-damaged-options-screen-review.md).
- Kimi independently reviewed the final live and restart duplicate-entry
  closure with no findings. See the [live duplicate fail-closed review
  note](LLM_WIKI/Manual/2026-07-15-kimi-live-duplicate-fail-closed-review.md).
- The local HausmanHub adapter check also covers a failed ordinary unload with one
  saved HausmanHub setup. In that case it keeps the current safe display intact
  rather than partly clearing its values or local page while Home Assistant
  still has HausmanHub loaded. This is separate from the damaged multi-entry case,
  which must close the display.
- The disposable Core lifecycle separately unloads and starts one safe,
  still-user-enabled HausmanHub setup. In the gap, its saved setup and nine enabled
  records remain but all count states and the guarded page fail closed; starting
  the same setup restores only the same nine safe counts, diagnostics, and
  GET-only page. This runs in a temporary empty configuration only.
- The same disposable lifecycle also ordinary-unloads that still-user-enabled
  setup, fully stops the temporary Home Assistant, then starts a new empty
  instance. The setup must auto-load with the same data, safe mode, nine count
  sensors, fixed diagnostics, and authenticated GET-only page. It remains
  user-enabled, direct execution stays blocked, and it creates no device or
  service. This is separate from a user's deliberate deactivation, which must
  remain inactive across a restart.
- After that automatic recovery, the same temporary user-enabled setup is
  ordinarily stopped once more and removed before it starts again. While
  stopped, its saved settings and nine enabled registry records must remain
  intact but all values, diagnostics, and the guarded page must stay closed.
  Removal must clear its records and values, keep the temporary external
  similar-name record unchanged, and remain absent after the following empty
  restart.
- A later temporary reinstallation is also ordinarily stopped before the user
  deactivates it. The stop retains its safe settings and nine enabled but
  value-free records; deactivation marks those same records disabled and closes
  diagnostics and the local page. The same setup is then immediately
  reactivated before a restart: it must restore the unchanged settings, the
  same nine safe counts, diagnostics, and authenticated GET-only page without
  changing the external temporary record. It is deactivated once more before
  the existing restart-and-removal check.
- Kimi independently reviewed that ordinary-stop/deactivate/reactivate path
  with no findings. See the [ordinary-stop reactivation review
  note](LLM_WIKI/Manual/2026-07-15-kimi-ordinary-stop-reactivation-review.md).
- While that user-enabled setup is ordinarily stopped before its temporary
  restart, the same lifecycle tries to add HausmanHub again. Home Assistant must
  refuse the duplicate, retain exactly one still-enabled saved setup and its
  nine unloaded count records, and keep values and the guarded page closed.
  It creates no extra sensor, device, service, or control path.
- Both HausmanHub setup forms now have an isolated input-boundary check: even if a
  form receives invented extra fields beside a safe mode, it persists only the
  fixed approved data shape. This is local test coverage only and adds no
  runtime authority.
- Before its first temporary restart, the same isolated lifecycle check also
  uses Home Assistant's ordinary user deactivation and reactivation path. While
  deactivated, the saved HausmanHub setup is not loaded, its nine registry entries
  are marked disabled by that setup, their temporary state values are absent,
  and the guarded local page returns only an unavailable response with no count
  keys. Reactivation must restore the same nine enabled count sensors, safe
  diagnostics, and authenticated GET-only page, still with no device, service,
  proxy, or execution capability.
- One later temporary reinstallation is deliberately deactivated, persisted
  through an empty restart, and then removed. Its nine HausmanHub registry records,
  temporary states, and guarded local page must stay cleared through the
  following empty restart, while the unrelated temporary external record is
  preserved.
- The first safe setup is also deactivated immediately before a temporary
  restart that replaces only the temporary HausmanHub copy. It must stay disabled and
  not restore runtime data, count states, or the guarded page on its own.
  Explicit reactivation must restore only its existing nine safe count sensors,
  diagnostics, and authenticated GET-only page.
- While that saved setup remains user-deactivated after the temporary restart,
  the lifecycle tries to add HausmanHub again. Home Assistant must refuse the
  duplicate, retain exactly one disabled saved setup and its nine disabled
  records, and keep runtime data, count values, and the guarded page closed
  until the owner explicitly activates the same setup.
- The same disposable lifecycle now counts every local HausmanHub page instead of
  merely finding the first one. An active safe setup must have exactly one
  guarded page; after an in-process deactivation or removal that one retained
  page must fail closed without counts; after a full temporary restart while
  disabled or removed, no such page may exist.
- Version 0.3.4 requires both fixed fields in saved HausmanHub main data. Even a
  safe `shadow` mode in the separate options cannot fill in a missing main
  mode, so an incomplete saved setup stays closed until its exact data is
  restored. This does not add any home-control feature.
- Version 0.3.3 keeps a bad saved HausmanHub setup closed. If its saved data violates
  the fixed safety contract, HausmanHub rejects a reload and removes only its own
  restored count states and stale HausmanHub records, both after startup and during a
  running-system reload. Its delayed startup cleanup is explicitly scheduled
  on Home Assistant's main loop, and the local test fake rejects an unmarked
  startup callback. It does not alter devices, services, other entities,
  Climate, or Automation.
- The same disposable Core lifecycle now checks five deliberately invalid main
  saved settings separately: an unsafe mode, a false unblocked-execution
  marker, a missing required execution block, a missing required mode, and an
  otherwise safe main setting with one extra synthetic field. Each must close
  through reload and restart, recover only after the exact safe data is
  restored, and keep the unrelated temporary record unchanged.
- The same disposable lifecycle now corrects only its own deliberately bad
  saved data back to the exact original safe data, then starts one more empty
  Home Assistant while the corrected HausmanHub setup remains installed. That restart
  must restore the same nine count-sensor names, fixed diagnostics, and the
  authenticated GET-only page with no devices or services. Only then is the
  temporary HausmanHub setup removed and checked through a final empty restart.
- The same disposable lifecycle separately covers two bad saved mode choices
  in HausmanHub options: a temporary `proxy` choice and an otherwise safe `shadow`
  choice with one extra synthetic field. Each rejects reload and remains closed
  after restart; restoring the exact original safe choice must preserve the
  same nine count-sensor names, safe diagnostics, and GET-only page through its
  own empty restart before removal. The check keeps no data beyond its
  temporary fixtures.
- Synthetic Common-contract fixtures, static validators, synthetic shadow
  evidence, and redacted diagnostics/repairs fixtures are present. They use
  Python's standard library and local JSON only.
- Version 0.3.1 retains one explicitly approved local read-only observation:
  `home_summary` in diagnostics. It contains exactly nine aggregate counts:
  areas, devices, entities, sensors, and available/unavailable/unknown/not
  reported/disabled entities. Disabled entries are counted separately before
  the adapter reads a state; `not_reported` therefore means an enabled entry
  has no current state. The adapter reduces each permitted local fact
  immediately to a category; it exports no name, identifier, reading, history,
  address, secret, or raw state. Version 0.3.1 shows the same fixed payload as
  exactly nine HausmanHub diagnostic number sensors. They share one redacted local
  snapshot, exclude HausmanHub's own sensors from the house totals, create no HausmanHub
  device or service, and do not call Home Assistant services.
- The owner explicitly approved a local count-only access path on 2026-07-14.
  It may expose the same fixed nine counts only after Home Assistant
  authentication, an exact built-in read-only user group, and a local-network
  origin check. It must have GET only, no outgoing connection, no token
  storage, no raw data, and no external or device-control capability. See the
  [local-access decision](LLM_WIKI/Manual/2026-07-14-local-read-only-access-decision.md).
- The Russian guides now make clear that ordinary HausmanHub counts and diagnostics
  need no extra user. The optional local account belongs only to a viewer;
  HausmanHub never receives or stores its password, key, or Home Assistant
  connection address, and only checks an incoming request origin momentarily.
  Kimi reviewed that clarification with no findings. See the [local viewer
  wording review](LLM_WIKI/Manual/2026-07-16-kimi-local-viewer-clarity-review.md).
- On 2026-07-14, an owner-performed local v0.1.2 diagnostics check confirmed
  the exact nine-count shape and all required safe-mode markers. Its aggregate
  values and the diagnostics file were inspected only and were not copied into
  this repository or this context.
- On 2026-07-14, the owner separately approved Codex direct local Home
  Assistant observation through a dedicated local non-administrator account.
  This is outside HausmanHub's runtime boundary: Codex sends GET only, keeps the
  credential outside GitHub and chat, and does not retain raw home data. The
  access account is not a technical read-only role, so the no-command rule is
  an operating constraint. See the [direct local observation decision](LLM_WIKI/Manual/2026-07-14-direct-local-read-observation-decision.md).

## Durable decisions

- HausmanHub is a separate repository and has no authority over the existing
  HausmanHub runtime.
- Initial modes are read-only and shadow only.
- Proxy requires separate owner approval and rollback notes.
- On 2026-07-17 the owner asked development to move toward working control.
  This authorizes the local 0.4.0 single-`input_boolean` canary only. It does
  not authorize a live deployment, a physical device, another service domain,
  multiple targets, Climate/Automation/Common ownership, proxy, or Node-RED.
- General physical-device execution remains blocked pending proven shadow
  parity, a device-specific canary/stop/rollback/authority decision, and owner
  signoff. The virtual-helper canary is not a physical authority transfer.
- Do not commit secrets, live identifiers, flow snapshots, device-specific
  service paths, physical command payloads, or deployment scripts.
- Every code change requires a final Codex self-review. Codex reviews the final
  current diff before commit, push, release, deployment, or publication. The
  self-review includes the staged diff, relevant tests, and the full local
  release gate. Review findings must be addressed or explicitly documented.
- The owner approved a public GitHub repository on 2026-07-14 because HACS
  cannot use a private GitHub repository. This permits only the minimal root
  `hacs.json` and manual HACS custom-repository installation. It does not
  approve inclusion in the public HACS catalog, live testing, proxy, or direct
  execution.
- The owner also explicitly approved local, read-only HausmanHub access to home
  data on 2026-07-14. That approval is limited to the v0.2.0 aggregate
  `home_summary`, including a separate disabled-entry count and the guarded
  local count-only path; it does not grant remote assistant access, proxy,
  direct execution,
  Common/Climate/Automation ownership, or permission to save live home data
  in this repository.
- The owner later approved a separate, local Codex read-observation path after
  the Home Assistant UI did not offer the exact `system-read-only` role. It
  does not relax HausmanHub's own strict route guard or grant HausmanHub any device
  authority; see the direct local observation decision above.
- On 2026-07-15 the owner explicitly approved showing only the existing nine
  aggregate HausmanHub counts in Home Assistant. This authorizes exactly nine
  diagnostic number sensors, not devices, controls, new home data, proxy, or
  execution. The decision is recorded in
  [the summary-display decision](docs/read-only-home-summary-display-decision.md).
- Version `0.3.1` has a public GitHub release at
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v0.3.1. It keeps the
  approved nine diagnostic count sensors only. New installations use a HausmanHub
  prefix for their internal names; an existing Home Assistant registry keeps
  the same names through its unchanged permanent keys.
- On 2026-07-15, after the owner updated and restarted Home Assistant, a direct
  local Codex check used only GET requests and HTTP status codes. It confirmed
  that Home Assistant responded, HausmanHub's guarded read-only path was active, and
  all nine approved HausmanHub count sensors were present. No count value, raw home
  payload, name, identifier, credential, or other home data was printed or
  stored.
- A local repository safety check now scans Git-tracked files or exactly the
  staged files before publication. It reads file blobs only from Git's index,
  so it never follows a working-tree symbolic link outside the repository. It
  detects common runtime/backup file names and credential-shaped data, but is
  an additional guard rather than a substitute for a human check. It has no
  Home Assistant, Node-RED, device, or network access.
- The local publication command also verifies the complete manual-HACS package
  from Git-index blobs and modes: approved metadata and manifest, entry files,
  both translations, the local icon, license, and release notes. It rejects a
  missing or linked required file, unapproved metadata or manifest field, bad
  JSON, mismatched translation shape, invalid icon, or missing version note.
  It remains local-only and does not change the HausmanHub runtime or home authority.
- Kimi independently reviewed the local HACS-package check with no findings.
  See the [HACS-package check review
  note](LLM_WIKI/Manual/2026-07-15-kimi-hacs-package-check-review.md).
- The supported baseline was lowered to Core 2026.6.4 after the isolated
  lifecycle check passed on that exact version. See the [2026.6.4 compatibility
  note](LLM_WIKI/Manual/2026-07-14-core-2026-6-4-compatibility.md).
- Kimi reviewed the 2026.6.4 baseline change. Its only non-blocking note was
  a prompt wording mismatch about a test rename; the final code has no related
  defect. See the [2026.6.4 baseline review
  note](LLM_WIKI/Manual/2026-07-14-kimi-core-2026-6-baseline-review.md).
- Kimi reviewed the local brand icon change with no findings. See the [brand
  icon review note](LLM_WIKI/Manual/2026-07-14-kimi-local-brand-icon-review.md).
- Kimi reviewed the isolated diagnostics smoke-check extension and the manual
  safe-check guide with no findings. See the [safe Home Assistant check review
  note](LLM_WIKI/Manual/2026-07-14-kimi-safe-home-assistant-check-review.md).
- Kimi reviewed the safe-mode language change after one review-fix pass and
  found no final issues. See the [safe-mode language review
  note](LLM_WIKI/Manual/2026-07-14-kimi-safe-mode-language-review.md).
- Kimi reviewed the initial HACS metadata change with no findings before the
  private-HACS limitation was discovered. Its historical review note is
  [here](LLM_WIKI/Manual/2026-07-14-kimi-private-hacs-metadata-review.md).
- Kimi reviewed the correction for the public HACS custom-repository path. It
  found three outdated phrases, which were corrected; the final review had no
  findings. See the [public HACS correction review
  note](LLM_WIKI/Manual/2026-07-14-kimi-public-hacs-correction-review.md).
- Kimi baseline/review-fix pass found no blocking safety or correctness issue
  in the static harness. The follow-up tightened mismatch validation, made
  negative tests assert their intended reason, and covered the CLI failure path.
- Kimi reviewed the approved read-only skeleton. It identified no blocking
  safety issue; a type-hint compatibility question was checked against the
  official Home Assistant 2026.7.0 source and is compatible. See the detailed
  [skeleton review note](LLM_WIKI/Manual/2026-07-13-kimi-read-only-skeleton-review.md).
- Kimi reviewed the isolated config/options-flow adapter test twice: first it
  identified two test-isolation gaps, then confirmed the corrections with no
  remaining findings. See the [adapter review note](LLM_WIKI/Manual/2026-07-13-kimi-config-flow-adapter-review.md).
- Kimi reviewed the isolated real-Core smoke check, then confirmed its
  remediations with no remaining findings. See the [Core smoke-check review
  note](LLM_WIKI/Manual/2026-07-13-kimi-home-assistant-core-smoke-check-review.md).
- Kimi reviewed the expanded real-Core lifecycle check, including both safe
  modes, rejected unsafe options, reload, and removal, with no remaining
  findings. See the [expanded Core lifecycle review
  note](LLM_WIKI/Manual/2026-07-13-kimi-expanded-core-lifecycle-review.md).
- Kimi reviewed the persisted-config exact-key boundary tests and confirmed the
  final version with no remaining findings. See the [persisted-config review
  note](LLM_WIKI/Manual/2026-07-13-kimi-persisted-config-boundary-review.md).
- Kimi reviewed the diagnostics allow-list structure test with no findings. See
  the [diagnostics review note](LLM_WIKI/Manual/2026-07-13-kimi-diagnostics-allow-list-review.md).
- Kimi reviewed the fixed manual-repair category contract with no findings. See
  the [repairs review note](LLM_WIKI/Manual/2026-07-13-kimi-manual-repairs-contract-review.md).
- Kimi-backed review of the v0.1.1 aggregate home summary first found an
  in-memory full-state map; that map was removed. The final independent review
  found no blocking or non-blocking issues. See the [aggregate-summary review
  note](LLM_WIKI/Manual/2026-07-14-kimi-read-only-home-summary-review.md).
- Kimi-backed final review of v0.1.2 found no blockers, important issues, or
  minor issues. It confirmed the separate disabled-entry count, the
  state-read order, the strict nine-count boundary, and the updated context.
  See the same [aggregate-summary review
  note](LLM_WIKI/Manual/2026-07-14-kimi-read-only-home-summary-review.md).
- Kimi reviewed the guarded local nine-count access path with no findings. It
  confirmed the fixed response shape, exact read-only role, local-source and
  GET-only guards, fail-closed behaviour, and clean architecture boundary. See
  the [local access review note](LLM_WIKI/Manual/2026-07-14-kimi-local-summary-access-review.md).
- Kimi's first review of the repository safety check found an unsafe
  working-tree symbolic-link read and an over-broad flow-file name check. Both
  were corrected and covered by tests. The final direct Kimi review found no
  remaining issues; see the [repository safety review
  note](LLM_WIKI/Manual/2026-07-14-kimi-repository-safety-check-review.md).
- Kimi reviewed the isolated safe-update check. It found no issues: the check
  restarts only a disposable empty Home Assistant after replacing the local
  test copy, then requires the safe choice, the execution block, and the
  absence of HausmanHub objects to survive. See the [safe-update review
  note](LLM_WIKI/Manual/2026-07-14-kimi-safe-update-persistence-review.md).
- Kimi reviewed the one-command local publication check with no findings. It
  confirmed the command runs only local tests, synthetic fixtures, and the
  existing Git-file safety checks, stopping at the first failed check. See the
  [local publication-check review
  note](LLM_WIKI/Manual/2026-07-14-kimi-local-release-check-review.md).
- Kimi reviewed the added command-list guard with no findings. It confirmed
  that the local publication command's fixed list cannot acquire a network
  address, Home Assistant, `curl`, or `wget` without making the local test
  fail. See the [no-home-target review
  note](LLM_WIKI/Manual/2026-07-14-kimi-local-release-no-home-target-review.md).
- Kimi re-reviewed the manifest/version-history test after its first review
  session did not return a final report. The final review found no issues; see
  the [version-history review
  note](LLM_WIKI/Manual/2026-07-14-kimi-version-history-review.md).
- Kimi reviewed the GitHub local-quality workflow before publication and found
  no safety or boundary issues. See the [GitHub local-quality review
  note](LLM_WIKI/Manual/2026-07-14-kimi-github-local-quality-review.md).
- Kimi reviewed the staged-release-version guard after a first independent
  review identified an omitted file-type change. The guard and its test were
  corrected; Kimi's final review found no remaining issues. See the
  [staged-release-version review
  note](LLM_WIKI/Manual/2026-07-15-kimi-staged-release-version-review.md).
- Kimi reviewed the v0.3.0 nine-count display. Its short fallback review raised
  five questions; checking the complete staged code showed that the first,
  second, third, and fifth came from the deliberately shortened excerpt, while
  the fourth is the intended no-change refresh behavior. No capability or data
  boundary was expanded. See the [nine-count display review
  note](LLM_WIKI/Manual/2026-07-15-kimi-nine-count-display-review.md).
- Kimi reviewed the v0.3.1 protected-name and upgrade-preservation change. The
  first review suggested an explicit legacy-update check; it was added. The
  final review found no blocking or non-blocking issue. See the
  [v0.3.1 review note](LLM_WIKI/Manual/2026-07-15-kimi-v0-3-1-review.md).
- Kimi reviewed the isolated occupied-name check twice. The first pass noted
  that the test should not depend on Home Assistant's exact suffix choice;
  the check now requires only a different protected HausmanHub name and exact names
  for the other eight sensors. The final review found no issues. See the
  [occupied-name review note](LLM_WIKI/Manual/2026-07-15-kimi-occupied-name-check-review.md).
- Kimi reviewed the no-device runtime check with no findings. It confirmed
  that the isolated check requires both an empty HausmanHub device list and no
  device attachment for each of the nine sensors. See the [no-device review
  note](LLM_WIKI/Manual/2026-07-15-kimi-no-device-check-review.md).
- Kimi reviewed the real-Core one-setup check with no findings. It confirmed
  that `single_instance_allowed` is the Home Assistant result for a second
  attempt when the manifest permits only one HausmanHub setup. See the [one-setup
  review note](LLM_WIKI/Manual/2026-07-15-kimi-one-setup-check-review.md).
- Kimi reviewed the isolated external-name cleanup check with no findings. It
  confirmed that after HausmanHub removal, the temporary external entry still has the
  same identity and no HausmanHub or device ownership. See the [external-cleanup
  review note](LLM_WIKI/Manual/2026-07-15-kimi-external-collision-cleanup-review.md).
- Kimi reviewed the isolated repeat-install check with no findings. It
  confirmed that the second safe setup creates the same nine count sensors,
  keeps the external entry unchanged, and removes cleanly. See the
  [repeat-install review
  note](LLM_WIKI/Manual/2026-07-15-kimi-repeat-install-after-cleanup-review.md).
- Kimi reviewed the isolated local-summary closure check with no findings. It
  confirmed that a retained route has no active entry after every removal and
  returns an unavailable response without count data to a temporary local
  read-only user. See the [local-summary closure review
  note](LLM_WIKI/Manual/2026-07-15-kimi-local-summary-closed-after-removal-review.md).
- Kimi reviewed the isolated state-cleanup check with no findings. It confirmed
  that the test remembers only HausmanHub's temporary internal state names before
  removal, then rejects any state left afterward without reading or printing a
  count value. See the [state-cleanup review
  note](LLM_WIKI/Manual/2026-07-15-kimi-state-cleanup-after-removal-review.md).
- Kimi reviewed the isolated final-restart cleanup check with no findings. It
  confirmed that a third empty Home Assistant instance keeps HausmanHub absent after
  removal while preserving the unrelated external record, without HTTP or home
  access. See the [final-restart cleanup review
  note](LLM_WIKI/Manual/2026-07-15-kimi-final-restart-cleanup-review.md).
- Kimi reviewed the isolated fresh-reinstall check with no findings. It
  confirmed that the third instance proves absence before creating a new
  read-only setup, keeps the external record unchanged, and reuses only a
  distinct temporary user name for the guarded local route. See the
  [fresh-reinstall review
  note](LLM_WIKI/Manual/2026-07-15-kimi-fresh-reinstall-after-cleanup-review.md).
- Kimi reviewed the isolated closed fresh-reinstall cycle with no findings. It
  confirmed that the fresh setup is removed, its route fails closed without
  count data, and a fourth empty Home Assistant instance remains HausmanHub-free
  while the external record survives. See the [closed-cycle review
  note](LLM_WIKI/Manual/2026-07-15-kimi-closed-fresh-reinstall-cycle-review.md).
- Kimi reviewed the ordinary deactivation/reactivation lifecycle check with no
  findings. It confirmed that deactivation marks only HausmanHub's nine temporary
  count entries disabled and closes the guarded page, while reactivation
  restores only the same safe observation surface. See the [deactivation
  review note](LLM_WIKI/Manual/2026-07-15-kimi-deactivation-reactivation-review.md).
- Kimi reviewed the removal of a deactivated temporary HausmanHub setup with no
  findings. It confirmed that the test closes the page before removal, clears
  only HausmanHub's own temporary records, and preserves the unrelated external
  record. See the [deactivated-removal review
  note](LLM_WIKI/Manual/2026-07-15-kimi-deactivated-removal-review.md).
- Kimi reviewed the persisted-deactivation check with no findings. It confirmed
  that a temporary restart/update cannot silently reactivate HausmanHub or restore
  its page or state values, while explicit reactivation remains limited to the
  same nine safe counts. See the [deactivation-persistence review
  note](LLM_WIKI/Manual/2026-07-15-kimi-deactivation-persistence-review.md).
- Kimi reviewed the local-page uniqueness check with no findings. It confirmed
  that an active HausmanHub requires exactly one page, while the retained in-process
  page remains safely unavailable after deactivation or removal and no page
  returns after a full empty restart. See the [local-page uniqueness review
  note](LLM_WIKI/Manual/2026-07-15-kimi-local-summary-route-uniqueness-review.md).
- Kimi reviewed the invalid-saved-settings fail-closed fix with no findings. It
  confirmed that HausmanHub clears only its own restored state placeholders after
  startup, immediately clears them on a reload, and does not touch a device,
  service, external entity, or home-control boundary. See the [invalid-settings
  review note](LLM_WIKI/Manual/2026-07-15-kimi-invalid-persisted-settings-review.md).
- The v0.3.3 Kimi review cycle first found stale HausmanHub registry records and a
  startup callback that needed the Home Assistant loop-safety marker. Both
  were corrected, with a local test that rejects an unmarked callback. The
  final focused Kimi review found no issues; see the [invalid-record cleanup
  review note](LLM_WIKI/Manual/2026-07-15-kimi-invalid-record-cleanup-review.md).
- Kimi reviewed the isolated lifecycle for an extra saved main-data field with
  no findings. It confirmed the third deliberately bad main setting closes
  through reload and restart, restores only the same nine counts after exact
  correction, and never touches the external temporary record. See the
  [extra-main-data review note](LLM_WIKI/Manual/2026-07-15-kimi-extra-saved-main-data-review.md).
- Kimi reviewed the lifecycle for a saved main setting with its mandatory
  execution block missing, with no findings. It confirmed the fourth bad main
  setting closes through reload and restart, restores only the same nine counts
  after exact correction, and never touches the external temporary record. See
  the [missing-execution-block review
  note](LLM_WIKI/Manual/2026-07-15-kimi-missing-execution-block-review.md).
- Kimi reviewed the v0.3.4 correction for a missing main mode with a safe
  `shadow` option, with no findings. It confirmed that complete main saved data
  is now required, empty options still work for a complete setting, and the
  rejected setup cannot create a page, sensor, device, service, or execution
  path. See the [missing-main-mode review
  note](LLM_WIKI/Manual/2026-07-15-kimi-missing-main-mode-review.md).
- Kimi reviewed the v0.3.5 cleanup of HausmanHub state values after a successful
  unload, with no findings. It confirmed that HausmanHub removes only its own nine
  displayed values, keeps its registry records, preserves an external state,
  and restores only the same nine counts after reactivation. See the
  [unload-state-cleanup review
  note](LLM_WIKI/Manual/2026-07-15-kimi-unload-state-cleanup-review.md).
- Kimi reviewed the test-only failed-unload case with no findings. It confirmed
  that a failed platform unload leaves the current safe display intact rather
  than partly clearing state or the local page, with no new home access or
  control. See the [failed-unload review
  note](LLM_WIKI/Manual/2026-07-15-kimi-failed-unload-review.md).
- Kimi reviewed the separate ordinary Core unload/setup check with no findings.
  It confirmed that it keeps the user-enabled lifecycle distinct from user
  deactivation, preserves the fixed safety boundary, and uses a temporary
  empty Home Assistant only. See the [ordinary unload/setup review
  note](LLM_WIKI/Manual/2026-07-15-kimi-ordinary-unload-setup-review.md).
- Kimi reviewed the ordinary-unload full-restart recovery check with no
  findings. It confirmed that an enabled HausmanHub setup auto-loads after the next
  empty Home Assistant starts, while preserving exactly the same nine counts,
  fixed diagnostics, GET-only local page, and all control prohibitions. See the
  [ordinary unload/restart review
  note](LLM_WIKI/Manual/2026-07-15-kimi-ordinary-unload-restart-review.md).
- Kimi reviewed removal of an ordinarily stopped, still-user-enabled HausmanHub
  setup with no findings. It confirmed that the temporary test keeps the same
  nine-count and no-control boundary, closes both read paths before and after
  removal, preserves an unrelated similar-name record, and uses no real home.
  See the [ordinary stopped-removal review
  note](LLM_WIKI/Manual/2026-07-15-kimi-stopped-removal-review.md).
- Kimi reviewed user deactivation after an ordinary HausmanHub stop with no findings.
  It confirmed that the disposable lifecycle distinguishes this state from an
  active deactivation, preserves the nine-count/no-control boundary, and
  carries the disabled state through restart and removal. See the [ordinary
  stopped-deactivation review
  note](LLM_WIKI/Manual/2026-07-15-kimi-stopped-deactivation-review.md).
- Kimi reviewed the duplicate-setup guard while HausmanHub is ordinarily stopped.
  Its first pass found a test that depended on exact source formatting; the
  check now uses semantic markers and order instead. The final direct Kimi
  review found no issues. See the [stopped duplicate-setup review
  note](LLM_WIKI/Manual/2026-07-15-kimi-stopped-duplicate-setup-review.md).
- Kimi reviewed the duplicate-setup guard while a saved HausmanHub setup stays
  user-deactivated after restart, with no findings. It confirmed that the
  rejected second setup preserves the disabled state and that explicit
  activation restores only the same nine safe counts. See the [disabled
  duplicate-setup review
  note](LLM_WIKI/Manual/2026-07-15-kimi-disabled-duplicate-setup-review.md).
- Kimi reviewed removal of a saved user-deactivated HausmanHub setup after an empty
  restart, with no findings. It confirmed the same collision-aware nine
  disabled records survive until removal and that the following restart remains
  HausmanHub-free. See the [disabled removal-after-restart review
  note](LLM_WIKI/Manual/2026-07-15-kimi-disabled-removal-after-restart-review.md).
- Kimi reviewed the isolated extra-input boundary check for both HausmanHub setup
  forms with no findings. It confirmed that the test preserves the fixed safe
  saved shape and adds no runtime, device, service, network, or home-data
  access. See the [extra config-form-input review
  note](LLM_WIKI/Manual/2026-07-15-kimi-extra-config-form-input-review.md).
- Kimi reviewed the version 0.3.6 safe options-form default. Its first pass
  requested an explicit test for the still-approved `shadow` default; after
  that test was added, the final review found no issues. See the [safe
  options-default review
  note](LLM_WIKI/Manual/2026-07-15-kimi-safe-options-default-review.md).
- Kimi reviewed recovery after a corrected temporary saved setting with no
  findings. It confirmed the additional persistence restart, exact same
  nine-count sensor names, fixed diagnostics, GET-only local page, collision
  preservation, and clean removal. See the [corrected-settings recovery review
  note](LLM_WIKI/Manual/2026-07-15-kimi-corrected-settings-recovery-review.md).
- Kimi reviewed the bad saved mode-option lifecycle with no findings. It
  confirmed option persistence, Core compatibility, the exact nine-count
  boundary, collision preservation, GET-only local access, and final cleanup.
  See the [invalid-options review
  note](LLM_WIKI/Manual/2026-07-15-kimi-invalid-persisted-options-review.md).
- Kimi reviewed the shared lifecycle for both an unsafe saved mode and a
  safe-looking mode with an extra field, with no findings. It confirmed the
  exact settings shape closes HausmanHub through reload and restart, and that only the
  original safe choice restores the same nine counts before cleanup. See the
  [extra-option review
  note](LLM_WIKI/Manual/2026-07-15-kimi-extra-saved-option-review.md).
- The old private-first skeleton decision is now clearly marked historical and
  points to the current public manual-HACS decision. Kimi first asked for a
  less brittle document guard; after that correction, its final review found no
  issues. See the [historical-decision review
  note](LLM_WIKI/Manual/2026-07-15-kimi-historical-skeleton-decision-review.md).

## Verification

Run `python3 -m unittest discover -s tests -v`. The suite validates synthetic
schema data, in-memory form/observation adapters, and strict count-only
diagnostics boundaries; it does not prove shadow parity or grant authority.
The isolated Core lifecycle check is documented in `docs/read-only-skeleton.md`;
on 2026-07-15 it passed with the aggregate summary, exactly nine diagnostic
count sensors, and guarded authenticated loopback route on Core 2026.6.4 and
2026.7.0 using disposable configurations only. It also now starts from a
temporary v0.3.0-style registry, replaces only the temporary HausmanHub copy, and
requires the old names to survive while a new entry receives the protected
v0.3.1 names. Before that new entry, the check reserves one protected-looking
name only in the disposable registry and requires the occupied name to remain
external while all nine HausmanHub sensors still appear. After HausmanHub removal, it
requires that external record to remain unchanged. It then creates and removes
another safe HausmanHub setup, requiring its nine sensors and the same external
record again. After each removal it sends one authenticated loopback GET from a
temporary exact read-only user to the retained local-summary route, requires an
unavailable response, and rejects any returned count key. It proves neither
live-home behaviour nor execution authority. It also records the temporary
HausmanHub state names before each removal and requires all of those states to be
absent afterward, without reading their values. It also requires no HausmanHub device
registry entry and no device attachment for each HausmanHub sensor. It also tries a
second safe setup and requires Home Assistant to refuse it while preserving the
original nine-sensor setup. After the final removal it starts a third empty
Home Assistant instance with the same temporary configuration and requires no
HausmanHub entry, entity, device, service, state, runtime data, or local route to
return, while the unrelated temporary external record remains unchanged.
Only after that absence proof, it creates a fresh `read-only` HausmanHub setup in the
same third instance. The new setup must have a new entry identifier, exactly
nine count sensors, the fixed safe diagnostics report, the unchanged external
record, and the guarded authenticated local route.
That fresh setup is then removed, its route must immediately fail closed
without count data, and a fourth empty Home Assistant instance must contain no
HausmanHub data while the external record remains unchanged.

Before its first restart, the check also deactivates the saved safe setup
through Home Assistant's normal user path. The setup must become unloaded, its
nine registry entries must be marked disabled by that setup, and the guarded
local route must return only an unavailable response without count keys. After
reactivation, it must restore the same nine enabled count sensors, safe
diagnostics, and the authenticated GET-only route without any device, service,
proxy, or execution capability.

One later temporary reinstallation is deactivated before removal. The check
then requires removal to clear its nine HausmanHub records, temporary states, and
guarded page, while preserving the unrelated temporary external record through
the next empty restart.

Before the earlier temporary update restart, the first safe setup is also
deactivated. The restarted empty Home Assistant must keep it disabled, with no
HausmanHub runtime data, count state, or guarded page. Only explicit reactivation
may restore the existing nine safe count sensors, diagnostics, and GET-only
page.

Throughout that temporary lifecycle, the check counts every local HausmanHub page.
An active setup must have exactly one. After a deactivation or removal in the
same temporary process, that one retained page must fail closed without counts;
after a full temporary restart while HausmanHub is disabled or removed, no page may
return.

The same disposable Core check writes one deliberately unsafe saved HausmanHub mode,
rejects an immediate reload, then restarts. It requires no HausmanHub runtime data,
service, device, page, or count state to return. HausmanHub clears only the restored
states belonging to that invalid HausmanHub entry after Home Assistant startup; it
does not change other entities or any device-control surface.

Separately, direct local Codex observation passed a harmless availability
check, a version-only check, and a count-only current-state check on
2026-07-14. It used no command or mutating request, retained no raw home data,
and does not validate or expand HausmanHub runtime authority.

Before publishing, run `python3 tools/check_local_release.py` after staging
the intended files. It runs the local tests, synthetic fixture checks, and the
Git-file safety checks as one fixed list. It also requires a higher integration
version if a staged change touches HausmanHub itself or `hacs.json`. It does not
inspect a live home or grant any authority.

The repository also runs that same fixed command in GitHub after a change to
`main` or a proposed change. Its workflow has only `contents: read`, disables
stored checkout credentials, and has no Home Assistant target, home data, or
deployment step.
Its first GitHub run completed successfully on 2026-07-14 for commit
`a75f78b`; the recorded run is
https://github.com/shumkiiv/hausmanhub_hacs/actions/runs/29352007883.
Public contribution guidance and a pull-request safety checklist are present.
They require the local check, Kimi review for code, and an explicit statement
that no home data or control capability is being introduced.
A Russian release checklist records the safe order for a real HACS update:
version, version history, local check, Kimi review, GitHub check, published
release, HACS refresh, and Home Assistant restart. Documentation-only and
test-only changes do not need a new HACS version.

## Current release handoff

- The remaining 1.17.2 red banner was reproduced in the user's normal Edge
  profile at `http://homeassistant.local:8123/hausman-hub`. Windows resolved
  the mDNS name to Home Assistant's IPv6 link-local address and Edge connected
  from another scoped `fe80::/10` address. Direct RFC1918 IPv4 requests
  returned HTTP 200, while the browser path was rejected by
  `climate_api._is_local_address`.
- Release candidate 1.17.3 permits IPv6 link-local only when the existing
  non-system administrator guard calls the shared address helper. Tablet
  routes keep the previous boundary, and the separate fixed read-only summary
  boundary remains unchanged. Regression coverage accepts plain, scoped, and
  upper-boundary `fe80::/10`, rejects neighboring and public IPv6, and proves
  that the tablet capability route still returns 403.
- `python3 tools/check_local_release.py` passes 623 tests plus fixture,
  Android-compatibility, version, naming, HACS-package, and repository-safety
  checks. Three configured Kimi reviewers could not start because of
  billing-cycle quota HTTP 403 (`ses_070fcb1edffeoQc7sXq6i4fGlc`,
  `ses_070fc2698ffef461bP6l0ZS6ei`, and
  `ses_070fbe366ffe1kpx6b5uPMnKRT`), so no Kimi PASS is claimed. The final
  read-only OpenAI fallback review returned PASS with no substantial findings
  in OpenCode session `ses_070fb78a1ffe1nlEik803qO5E0`.
- Release commit `38cf6c5` was pushed to `origin/main`; GitHub Actions run
  `30009265197` passed. Stable release `v1.17.3` was published at
  https://github.com/shumkiiv/hausmanhub_hacs/releases/tag/v1.17.3. The remote
  tag resolves exactly to `38cf6c5`, and the tagged manifest declares
  `1.17.3`.
- No live Home Assistant update, restart, or configuration change occurred.
  Next: refresh HACS, install 1.17.3, restart Home Assistant, and retest the
  same Edge `homeassistant.local` URL.

## Next decision gate

The active 50-item roadmap changes only HausmanHub. Android is already developed in a
separate read-only repository; HausmanHub must provide stable contracts for it without
editing or building the application here. The existing climate module is also
read-only and remains the execution engine through its current fixed API. The
first 1.6 milestone is API discovery and a combined climate projection; a
readable decision journal, a continuous HausmanHub dispatcher, and further contour
types follow. Generic proxying, arbitrary device execution, changes to the
climate module, and unsupervised live deployment remain out of scope.

The current public manual-HACS decision and its narrow implementation boundary
are recorded in the [HACS packaging decision
record](docs/hacs-packaging-decision.md). The original private-first skeleton
choice is preserved in [the historical skeleton decision
record](docs/read-only-skeleton-decision.md); it is not the current installation
instruction. The skeleton's implementation boundary is documented in
[the read-only skeleton guide](docs/read-only-skeleton.md).

See [repository basics](docs/repository-basics.md) and
[static validation](docs/static-validation.md),
[shadow evidence](docs/shadow-evidence-contract.md),
[diagnostics/repairs](docs/diagnostics-repairs-contract.md), and the
[foundation handoff](LLM_WIKI/Manual/2026-07-13-hausmanhub-repository-foundation.md).
Engineering and review rules are in
[engineering standards](docs/engineering-standards.md).

<!-- llm-wiki-sync:start -->
## LLM Wiki

- Obsidian/context index: `LLM_WIKI/00_Index.md`.
- Latest generated context: `LLM_WIKI/Context.md`.
- Last sync: 2026-08-23T07:01:58+03:00.
<!-- llm-wiki-sync:end -->
