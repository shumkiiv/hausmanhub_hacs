# Backend safety gate

Дата фиксации: 2026-08-23.

Релизный gate объединяет уже существующие поведенческие проверки вместо
подмены качества числом тестов:

- pure domain: climate, scenario, energy и physical device grouping;
- граничные последовательности чисел, времени, timezone и состояний;
- execution modes single, restart, queued и parallel;
- unavailable, stale, bouncing feedback и поздние события;
- atomic storage, migration и restart pending operations;
- optional/null/unknown contract mutations;
- disposable Home Assistant Core harness;
- fake service bus, задержанный read-back и partial failure;
- вода никогда не открывается автоматически, опасная команда требует
  подтверждения, stale evidence закрывает command path.

`tools/check_critical_coverage.py` запускает 156 safety tests и измеряет branch
coverage пяти критических runtime-модулей, включая общий ручной приоритет
света. Нижняя граница равна 75%. На эталонном запуске суммарное branch coverage
равно 76%, отдельные модули дают 70-90%. Любое падение ниже порога блокирует CI
и release.

Порог не является обещанием идеального покрытия. Он фиксирует текущую
измеренную базу, а новые ветви должны добавляться вместе с тестами. Flaky test
считается красным gate, автоматический повтор для его сокрытия не используется.
