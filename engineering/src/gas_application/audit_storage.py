"""Filesystem boundary for gas demo ledgers."""

import os
import stat
from pathlib import Path


def private_sqlite_path(path: Path) -> Path:
    """Reserve a private local ledger file outside any Git working tree."""
    try:
        parent = path.parent
        resolved = parent.resolve(strict=True)
        if os.name != "posix" or not path.is_absolute() or resolved != parent:
            raise ValueError("noncanonical directory")
        for directory in (resolved, *resolved.parents):
            if os.path.lexists(directory / ".git") or ((directory / "HEAD").is_file() and (directory / "objects").is_dir()):
                raise ValueError("repository directory")
        directory_stats = resolved.stat()
        if (not stat.S_ISDIR(directory_stats.st_mode) or stat.S_IMODE(directory_stats.st_mode) != 0o700
                or directory_stats.st_uid != os.geteuid() or path.is_symlink()):
            raise ValueError("nonprivate directory or ledger")
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            ledger = os.fstat(fd)
            if (not stat.S_ISREG(ledger.st_mode) or stat.S_IMODE(ledger.st_mode) != 0o600
                    or ledger.st_uid != os.geteuid() or ledger.st_nlink != 1):
                raise ValueError("nonprivate ledger")
        finally:
            os.close(fd)
    except (OSError, RuntimeError, ValueError):
        raise RuntimeError("燃气审计数据库配置无效") from None
    return path
