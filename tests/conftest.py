"""Cấu hình chung cho bộ test.

PySide6 6.12.0 + Python 3.11 (máy thử): mọi hàm Qt trả về void gọi từ Python (setText, update, processEvents,
QTest.qWait…) làm mất 1 tham chiếu của None -> giữa bộ test None về 0, Python sập "deallocating None" (mã 134).
Tool tự bật lá chắn khi mở (`trami.config.guard_none_refcount`); ở đây làm y hệt trước khi test nào chạy (không
import trami – test tự đặt TRAMI_DATA trước khi nạp config).
"""
from __future__ import annotations

import sys


def _guard_none() -> None:
    if sys.version_info >= (3, 12) or hasattr(sys, "gettotalrefcount"):
        return
    try:
        from PySide6.QtCore import QObject
    except ImportError:
        return
    o = QObject()
    before = sys.getrefcount(None)
    for _ in range(64):
        o.setObjectName("x")
    if before - sys.getrefcount(None) >= 48:
        import ctypes

        ctypes.c_ssize_t.from_address(id(None)).value += 1 << 60


_guard_none()
