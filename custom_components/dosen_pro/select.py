"""Ô chọn pipeline Assist và độ nhạy "nói xong" cho vệ tinh camera."""

from __future__ import annotations

from homeassistant.components.assist_pipeline import AssistPipelineSelect, VadSensitivitySelect
from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_added_domain, async_track_state_removed_domain
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import DosenApiError
from .const import DEFAULT_MODES, DOMAIN, FIT_OPTIONS, NONE_OPTION, ROTATE_OPTIONS, device_info
from .entity import DosenEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    c = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DosenPipelineSelect(hass, c), DosenVadSelect(hass, c), DosenDefaultScreen(c),
                        DosenDefaultCamera(c), DosenDefaultImage(c), DosenDefaultWeather(c), DosenDefaultRotate(c),
                        DosenDefaultFit(c)])


class DosenPipelineSelect(DosenEntity, AssistPipelineSelect):
    def __init__(self, hass: HomeAssistant, coordinator) -> None:
        DosenEntity.__init__(self, coordinator)
        AssistPipelineSelect.__init__(self, hass, DOMAIN, coordinator.entry.entry_id)


class DosenVadSelect(DosenEntity, VadSensitivitySelect):
    def __init__(self, hass: HomeAssistant, coordinator) -> None:
        DosenEntity.__init__(self, coordinator)
        VadSensitivitySelect.__init__(self, hass, coordinator.entry.entry_id)


class _RestoreSelect(CoordinatorEntity, SelectEntity, RestoreEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, key: str, name: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}-{key}"
        self._attr_name = name
        self._attr_device_info = device_info(coordinator.entry.entry_id)


class DosenDefaultScreen(_RestoreSelect):
    """Loại màn hình mặc định (hiện khi không có sự kiện nào đè lên). Nguồn chọn ở các ô «Camera mặc định»,
    «Ảnh mặc định», «Địa chỉ mặc định» ngay bên dưới."""

    _attr_options = DEFAULT_MODES

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "default_screen", "Màn hình mặc định")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        cu = await self.async_get_last_state()
        if cu is not None and cu.state in DEFAULT_MODES:
            self.coordinator.default_mode = cu.state

    @property
    def current_option(self) -> str | None:
        return (self.coordinator.default_mode
                or ((self.coordinator.data or {}).get("screen", {}).get("default") or {}).get("mode"))

    async def async_select_option(self, option: str) -> None:
        before = self.coordinator.default_mode
        self.coordinator.default_mode = option
        try:
            await self.coordinator.apply_default()
        except HomeAssistantError:
            self.coordinator.default_mode = before          # không áp dụng được thì không đổi (và báo lỗi cho người dùng)
            raise
        self.async_write_ha_state()


class _SourcePicker(_RestoreSelect):
    """Ô chọn theo danh sách entity của HA (camera hoặc ảnh). Chọn xong, nếu loại mặc định đang dùng nguồn này thì áp dụng luôn."""

    _domain = ""
    _field = ""
    _modes: tuple[str, ...] = ()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        cu = await self.async_get_last_state()
        if cu is not None and cu.state not in (None, "unknown", "unavailable", NONE_OPTION):
            setattr(self.coordinator, self._field, cu.state)
        # Danh sách theo entity của HA: cập nhật khi có camera/ảnh mới hoặc mất đi.
        self.async_on_remove(async_track_state_added_domain(self.hass, self._domain, self._lam_moi))
        self.async_on_remove(async_track_state_removed_domain(self.hass, self._domain, self._lam_moi))

    @callback
    def _lam_moi(self, _event) -> None:
        self.async_write_ha_state()

    @property
    def options(self) -> list[str]:
        return [NONE_OPTION] + sorted(self.hass.states.async_entity_ids(self._domain))

    @property
    def current_option(self) -> str:
        return getattr(self.coordinator, self._field) or NONE_OPTION

    async def async_select_option(self, option: str) -> None:
        before = getattr(self.coordinator, self._field)
        setattr(self.coordinator, self._field, "" if option == NONE_OPTION else option)
        if self.coordinator.default_mode in self._modes:
            try:
                await self.coordinator.apply_default()
            except HomeAssistantError:
                setattr(self.coordinator, self._field, before)
                raise
        self.async_write_ha_state()


class DosenDefaultCamera(_SourcePicker):
    _domain, _field, _modes = "camera", "default_camera", ("live",)

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "default_camera", "Camera mặc định")


class DosenDefaultImage(_SourcePicker):
    _domain, _field, _modes = "image", "default_image", ("image",)

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "default_image", "Ảnh mặc định")


class DosenDefaultWeather(_SourcePicker):
    """Thực thể thời tiết hiện trên đồng hồ (chọn theo danh sách ``weather.*`` của HA)."""

    _domain, _field, _modes = "weather", "default_weather", ()

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "default_weather", "Thời tiết trên đồng hồ")

    async def async_select_option(self, option: str) -> None:
        before = self.coordinator.default_weather
        self.coordinator.default_weather = "" if option == NONE_OPTION else option
        self.coordinator.track_weather()
        try:
            await self.coordinator.push_weather()
        except HomeAssistantError:
            self.coordinator.default_weather = before
            self.coordinator.track_weather()
            raise
        self.async_write_ha_state()


class _ViewSelect(_RestoreSelect):
    """Xoay hình / kiểu khung hình áp cho mọi thứ trên màn (trừ chữ và đồng hồ, đã vẽ sẵn đúng chiều). Chọn xong áp ngay."""

    _field = ""

    def _to_value(self, option: str):
        raise NotImplementedError

    def _to_option(self, value) -> str:
        raise NotImplementedError

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        cu = await self.async_get_last_state()
        if cu is not None and cu.state in self.options:
            setattr(self.coordinator, self._field, self._to_value(cu.state))

    @property
    def current_option(self) -> str:
        return self._to_option(getattr(self.coordinator, self._field))

    async def async_select_option(self, option: str) -> None:
        before = getattr(self.coordinator, self._field)
        setattr(self.coordinator, self._field, self._to_value(option))
        try:
            await self.coordinator.api.screen_view(self.coordinator.default_rotate, self.coordinator.default_fit)
        except DosenApiError as exc:
            setattr(self.coordinator, self._field, before)
            raise HomeAssistantError(f"Dosen Pro: {exc}") from exc
        self.async_write_ha_state()


class DosenDefaultRotate(_ViewSelect):
    _attr_options = ROTATE_OPTIONS
    _field = "default_rotate"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "default_rotate", "Xoay hình mặc định")

    def _to_value(self, option: str) -> int:
        return int(option.rstrip("°"))

    def _to_option(self, value: int) -> str:
        return f"{value}°"


class DosenDefaultFit(_ViewSelect):
    _attr_options = list(FIT_OPTIONS)
    _field = "default_fit"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "default_fit", "Kiểu khung hình mặc định")

    def _to_value(self, option: str) -> str:
        return FIT_OPTIONS[option]

    def _to_option(self, value: str) -> str:
        return next(label for label, v in FIT_OPTIONS.items() if v == value)
