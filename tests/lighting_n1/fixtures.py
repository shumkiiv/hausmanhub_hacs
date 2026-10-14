"""Synthetic contract inputs; no private entity/configuration is copied."""

ROOM = "room_n1"
LIGHT = "light.n1_main"
PRESENCE = "binary_sensor.n1_presence"


def room_config():
    return {
        "contract": {"name": "hausman-hub-room-lighting-config", "version": 1},
        "roomId": ROOM, "name": "N1", "version": 1,
        "devices": {
            "sensors": [{"id": "presence", "name": "N1", "kind": "presence", "entityId": PRESENCE,
                         "autoAdoptOverride": None}],
            "light_targets": [{"id": "n1_main", "name": "N1", "kind": "light", "entityId": LIGHT,
                "role": "main", "groupId": None, "brightness": True, "color_temperature": True,
                "autoAdoptOverride": None}],
            "power_switch": None, "wireless_switches": [], "selectAll": False,
        },
        "schedule": [{"id": "n1_day", "title": "N1", "when": {
            "daysOfWeek": "all", "holiday": False,
            "anchor": {"kind": "fixed", "time": "00:00", "offsetMinutes": 0}},
            "targets": {"lightTargets": ["n1_main"], "groupIds": [], "roles": []},
            "how": {"brightness": 60, "colorTemperature": 4000, "fade": False,
                    "mode": "on_presence", "minOnSeconds": 0}},
            {"id": "n1_next", "title": "N1 next", "when": {
                "daysOfWeek": "all", "holiday": False,
                "anchor": {"kind": "fixed", "time": "12:01", "offsetMinutes": 0}},
                "targets": {"lightTargets": ["n1_main"], "groupIds": [], "roles": []},
                "how": {"brightness": 40, "colorTemperature": 3000, "fade": False,
                        "mode": "on_presence", "minOnSeconds": 0}}],
        "switchBindings": [],
        "dimming": {"enabled": True, "onAbsence": True, "fadeSeconds": 20, "targetPercent": 0},
        "manualOffProtection": {"enabled": True, "minimumIntervalSeconds": 600,
            "releaseMode": "timer_and_absence", "stableAbsenceSeconds": 30, "priority": "manual_above_auto"},
        "awayBehavior": {"mode": "none"}, "autoAdopt": True, "commandsEnabled": True,
        "updatedAt": 1, "overrides": {},
    }


def off_settings():
    return {"globalPolicy": {"enabled": True, "minimumIntervalSeconds": 600,
        "releaseMode": "timer_and_absence", "stableAbsenceSeconds": 30,
        "extendOnRepeatedManualOff": True, "noSensorFallback": "timer_only",
        "protectedScope": "profile", "allowManualRelease": True},
        "roomOverrides": {}, "profileOverrides": {},
        "profiles": [{"roomId": ROOM, "profileId": "n1_day", "lightIds": [LIGHT],
                      "presenceSensorIds": [PRESENCE]}]}
