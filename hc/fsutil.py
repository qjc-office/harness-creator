"""캐시 파일 쓰기. umask와 무관하게 0600으로, 원자적으로 쓴다(반쯤 쓴 파일을 읽지 않게)."""
from __future__ import annotations
import os, pathlib

def write_private(path, text: str) -> None:
    """같은 폴더의 고유 임시 파일에 0600으로 쓰고 fsync 뒤 os.replace. 실패하면 임시 파일을 지운다.
    os.open의 mode는 새로 만들 때만 적용되므로 fchmod로 한 번 더 0600을 못 박는다."""
    path = pathlib.Path(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fd = -1
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
        finally:
            if fd >= 0:
                os.close(fd)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
