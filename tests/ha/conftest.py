import sys
import types

import pytest
from pytest_homeassistant_custom_component.common import mock_component

try:                                    # thành phần camera của HA nạp turbojpeg lúc import; ảnh thử có thể không có sẵn
    import turbojpeg  # noqa: F401
except ImportError:
    _m = types.ModuleType("turbojpeg")
    _m.TurboJPEG = type("TurboJPEG", (), {})
    sys.modules["turbojpeg"] = _m


_REAL: dict = {}                       # hàm thật bị thay bằng bản giả trong test (vd. vòng nghe của vệ tinh)


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def _fake_camera(monkeypatch):
    """Camera giả thay cho lõi thật ở MỌI test tích hợp: không mở luồng/ffmpeg/pipeline thật."""
    import custom_components.dosen_pro as pkg
    import custom_components.dosen_pro.config_flow as cflow
    from custom_components.dosen_pro import assist_satellite as sat_mod
    from fakes import FakeCamera
    FakeCamera.instances.clear()
    FakeCamera.login_error = None
    monkeypatch.setattr(pkg, "Camera", FakeCamera)
    monkeypatch.setattr(cflow, "Camera", FakeCamera)

    async def _no_loop(self):
        return
    _REAL.setdefault("chay", sat_mod.DosenSatellite._chay)          # test cần vòng nghe thật thì lấy lại từ đây
    monkeypatch.setattr(sat_mod.DosenSatellite, "_chay", _no_loop)


@pytest.fixture(autouse=True)
def _frontend_stub(request, monkeypatch):
    """Ảnh thử không có gói giao diện của HA (hass_frontend): coi như đã nạp «frontend»/«panel_custom» và bỏ qua phần đăng ký tệp JS/thanh bên
    (có test riêng cho phần đó). Lệnh WebSocket vẫn được đăng ký thật."""
    if "hass" in request.fixturenames:
        hass = request.getfixturevalue("hass")
        for name in ("frontend", "panel_custom"):
            mock_component(hass, name)
        import custom_components.dosen_pro as pkg

        async def _no_assets(_hass):
            return None
        monkeypatch.setattr(pkg, "_async_register_frontend_assets", _no_assets)
    yield
