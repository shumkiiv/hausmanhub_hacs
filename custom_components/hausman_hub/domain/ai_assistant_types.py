from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address
from urllib.parse import urlsplit, urlunsplit

from .ai_assistant_json import AiJsonValue


AI_ADVISORY_VERSION = 1
MAX_TIMESTAMP = 9_007_199_254_740_991
SUMMARY_CODES = frozenset({"advisory_available", "evidence_limited"})
_MAX_BASE_URL_LENGTH = 512
_MAX_MODEL_LENGTH = 128
_PRIVATE_IPV4 = (
    IPv4Network("10.0.0.0/8"),
    IPv4Network("172.16.0.0/12"),
    IPv4Network("192.168.0.0/16"),
)
_PRIVATE_IPV6 = IPv6Network("fc00::/7")


class AiProviderPreset(StrEnum):
    DEEPSEEK = "deepseek"
    OPENAI = "openai"
    CUSTOM = "custom"


class AiAdvisoryStatus(StrEnum):
    READY = "ready"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_TIMEOUT = "provider_timeout"
    PROVIDER_ERROR = "provider_error"
    PROVIDER_OUTPUT_INVALID = "provider_output_invalid"
    DISABLED = "disabled"
    UNCONFIGURED = "unconfigured"


@dataclass(frozen=True, slots=True)
class AiAssistantViolation(ValueError):
    code: str

    def __str__(self) -> str:
        return self.code


@dataclass(frozen=True, slots=True)
class AiAssistantSettings:
    enabled: bool
    preset: AiProviderPreset
    base_url: str
    model: str

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool or not isinstance(
            self.preset, AiProviderPreset
        ):
            raise AiAssistantViolation("invalid_settings")
        normalized_url = normalized_base_url(self.base_url)
        if type(self.model) is not str or not 0 < len(self.model) <= _MAX_MODEL_LENGTH:
            raise AiAssistantViolation("invalid_model")
        if self.model.strip() != self.model:
            raise AiAssistantViolation("invalid_model")
        object.__setattr__(self, "base_url", normalized_url)


def normalized_base_url(value: AiJsonValue) -> str:
    if type(value) is not str or not 0 < len(value) <= _MAX_BASE_URL_LENGTH:
        raise AiAssistantViolation("invalid_base_url")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AiAssistantViolation("invalid_base_url")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise AiAssistantViolation("invalid_base_url")
    host = parsed.hostname.lower()
    if is_link_local(host) or (
        parsed.scheme == "http" and not is_private_or_loopback(host)
    ):
        raise AiAssistantViolation("invalid_base_url")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def is_link_local(host: str) -> bool:
    try:
        return ip_address(host).is_link_local
    except ValueError:
        return False


def is_private_or_loopback(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ip_address(host)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    if isinstance(address, IPv4Address):
        return any(address in network for network in _PRIVATE_IPV4)
    return isinstance(address, IPv6Address) and address in _PRIVATE_IPV6
