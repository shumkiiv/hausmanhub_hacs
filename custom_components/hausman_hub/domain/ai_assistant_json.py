from __future__ import annotations

type AiJsonPrimitive = str | int | float | bool | None
type AiJsonValue = AiJsonPrimitive | list["AiJsonValue"] | dict[str, "AiJsonValue"]
type AiJsonObject = dict[str, AiJsonValue]
