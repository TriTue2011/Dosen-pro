"""Camera Dosen thành vệ tinh Assist gốc của HA (không qua Wyoming).

Làm theo khuôn của ``dahua_talk`` (kho ha-camera-talk): ffmpeg của HA đọc mic camera (luồng WAV
của dịch vụ Dosen) → PCM16 mono 16 kHz → ``async_accept_pipeline_from_satellite`` bắt đầu từ bước
từ gọi; trả lời TTS xin sẵn WAV 8 kHz một kênh (đúng định dạng loa camera) rồi gửi cho dịch vụ phát.
Hội thoại nối tiếp khi câu trả lời là câu hỏi lại. Khác ``dahua_talk``: **câu hỏi và câu trả lời hiện
lên màn hình camera** (công tắc «Hiện hội thoại trên màn hình»), rồi màn tự tắt sau vài giây.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from homeassistant.components import tts
from homeassistant.components.assist_pipeline import PipelineEvent, PipelineEventType, PipelineStage
from homeassistant.components.assist_satellite import (
    AssistSatelliteAnnouncement, AssistSatelliteConfiguration, AssistSatelliteEntity,
    AssistSatelliteEntityFeature)
from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

from .api import DosenApiError
from .const import CHAT_HOLD_SECONDS, CHAT_MAX_MESSAGES, DOMAIN, MIC_CHUNK, MIC_RATE
from .entity import DosenEntity

_LOGGER = logging.getLogger(__name__)
_TTS_THEM_GIAY = 5.0


def loc_mic(tang_db: float) -> str:
    """Bộ lọc ffmpeg cho tiếng mic: lọc thông cao 80 Hz bỏ lệch một chiều (DC) rồi tăng + chặn đỉnh."""
    loc = "highpass=f=80"
    if tang_db > 0:
        loc += f",volume={tang_db:g}dB,alimiter=limit=0.9:attack=5:release=50:level=false"
    return loc


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities) -> None:
    async_add_entities([DosenSatellite(hass.data[DOMAIN][entry.entry_id])])


class DosenSatellite(DosenEntity, AssistSatelliteEntity):
    _attr_name = None
    _attr_supported_features = (AssistSatelliteEntityFeature.ANNOUNCE
                                | AssistSatelliteEntityFeature.START_CONVERSATION)

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}-satellite"
        self._data = coordinator
        self._hang: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=64)
        self._dang_noi = False
        self._tts_xong = asyncio.Event()
        self._co_tts = False
        self._tiep = False
        self._vong: asyncio.Task | None = None
        self._mic_proc: asyncio.subprocess.Process | None = None
        self._mo_lai_mic = False
        self._chat: list[dict[str, str]] = []
        self._chat_task: asyncio.Task | None = None
        self._tat_task: asyncio.Task | None = None

    # ── Cấu hình HA đòi ───────────────────────────────────────────────

    @property
    def pipeline_entity_id(self) -> str | None:
        return er.async_get(self.hass).async_get_entity_id(
            "select", DOMAIN, f"{self._entry.entry_id}-pipeline")

    @property
    def vad_sensitivity_entity_id(self) -> str | None:
        return er.async_get(self.hass).async_get_entity_id(
            "select", DOMAIN, f"{self._entry.entry_id}-vad_sensitivity")

    @property
    def tts_options(self) -> dict[str, Any] | None:
        return {tts.ATTR_PREFERRED_FORMAT: "wav", tts.ATTR_PREFERRED_SAMPLE_RATE: 8000,
                tts.ATTR_PREFERRED_SAMPLE_CHANNELS: 1, tts.ATTR_PREFERRED_SAMPLE_BYTES: 2}

    @callback
    def async_get_configuration(self) -> AssistSatelliteConfiguration:
        raise NotImplementedError

    async def async_set_configuration(self, config: AssistSatelliteConfiguration) -> None:
        raise NotImplementedError

    # ── Vòng đời ───────────────────────────────────────────────────

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._vong = self._entry.async_create_background_task(
            self.hass, self._chay(), f"{self.entity_id} listen")
        self.async_on_remove(self._data.listen_mute(self._khi_tat_mic))
        self.async_on_remove(self._data.listen_gain(self._khi_doi_tang))

    async def async_will_remove_from_hass(self) -> None:
        for task in (self._vong, self._chat_task, self._tat_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        await super().async_will_remove_from_hass()

    @callback
    def _khi_tat_mic(self, tat: bool) -> None:
        if tat:
            self._cat_luot()

    @callback
    def _khi_doi_tang(self, _db: float) -> None:
        if self._mic_proc is not None and self._mic_proc.returncode is None:
            self._mo_lai_mic = True
            self._mic_proc.kill()

    @callback
    def _cat_luot(self) -> None:
        while not self._hang.empty():
            self._hang.get_nowait()
        self._hang.put_nowait(None)

    # ── Tai ─────────────────────────────────────────────────────────

    async def _chay(self) -> None:
        mic = self._entry.async_create_background_task(self.hass, self._doc_mic(), f"{self.entity_id} mic")
        try:
            while True:
                if self._data.mic_muted:
                    await asyncio.sleep(1)
                    continue
                bat_dau = PipelineStage.STT if self._tiep else PipelineStage.WAKE_WORD
                self._tiep = False
                self._co_tts = False
                self._tts_xong.clear()
                try:
                    await self.async_accept_pipeline_from_satellite(
                        audio_stream=self._luong_stt(), start_stage=bat_dau)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 — một lượt hỏng không được làm điếc vệ tinh
                    _LOGGER.exception("%s: pipeline failed", self.entity_id)
                    await asyncio.sleep(2)
                    continue
                if self._co_tts:
                    await self._tts_xong.wait()
        finally:
            mic.cancel()

    async def _luong_stt(self):
        while not self._hang.empty():
            self._hang.get_nowait()
        while (khuc := await self._hang.get()) is not None:
            yield khuc

    async def _doc_mic(self) -> None:
        """ffmpeg đọc luồng mic của dịch vụ liên tục; đứt thì mở lại."""
        api = self._data.api
        while True:
            lenh = [get_ffmpeg_manager(self.hass).binary, "-nostdin", "-hide_banner", "-loglevel", "error"]
            if api.mic_headers:
                lenh += ["-headers", api.mic_headers]
            lenh += ["-i", api.mic_url, "-vn", "-af", loc_mic(self._data.mic_gain_db),
                     "-ac", "1", "-ar", str(MIC_RATE), "-f", "s16le", "pipe:"]
            proc = self._mic_proc = await asyncio.create_subprocess_exec(
                *lenh, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL)
            try:
                while True:
                    khuc = await proc.stdout.readexactly(MIC_CHUNK)
                    if self._dang_noi or self._data.mic_muted:
                        continue
                    if self._hang.full():
                        self._hang.get_nowait()
                    self._hang.put_nowait(khuc)
            except asyncio.IncompleteReadError:
                if not self._mo_lai_mic:
                    _LOGGER.warning("%s: mic stream ended, reopening", self.entity_id)
            finally:
                if proc.returncode is None:
                    proc.kill()
                await proc.wait()
            if self._mo_lai_mic:
                self._mo_lai_mic = False
            else:
                await asyncio.sleep(5)       # mic camera đóng (camera bận/khoá đăng nhập): đừng dồn dập đăng nhập

    # ── Hiện hội thoại trên màn camera ──────────────────────────────

    def _chat_them(self, role: str, text: str, thay_dang_nghe: bool = True) -> None:
        if not self._data.show_chat or not text:
            return
        if thay_dang_nghe and self._chat and self._chat[-1].get("pending"):
            self._chat.pop()
        self._chat.append({"role": role, "text": text, **({"pending": True} if role == "info" else {})})
        del self._chat[:-CHAT_MAX_MESSAGES]
        self._chat_ve()

    def _chat_ve(self) -> None:
        """Vẽ lại màn hình hội thoại (gộp nhiều sự kiện sát nhau thành một lần gửi)."""
        if self._tat_task is not None:
            self._tat_task.cancel()
            self._tat_task = None
        if self._chat_task is None or self._chat_task.done():
            self._chat_task = self._entry.async_create_background_task(
                self.hass, self._chat_gui(), f"{self.entity_id} screen")

    async def _chat_gui(self) -> None:
        await asyncio.sleep(0.15)
        msgs = [{"role": m["role"], "text": m["text"]} for m in self._chat]
        try:
            await self._data.api.screen_text("Trợ lý", msgs, after="hold")
        except DosenApiError as exc:
            _LOGGER.warning("%s: cannot show conversation on the screen: %s", self.entity_id, exc)

    @callback
    def _chat_ket_thuc(self) -> None:
        """Trả lời xong: giữ chữ vài giây rồi tắt màn hiển thị (nếu vẫn đang hiện chữ của ta)."""
        if not self._chat:
            return
        if self._tat_task is not None:
            self._tat_task.cancel()
        self._tat_task = self._entry.async_create_background_task(
            self.hass, self._chat_tat(), f"{self.entity_id} screen off")

    async def _chat_tat(self) -> None:
        await asyncio.sleep(CHAT_HOLD_SECONDS)
        self._chat = []
        try:
            status = await self._data.api.screen_status()
            if status.get("mode") == "text":
                await self._data.api.screen_off()
        except DosenApiError as exc:
            _LOGGER.debug("%s: screen off after conversation failed: %s", self.entity_id, exc)

    # ── Miệng ───────────────────────────────────────────────────────

    def on_pipeline_event(self, event: PipelineEvent) -> None:
        data = event.data or {}
        if event.type is PipelineEventType.WAKE_WORD_END:
            self._chat = []
            self._chat_them("info", "Đang nghe…", thay_dang_nghe=False)
        elif event.type is PipelineEventType.STT_START and not self._chat:
            self._chat_them("info", "Đang nghe…", thay_dang_nghe=False)
        elif event.type is PipelineEventType.STT_END:
            self._chat_them("user", str((data.get("stt_output") or {}).get("text") or ""))
        elif event.type is PipelineEventType.INTENT_END:
            ra = data.get("intent_output") or {}
            self._tiep = bool(ra.get("continue_conversation"))
            noi = (((ra.get("response") or {}).get("speech") or {}).get("plain") or {}).get("speech")
            self._chat_them("assistant", str(noi or ""))
        elif event.type is PipelineEventType.TTS_END:
            ra = data.get("tts_output") or {}
            luong = tts.async_get_stream(self.hass, ra["token"]) if ra.get("token") else None
            if luong is None:
                self._tts_xong.set()
                self._chat_ket_thuc()
                return
            self._co_tts = True
            self._entry.async_create_background_task(self.hass, self._phat_tts(luong), f"{self.entity_id} tts")
        elif event.type is PipelineEventType.ERROR:
            _LOGGER.debug("%s: pipeline error %s", self.entity_id, data)
            self._tts_xong.set()

    async def _phat_tts(self, luong: tts.ResultStream) -> None:
        self._dang_noi = True
        try:
            wav = b"".join([k async for k in luong.async_stream_result()])
            async with asyncio.timeout(len(wav) / 16000 + _TTS_THEM_GIAY + 10):
                await self._data.api.speaker_play_bytes(wav)
                await self._cho_phat_xong(len(wav) / 16000 + _TTS_THEM_GIAY)
        except (DosenApiError, OSError, TimeoutError) as exc:
            _LOGGER.warning("%s: cannot play reply: %s", self.entity_id, exc)
        finally:
            self._dang_noi = False
            self.tts_response_finished()
            self._tts_xong.set()
            self._chat_ket_thuc()

    async def _cho_phat_xong(self, toi_da: float) -> None:
        """Dịch vụ phát theo nhịp thời gian thật: đợi tới khi loa báo phát xong để khỏi nghe chính mình."""
        end = asyncio.get_running_loop().time() + toi_da
        while asyncio.get_running_loop().time() < end:
            await asyncio.sleep(0.3)
            try:
                if not (await self._data.api.speaker_status()).get("playing"):
                    return
            except DosenApiError:
                return

    async def async_announce(self, announcement: AssistSatelliteAnnouncement) -> None:
        self._dang_noi = True
        try:
            if announcement.tts_token and (luong := tts.async_get_stream(self.hass, announcement.tts_token)):
                wav = b"".join([k async for k in luong.async_stream_result()])
                await self._data.api.speaker_play_bytes(wav)
            else:
                await self._data.api.speaker_play(announcement.media_id)
            await self._cho_phat_xong(60)
        except (DosenApiError, OSError) as exc:
            _LOGGER.warning("%s: cannot play announcement: %s", self.entity_id, exc)
        finally:
            self._dang_noi = False

    async def async_start_conversation(self, start_announcement: AssistSatelliteAnnouncement) -> None:
        await self.async_announce(start_announcement)
        self._tiep = True
        self._cat_luot()
