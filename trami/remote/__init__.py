"""Điều khiển tool từ xa bằng điện thoại (2.3.0).

- `server`: máy chủ web nhỏ chạy NGAY TRONG tool (luồng riêng, aiohttp) – trang điều khiển (thư mục web/) + API.
- `api`: các thao tác (dự án, việc, 4 bước, vùng phụ đề, dịch, lồng tiếng, xuất) – mọi thay đổi dự án chạy qua hàng việc
  (`jobs.manager`) để giao diện trên máy tính tự nạp lại như khi bấm nút trên máy.
- `tunnel`: tool tự nối ra VPS (trạm trung chuyển, vps/relay.py) để điện thoại ở ngoài vào được mà không mở cổng modem.
"""
from __future__ import annotations

from .server import apply_settings, status  # noqa: F401
