"""Nạp gói ``dosen_core`` KHÔNG thêm thư mục tích hợp vào sys.path (ở đó có select.py, text.py… trùng tên thư viện chuẩn)."""

import importlib.util
import os
import sys


def load():
    if "dosen_core" in sys.modules:
        return sys.modules["dosen_core"]
    here = os.path.dirname(os.path.abspath(__file__))
    pkg = os.path.normpath(os.path.join(here, "..", "..", "custom_components", "dosen_pro", "dosen_core"))
    spec = importlib.util.spec_from_file_location("dosen_core", os.path.join(pkg, "__init__.py"),
                                                  submodule_search_locations=[pkg])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dosen_core"] = mod
    spec.loader.exec_module(mod)
    return mod
