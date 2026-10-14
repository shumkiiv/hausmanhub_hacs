"""Import only an immutable installed package, never the checkout's runtime."""

import hashlib
import importlib
import json
import os
from pathlib import Path
import socket
import sys

EXPECTED_SOURCE_DIGEST = "2800211a415719a8ca2f21f9e1370578a42fefbdc7e3592788a8ca73e296bd59"

def verify_source(candidate, *, expected_digest=EXPECTED_SOURCE_DIGEST):
    candidate = Path(candidate).resolve(strict=True)
    document = json.loads((candidate / "verification.json").read_text())
    root = candidate / "files"
    declared = document["files"]
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    if actual != set(declared):
        raise AssertionError("installed snapshot file set changed")
    for relative, expected in declared.items():
        path = root / relative
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected["sha256"]:
            raise AssertionError(f"installed snapshot changed: {relative}")
    digest = hashlib.sha256(json.dumps(declared, sort_keys=True).encode()).hexdigest()
    if digest != expected_digest:
        raise AssertionError("not the declared installed N1 basis")
    return root, digest


def close_network(*, read_roots=(), write_root=None):
    def denied(*args, **kwargs):
        raise RuntimeError("N1 forbids external network")
    socket.socket.connect = denied
    socket.socket.connect_ex = denied
    socket.socket.sendto = denied
    socket.create_connection = denied
    socket.getaddrinfo = denied
    read_roots = tuple(Path(root).resolve() for root in read_roots)
    write_root = Path(write_root).resolve() if write_root else None
    def inside(path, root):
        return path == root or root in path.parents
    def audit(event, args):
        if event == "socket.__new__" and args[1] in {socket.AF_INET, socket.AF_INET6}:
            denied()
        if event in {"socket.connect", "socket.sendto", "subprocess.Popen", "os.system"}:
            denied()
        if event == "open" and not isinstance(args[0], int) and write_root is not None:
            path = Path(os.fsdecode(args[0])).resolve()
            mode, flags = args[1], args[2]
            writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or bool(
                flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
            if writing and not inside(path, write_root):
                raise RuntimeError("N1 refuses write outside temporary storage")
            if not writing and not inside(path, write_root) and not any(inside(path, root) for root in read_roots):
                raise RuntimeError("N1 refuses undeclared file read")
        if event in {"os.rename", "os.remove", "os.rmdir", "os.mkdir"} and write_root is not None:
            paths = args[:2] if event == "os.rename" else args[:1]
            if any(not inside(Path(os.fsdecode(path)).resolve(), write_root) for path in paths):
                raise RuntimeError("N1 refuses mutation outside temporary storage")
    sys.addaudithook(audit)


def load_installed(candidate, *, temporary_root, oracle_root, expected_digest=EXPECTED_SOURCE_DIGEST):
    root, digest = verify_source(candidate, expected_digest=expected_digest)
    if any(name.startswith("custom_components.hausman_hub") for name in sys.modules):
        raise AssertionError("SUT already imported; use a fresh process")
    if os.environ.get("HASSIO") or os.environ.get("HAUSMAN_HUB_CLIMATE_LEDGER_KEYRING_PATH"):
        raise AssertionError("N1 requires a sanitized environment without house access")
    close_network(read_roots=(root, Path(candidate)/"verification.json", Path(__file__).parent,
        "/usr/lib/python3.12", "/usr/lib/python3/dist-packages", "/usr/local/lib/python3.12", "/usr/share/zoneinfo/UTC",
        oracle_root), write_root=temporary_root)
    sys.path.insert(0, str(root))
    package = importlib.import_module("custom_components.hausman_hub")
    if Path(package.__file__).resolve() != root / "custom_components/hausman_hub/__init__.py":
        raise AssertionError("wrong startup loaded")
    return package, digest
