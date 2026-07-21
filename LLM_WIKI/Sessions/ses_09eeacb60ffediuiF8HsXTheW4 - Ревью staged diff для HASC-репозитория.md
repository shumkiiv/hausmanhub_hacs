# Ревью staged diff для HASC-репозитория

- Session: `ses_09eeacb60ffediuiF8HsXTheW4`
- Agent: `ivsh-plan`
- Project: `/home/ivsh/projects/hausmanhub_hasc`

## 2026-07-14T17:43:28+03:00 - user - user-prompt

- Part: `prt_f61153511001QjNiHUfXXd1GuK`
- Message: `msg_f61153509001AqnEg6IIjLX7RT`

"Ты финальный независимый ревьюер. Проверь ТОЛЬКО staged diff и не меняй файлов. Это отдельный публичный HASC-репозиторий: нельзя добавлять управление домом, Home Assistant services, Node-RED, сеть, секреты, proxy или direct execution. Новый локальный инструмент должен проверять подготовленные файлы перед публикацией. Главное: Git-данные читаются только через index, без перехода по символьным ссылкам за репозиторий; --staged проверяет только подготовленные изменения; workflow.json не должен ошибочно считаться Node-RED flow. Проверь `git diff --cached`, инструменты и тесты. Ответь кратко, строго одним из двух вариантов: `Находок нет.` либо список находок с важностью и file:line. Проверки автора: 47 unittest, check_repository_boundary в обычном и --staged режиме, git diff --cached --check — успешно."

