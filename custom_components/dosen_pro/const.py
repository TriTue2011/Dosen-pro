"""Hằng số của tích hợp Dosen Pro."""

DOMAIN = "dosen_pro"
CONF_URL = "url"
CONF_API_KEY = "api_key"
SCAN_INTERVAL_SECONDS = 10


def device_info(entry_id: str) -> dict:
    """Một thiết bị cho cả loa và màn hình (tên thực thể luôn có tiền tố «Dosen Pro»)."""
    return {"identifiers": {(DOMAIN, entry_id)}, "name": "Dosen Pro", "manufacturer": "DOSEN"}
