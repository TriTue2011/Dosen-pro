"""Cấu hình kết nối tới một camera (mỗi camera một đối tượng, không có biến toàn cục)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CamConfig:
    cam_ip: str = ""
    cam_port: int = 34567
    cam_user: str = "admin"
    cam_pass: str = ""
    cam_channel: int = 0
    cam_stream: str = "Main"          # luồng giám sát lấy tiếng mic: Main hoặc Extra1
    dvrip_connect_timeout: float = 5.0
    dvrip_read_timeout: float = 10.0
    screen_auto_wake: bool = True     # mở phiên màn hình thì bật nguồn màn hình (màn ngủ thì không hiện hình)
    ffmpeg: str = "ffmpeg"            # đường dẫn ffmpeg (trong HA: ffmpeg của HA)
