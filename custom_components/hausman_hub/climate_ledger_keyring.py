"""External keyring for authenticated climate operation ledgers.

The keyring is intentionally read from an administrator-managed file outside
the Home Assistant configuration directory.  Neither the key material nor its
path is persisted by this integration.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import hashlib
import hmac
import os
from pathlib import Path
import re
import stat
from typing import Mapping


_KEY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_HEX_KEY = re.compile(r"[a-f0-9]{64}")
KEYRING_PATH_ENV = "HAUSMAN_HUB_CLIMATE_LEDGER_KEYRING_PATH"
HA_OS_KEYRING_PATH = Path("/ssl/hausman_hub/climate-ledger.json")
_HA_OS_KEY_ID = "haos-1"


class ClimateLedgerKeyringError(ValueError):
    """The externally supplied climate-ledger keyring is unusable."""


@dataclass(frozen=True)
class LedgerAnchor:
    generation: int
    fingerprint: str
    authentication_tag: str


@dataclass(frozen=True)
class ClimateLedgerKeyring:
    """One signing key and a bounded set of keys accepted during rotation."""

    active_key_id: str
    keys: Mapping[str, bytes]
    source_path: Path | None = None
    ledger_anchors: Mapping[str, LedgerAnchor] | None = None

    @property
    def active_key(self) -> bytes:
        return self.keys[self.active_key_id]

    def key_for(self, key_id: object) -> bytes | None:
        return self.keys.get(key_id) if isinstance(key_id, str) else None

    def has_ledger_anchor(self, entry_id: str) -> bool:
        """Tell setup whether this entry already has external history."""

        if self.source_path is None:
            return False
        try:
            document = _read_keyring_document(self.source_path)
            anchors = document.get("ledger_anchors", {})
            if not isinstance(anchors, dict):
                raise ClimateLedgerKeyringError("external climate ledger anchor is invalid")
            current, pending = _anchor_state(
                anchors.get(entry_id), entry_id, self.keys.values()
            )
            return current is not None or pending is not None
        except (OSError, json.JSONDecodeError) as error:
            raise ClimateLedgerKeyringError("external climate ledger anchor is unavailable") from error

    def has_committed_ledger_anchor(self, entry_id: str) -> bool:
        """Tell setup whether an entry has durable, non-resettable history."""

        if self.source_path is None:
            return False
        try:
            document = _read_keyring_document(self.source_path)
            anchors = document.get("ledger_anchors", {})
            if not isinstance(anchors, dict):
                raise ClimateLedgerKeyringError("external climate ledger anchor is invalid")
            current, _pending = _anchor_state(
                anchors.get(entry_id), entry_id, self.keys.values()
            )
            return current is not None
        except (OSError, json.JSONDecodeError) as error:
            raise ClimateLedgerKeyringError("external climate ledger anchor is unavailable") from error

    def prepare_ledger_anchor(self, entry_id: str, envelope: Mapping[str, object]) -> None:
        """Durably prepare, but do not yet promote, one local ledger generation."""

        if self.source_path is None:
            return 0
        try:
            document = _read_keyring_document(self.source_path)
            raw_anchors = document.get("ledger_anchors", {})
            if not isinstance(raw_anchors, dict):
                raise ClimateLedgerKeyringError("external climate ledger anchor is invalid")
            current, pending = _anchor_state(raw_anchors.get(entry_id), entry_id, self.keys.values())
            # A pending anchor with no matching local envelope was interrupted
            # before the main write and can never authorize a rollback.
            if pending is not None:
                raw_anchors[entry_id] = _anchor_state_payload(current, None)
                current, pending = current, None
            generation = envelope.get("ledger_generation")
            if type(generation) is not int or generation != (1 if current is None else current.generation + 1):
                raise ClimateLedgerKeyringError("external climate ledger anchor is stale")
            fingerprint = hashlib.sha256(_canonical(envelope)).hexdigest()
            anchor_body = {"generation": generation, "fingerprint": fingerprint}
            pending_anchor = _signed_anchor(entry_id, anchor_body, self.active_key)
            raw_anchors[entry_id] = _anchor_state_payload(current, pending_anchor)
            document["ledger_anchors"] = raw_anchors
            _replace_keyring_document(self.source_path, document)
        except (OSError, json.JSONDecodeError) as error:
            raise ClimateLedgerKeyringError("external climate ledger anchor is unavailable") from error

    def finalize_ledger_anchor(self, entry_id: str, envelope: Mapping[str, object]) -> None:
        """Promote a prepared anchor after all local writes completed."""

        if self.source_path is None:
            return
        try:
            document = _read_keyring_document(self.source_path)
            anchors = document.get("ledger_anchors", {})
            if not isinstance(anchors, dict):
                raise ClimateLedgerKeyringError("external climate ledger anchor is invalid")
            current, pending = _anchor_state(anchors.get(entry_id), entry_id, self.keys.values())
            if pending is None or not _anchor_matches(pending, envelope):
                raise ClimateLedgerKeyringError("external climate ledger anchor is stale")
            anchors[entry_id] = _anchor_state_payload(pending, None)
            document["ledger_anchors"] = anchors
            _replace_keyring_document(self.source_path, document)
        except (OSError, json.JSONDecodeError) as error:
            raise ClimateLedgerKeyringError("external climate ledger anchor is unavailable") from error

    def verify_ledger_anchor(self, entry_id: str, envelope: Mapping[str, object]) -> bool:
        if self.source_path is None:
            return True
        try:
            document = _read_keyring_document(self.source_path)
            anchors = document.get("ledger_anchors", {})
            current, pending = _anchor_state(anchors.get(entry_id) if isinstance(anchors, dict) else None, entry_id, self.keys.values())
            if _anchor_matches(current, envelope):
                if pending is not None:
                    anchors[entry_id] = _anchor_state_payload(current, None)
                    document["ledger_anchors"] = anchors
                    _replace_keyring_document(self.source_path, document)
                return True
            if _anchor_matches(pending, envelope):
                anchors[entry_id] = _anchor_state_payload(pending, None)
                document["ledger_anchors"] = anchors
                _replace_keyring_document(self.source_path, document)
                return True
            return False
        except (OSError, json.JSONDecodeError):
            return False


def load_external_climate_ledger_keyring(
    *, config_dir: str | os.PathLike[str] | None = None,
    environ: Mapping[str, str] | None = None,
    ha_os_keyring_path: Path | None = None,
) -> ClimateLedgerKeyring:
    """Load a strict keyring from an external, administrator-owned JSON file.

    Home Assistant OS has a persistent ``/ssl`` volume mounted outside the
    configuration directory. On that platform only, the integration creates a
    private keyring there on first setup. Container and Supervised users keep
    the explicit environment-variable provider.
    """

    environment = os.environ if environ is None else environ
    raw_path = environment.get(KEYRING_PATH_ENV)
    if isinstance(raw_path, str) and raw_path:
        path = Path(raw_path).expanduser()
    elif environment.get("HASSIO"):
        path = ha_os_keyring_path or HA_OS_KEYRING_PATH
        _ensure_ha_os_keyring(path)
    else:
        raise ClimateLedgerKeyringError("external climate ledger keyring is not configured")
    if not path.is_absolute():
        raise ClimateLedgerKeyringError("external climate ledger keyring path is invalid")
    if config_dir is not None:
        try:
            path.resolve().relative_to(Path(config_dir).resolve())
        except ValueError:
            pass
        else:
            raise ClimateLedgerKeyringError("external climate ledger keyring must be outside Home Assistant config")
    try:
        document = _read_keyring_document(path)
    except (OSError, json.JSONDecodeError) as error:
        raise ClimateLedgerKeyringError("external climate ledger keyring is unavailable") from error
    if not isinstance(document, dict) or set(document) not in (
        {"active_key_id", "keys"}, {"active_key_id", "keys", "ledger_anchors"},
    ):
        raise ClimateLedgerKeyringError("external climate ledger keyring is invalid")
    active_key_id = document.get("active_key_id")
    raw_keys = document.get("keys")
    if (
        not isinstance(active_key_id, str)
        or _KEY_ID.fullmatch(active_key_id) is None
        or not isinstance(raw_keys, dict)
        or not 1 <= len(raw_keys) <= 4
    ):
        raise ClimateLedgerKeyringError("external climate ledger keyring is invalid")
    keys: dict[str, bytes] = {}
    for key_id, raw_key in raw_keys.items():
        if (
            not isinstance(key_id, str)
            or _KEY_ID.fullmatch(key_id) is None
            or not isinstance(raw_key, str)
            or _HEX_KEY.fullmatch(raw_key) is None
        ):
            raise ClimateLedgerKeyringError("external climate ledger keyring is invalid")
        keys[key_id] = bytes.fromhex(raw_key)
    if active_key_id not in keys:
        raise ClimateLedgerKeyringError("external climate ledger active key is unavailable")
    anchors = document.get("ledger_anchors")
    if anchors is not None and not isinstance(anchors, dict):
        raise ClimateLedgerKeyringError("external climate ledger anchor is invalid")
    return ClimateLedgerKeyring(active_key_id=active_key_id, keys=keys, source_path=path)


def _ensure_ha_os_keyring(path: Path) -> None:
    """Create the HA OS default keyring once, without weakening file safety."""

    if not path.is_absolute():
        raise ClimateLedgerKeyringError("external climate ledger keyring path is invalid")
    directory = path.parent
    try:
        metadata = os.lstat(directory)
    except FileNotFoundError:
        try:
            os.mkdir(directory, mode=0o700)
        except OSError as error:
            raise ClimateLedgerKeyringError("external climate ledger keyring is unavailable") from error
        metadata = os.lstat(directory)
    except OSError as error:
        raise ClimateLedgerKeyringError("external climate ledger keyring is unavailable") from error
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_mode & 0o077
    ):
        raise ClimateLedgerKeyringError("external climate ledger keyring permissions are unsafe")
    try:
        file_metadata = os.lstat(path)
    except FileNotFoundError:
        document = {
            "active_key_id": _HA_OS_KEY_ID,
            "keys": {_HA_OS_KEY_ID: os.urandom(32).hex()},
        }
        try:
            _replace_keyring_document(path, document)
        except OSError as error:
            raise ClimateLedgerKeyringError("external climate ledger keyring is unavailable") from error
    except OSError as error:
        raise ClimateLedgerKeyringError("external climate ledger keyring is unavailable") from error
    else:
        if stat.S_ISLNK(file_metadata.st_mode) or not stat.S_ISREG(file_metadata.st_mode):
            raise ClimateLedgerKeyringError("external climate ledger keyring permissions are unsafe")


def _read_keyring_document(path: Path) -> object:
    """Read only a private regular file, without following a symlink."""

    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
            raise OSError("external climate ledger keyring permissions are unsafe")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            chunks.append(chunk)
        return json.loads(b"".join(chunks).decode("utf-8"))
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _parse_anchor(value: object, entry_id: str, keys: object) -> LedgerAnchor | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"generation", "fingerprint", "authentication_tag"}:
        raise ClimateLedgerKeyringError("external climate ledger anchor is invalid")
    generation = value.get("generation")
    fingerprint = value.get("fingerprint")
    tag = value.get("authentication_tag")
    body = {"generation": generation, "fingerprint": fingerprint}
    if type(generation) is not int or generation < 1 or not isinstance(fingerprint, str) or not re.fullmatch(r"[a-f0-9]{64}", fingerprint) or not isinstance(tag, str):
        raise ClimateLedgerKeyringError("external climate ledger anchor is invalid")
    expected_payload = _canonical({"entry_id": entry_id, **body})
    if not any(
        isinstance(key, bytes)
        and hmac.compare_digest(tag, hmac.new(key, expected_payload, hashlib.sha256).hexdigest())
        for key in keys
    ):
        raise ClimateLedgerKeyringError("external climate ledger anchor is invalid")
    return LedgerAnchor(generation, fingerprint, tag)


def _signed_anchor(entry_id: str, body: Mapping[str, object], key: bytes) -> LedgerAnchor:
    generation = body["generation"]
    fingerprint = body["fingerprint"]
    if type(generation) is not int or not isinstance(fingerprint, str):
        raise ClimateLedgerKeyringError("external climate ledger anchor is invalid")
    return LedgerAnchor(
        generation, fingerprint,
        hmac.new(key, _canonical({"entry_id": entry_id, **body}), hashlib.sha256).hexdigest(),
    )


def _anchor_state(value: object, entry_id: str, keys: object) -> tuple[LedgerAnchor | None, LedgerAnchor | None]:
    if value is None:
        return None, None
    if not isinstance(value, dict) or set(value) != {"current", "pending"}:
        raise ClimateLedgerKeyringError("external climate ledger anchor is invalid")
    return (
        _parse_anchor(value.get("current"), entry_id, keys),
        _parse_anchor(value.get("pending"), entry_id, keys),
    )


def _anchor_state_payload(current: LedgerAnchor | None, pending: LedgerAnchor | None) -> dict[str, object]:
    def payload(value: LedgerAnchor | None) -> dict[str, object] | None:
        return None if value is None else {
            "generation": value.generation,
            "fingerprint": value.fingerprint,
            "authentication_tag": value.authentication_tag,
        }
    return {"current": payload(current), "pending": payload(pending)}


def _anchor_matches(anchor: LedgerAnchor | None, envelope: Mapping[str, object]) -> bool:
    return anchor is not None and anchor.generation == envelope.get("ledger_generation") and hmac.compare_digest(
        anchor.fingerprint, hashlib.sha256(_canonical(envelope)).hexdigest()
    )


def _replace_keyring_document(path: Path, document: object) -> None:
    temporary = path.with_name(f".{path.name}.{os.urandom(12).hex()}.next")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            encoded = _canonical(document)
            written = 0
            while written < len(encoded):
                written += os.write(descriptor, encoded[written:])
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
