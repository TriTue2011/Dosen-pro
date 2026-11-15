import sys
import types

import pytest

try:                                    # thành phần camera của HA nạp turbojpeg lúc import; ảnh thử có thể không có sẵn
    import turbojpeg  # noqa: F401
except ImportError:
    _m = types.ModuleType("turbojpeg")
    _m.TurboJPEG = type("TurboJPEG", (), {})
    sys.modules["turbojpeg"] = _m


@pytest.fixture(autouse=True)
def _enable(enable_custom_integrations):
    yield
