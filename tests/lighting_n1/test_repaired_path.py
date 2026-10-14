"""Same N1 oracle and real entry points on an independently pinned repair."""

import test_installed_path


class RepairedPath(test_installed_path.InstalledPath):
    source_digest = "72bf99fcdb56520a707d89e40968e36f4291ff657998f065ff0f2793ef1d8a51"
    verify_repaired_followup = True
