# Read-only review удаления HASC-записей

- Session: `ses_099b2ba01ffe9YS1RBppbo5e9o`
- Agent: `build`
- Project: `/home/ivsh/projects/hausmanhub_hasc`

## 2026-07-15T18:02:48+03:00 - user - user-prompt

- Part: `prt_f664d46330011p0q7kbK7vQVnj`
- Message: `msg_f664d462a001p39usp4N45lXpE`

"Ты Kimi и выполняешь финальный короткий read-only review. НЕ используй task, НЕ читай и НЕ меняй файлы, НЕ делай commit/push. Ответь только конкретными findings с уровнем или NO FINDINGS. Оцени этот уже проверенный локальными тестами фрагмент HASC: при плохих сохранённых настройках вызывается функция ниже. Цель: убрать только записи HASC, не трогая дом. @callback из homeassistant.core означает синхронную функцию, безопасную для основного цикла HA; async_at_started запускает её на старте.\n\n```python\ndef _clear_restored_hasc_records_for_invalid_entry(hass, entry):\n    from homeassistant.core import callback\n    from homeassistant.helpers import entity_registry\n    from homeassistant.helpers.start import async_at_started\n\n    @callback\n    def clear_hasc_records_after_start(_):\n        entities = entity_registry.async_get(hass)\n        entries = entity_registry.async_entries_for_config_entry(entities, entry.entry_id)\n        for registered_entity in entries:\n            hass.states.async_remove(registered_entity.entity_id)\n            entities.async_remove(registered_entity.entity_id)\n\n    if getattr(hass, \"is_running\", False):\n        clear_hasc_records_after_start(hass)\n    else:\n        async_at_started(hass, clear_hasc_records_after_start)\n```\n\nУчебный тест подменяет async_at_started так, что он выбрасывает ошибку, если callback не имеет `_hass_callback`, затем проверяет: удалена только HASC-запись, а отдельная внешняя запись осталась. Полные пустые проверки Home Assistant 2026.6.4 и 2026.7.0 проходят; они также доказывают отсутствие HASC-служб, устройств, страницы, записей, состояний после плохих data/options и сохранность внешней учебной записи. Никакие настоящие данные/HA/Node-RED не используются. Проверь чистоту, границы и поток выполнения."

