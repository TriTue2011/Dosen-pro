"""HTTP API for the ICSee DVRIP-native Docker service.

Features and their real status (see README.md for the full picture):
* Mic (GET /api/audio/mic) and speaker (POST /api/audio/talk/*) -- DVRIP
  native, implemented. NOT yet verified live on the DOSEN RPO V1 camera.
* Video to the camera screen (POST /api/video/*) -- transcode pipeline
  implemented (dry-run); the wire send is NOT implemented (see
  video_sender.WIRE_BLOCKER) and the API says so on every response.

No credential is ever logged or returned in a response.
"""

import asyncio
import base64
import binascii
import hmac
import json
import logging
import os
import time

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from . import video_sender
from .audio import MicSession, TalkSession
from .config import settings
from .dvrip import DVRIPClient, DVRIPError
from .events import AlarmSession
from .screen import Screen
from .speaker import Speaker
from .video import VideoPipeline

# Root logger: uvicorn only configures its own loggers, so without this the
# service's own INFO lines (events, reconnects) would never be printed.
# LOG_LEVEL=DEBUG also shows keepalive replies. Credentials are never logged.
logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s")

SERVICE_VERSION = "0.1.0"

app = FastAPI(title="icsee-docker",
              description="DVRIP-native HTTP API for ICSee/XM cameras "
                          "(mic + speaker live, video-to-screen dry-run).",
              version=SERVICE_VERSION)

@app.middleware("http")
async def _require_api_key(request: Request, call_next):
    """With API_KEY set, every /api/ call except /api/health must carry it.
    The mic stream and the speaker are sensitive: do not leave them open."""
    key = settings.api_key
    path = request.url.path
    if key and path.startswith("/api/") and path != "/api/health":
        sent = request.headers.get("x-api-key") or request.headers.get(
            "authorization", "").removeprefix("Bearer ").strip()
        if not hmac.compare_digest(sent.encode(), key.encode()):
            return JSONResponse({"error": "unauthorized",
                                 "message": "missing or wrong API key"},
                                status_code=401)
    return await call_next(request)


_mic = MicSession()
_talk = TalkSession()
_video = VideoPipeline()
_events = AlarmSession()
_screen = Screen()
_speaker = Speaker(_talk, _screen)
_started_at = time.time()


def _camera_configured() -> bool:
    return bool(settings.cam_ip)


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status,
                        content={"error": code, "message": message})


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "icsee-docker",
            "version": SERVICE_VERSION,
            "uptime_seconds": round(time.time() - _started_at, 1)}


@app.get("/api/status")
def status() -> dict:
    return {
        "camera": {
            "configured": _camera_configured(),
            "ip": settings.cam_ip or None,
            "port": settings.cam_port,
            "user": settings.cam_user,
            "channel": settings.cam_channel,
            "stream": settings.cam_stream,
        },
        "mic": _mic.status(),
        "talk": _talk.status(),
        "video": _video.status(),
        "events": _events.status(),
    }


# -- camera ------------------------------------------------------------

@app.post("/api/cam/connect")
def cam_connect() -> Response:
    """Test the DVRIP login against the configured camera (short-lived)."""
    if not _camera_configured():
        return _err(503, "camera_not_configured",
                    "ICSEE_CAM_IP is not set (see .env.example)")
    client = DVRIPClient(settings.cam_ip, settings.cam_port,
                         settings.dvrip_connect_timeout,
                         settings.dvrip_read_timeout)
    try:
        client.connect()
        reply = client.login(settings.cam_user, settings.cam_pass)
    except (OSError, DVRIPError) as exc:
        return _err(503, "camera_unreachable",
                    f"DVRIP login failed: {exc}")
    finally:
        client.close()
    return JSONResponse({"connected": True,
                         "session": reply.get("SessionID"),
                         "device": reply.get("DeviceType")})


@app.get("/api/cam/decoder-pram")
def cam_decoder_pram() -> Response:
    """Best-effort DecoderPram negotiation (opcode 1360).

    Tries Name "DecoderPram" then "EncodeCapability"; on failure returns
    the app fallback (864x480, fps 10, H264) with negotiated=false.
    UNVERIFIED on the DOSEN camera -- see dvrip.query_decoder_pram.
    """
    if not _camera_configured():
        return _err(503, "camera_not_configured",
                    "ICSEE_CAM_IP is not set (see .env.example)")
    client = DVRIPClient(settings.cam_ip, settings.cam_port,
                         settings.dvrip_connect_timeout,
                         settings.dvrip_read_timeout)
    try:
        client.connect()
        client.login(settings.cam_user, settings.cam_pass)
        pram = client.query_decoder_pram()
    except (OSError, DVRIPError) as exc:
        return _err(503, "camera_unreachable",
                    f"DecoderPram query failed: {exc}")
    finally:
        client.close()
    params = video_sender.apply_decoder_pram(pram)
    return JSONResponse({"raw": pram, "params": params})


# -- speaker (talk) ------------------------------------------------------

@app.post("/api/audio/talk/start")
def talk_start() -> Response:
    try:
        _talk.start()
    except (OSError, DVRIPError) as exc:
        return _err(503, "camera_unreachable",
                    f"talk start failed: {exc}")
    return JSONResponse(_talk.status())


@app.post("/api/audio/talk/send")
async def talk_send(request: Request) -> Response:
    """Send audio to the camera speaker.

    Accepts either:
    * application/octet-stream -- raw PCM16 8 kHz mono bytes, or raw
      G.711 A-law bytes when ?format=g711a is given;
    * application/json -- {"format": "pcm16"|"g711a", "data": "<base64>"}.
    Audio is chunked into 320-byte G.711 packets (OPTalkData, 1432).
    """
    if not _talk.running:
        return _err(409, "talk_not_started",
                    "call POST /api/audio/talk/start first")
    content_type = request.headers.get("content-type", "")
    fmt = request.query_params.get("format", "pcm16").lower()
    try:
        if content_type.startswith("application/json"):
            body = json.loads(await request.body())
            fmt = str(body.get("format", fmt)).lower()
            try:
                data = base64.b64decode(body.get("data", ""))
            except binascii.Error as exc:
                return _err(400, "bad_base64", f"invalid base64 data: {exc}")
        else:
            data = await request.body()
        if not data:
            return _err(400, "empty_body", "no audio data received")
        if fmt == "g711a":
            packets = _talk.send_g711a(data)
        elif fmt == "pcm16":
            packets = _talk.send_pcm16(data)
        else:
            return _err(400, "bad_format",
                        "format must be 'pcm16' or 'g711a'")
    except (OSError, DVRIPError) as exc:
        return _err(503, "camera_unreachable",
                    f"talk send failed: {exc}")
    except ValueError as exc:
        return _err(400, "bad_audio", str(exc))
    return JSONResponse({"packets_sent": packets,
                         "bytes_received": len(data),
                         "talk": _talk.status()})


@app.post("/api/audio/talk/stop")
def talk_stop() -> dict:
    _talk.stop()
    return _talk.status()


# -- microphone (listen) ---------------------------------------------------

@app.get("/api/audio/mic")
async def mic_stream(request: Request, format: str = "pcm16"):
    """Stream the camera microphone audio (chunked HTTP).

    format=pcm16 (default): raw little-endian PCM16 8 kHz mono,
    Content-Type audio/L16;rate=8000;channels=1.
    format=wav: same PCM with a 44-byte WAV header (data size
    0xFFFFFFFF, streaming-style).
    format=g711a: raw G.711 A-law bytes as received from the camera.
    The mic session starts on first request and stops automatically
    when the client disconnects (or via POST /api/audio/mic/stop).
    """
    fmt = format.lower()
    if fmt not in ("pcm16", "wav", "g711a"):
        return _err(400, "bad_format",
                    "format must be 'pcm16', 'wav' or 'g711a'")
    try:
        _mic.start()
    except (OSError, DVRIPError) as exc:
        return _err(503, "camera_unreachable",
                    f"mic start failed: {exc}")

    async def generate():
        # try/finally: uvicorn CANCELS this generator when the client
        # disconnects (it does not wait for is_disconnected() to be
        # polled), so the session stop must live in finally, not after
        # the loop.
        try:
            if fmt == "wav":
                yield _wav_header(8000, 1, 16)
            while _mic.running:
                if await request.is_disconnected():
                    break
                # Read in a thread so the event loop is not blocked.
                chunk = await asyncio.to_thread(_mic.read_chunk, 1.0)
                if chunk is None:
                    continue  # timeout: re-check disconnect, keep waiting
                if fmt == "g711a":
                    # The mic session only keeps PCM; transcode back to
                    # A-law for the raw G.711 stream.
                    from . import g711 as _g711
                    yield _g711.alaw_encode(chunk)
                else:
                    yield chunk
        finally:
            _mic.stop()

    media = {"pcm16": "audio/L16;rate=8000;channels=1",
             "wav": "audio/wav",
             "g711a": "audio/PCMA;rate=8000"}[fmt]
    return StreamingResponse(generate(), media_type=media)


def _wav_header(rate: int, channels: int, bits: int) -> bytes:
    """44-byte WAV header with unknown data size (streaming)."""
    import struct
    byte_rate = rate * channels * bits // 8
    block_align = channels * bits // 8
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", 0xFFFFFFFF, b"WAVE", b"fmt ", 16, 1, channels,
        rate, byte_rate, block_align, bits, b"data", 0xFFFFFFFF)


@app.post("/api/audio/mic/stop")
def mic_stop() -> dict:
    _mic.stop()
    return _mic.status()


# -- button-press / alarm events (DVRIP push, LAN) -------------------------

@app.post("/api/events/start")
def events_start() -> Response:
    """Subscribe to the camera's alarm push (opcode 1500).

    After this, the camera pushes AlarmInfo (1504) on its own whenever
    an alarm fires -- e.g. (inferred, not proven on the RPO V1) a
    LocalAlarm when the button on the camera is pressed. Use
    GET /api/events or the SSE stream to see what actually arrives.
    """
    try:
        _events.start()
    except (OSError, DVRIPError) as exc:
        return _err(503, "camera_unreachable",
                    f"events start failed: {exc}")
    return JSONResponse(_events.status())


@app.post("/api/events/stop")
def events_stop() -> dict:
    _events.stop()
    return _events.status()


@app.get("/api/events")
def events_list(limit: int = 50) -> dict:
    """Buffered alarm events received so far (chronological order).

    Each event carries the raw ``alarm`` dict exactly as the camera
    sent it, plus ``event`` (its Event string), ``received_at`` and
    ``possible_button_press`` (True only for LocalAlarm -- an
    inference, see app/events.py).
    """
    return {"running": _events.running,
            "events_received": _events.events_received,
            "events": _events.recent(limit)}


@app.get("/api/events/stream")
async def events_stream():
    """Server-sent events: new alarm events as they arrive.

    The stream only produces events while the session is running --
    start it first with POST /api/events/start. Send a ``: heartbeat``
    comment every 15 s so proxies do not close an idle stream.
    When the client disconnects, uvicorn cancels this generator (the
    same mechanism as the mic stream); no is_disconnected() poll is
    used here, because awaiting it blocks the loop until a disconnect
    message arrives and stalls delivery/teardown.
    """
    if not _events.running:
        return _err(409, "events_not_started",
                    "call POST /api/events/start first")

    async def generate():
        # Start after the newest buffered event: the stream only
        # carries events that arrive after the client connects.
        _, last_seq = _events.events_since(0)
        yield ": connected\n\n"
        last_beat = time.time()
        while _events.running:
            new, last_seq = await asyncio.to_thread(
                _events.events_since, last_seq)
            for event in new:
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            if not new:
                if time.time() - last_beat >= 15:
                    yield ": heartbeat\n\n"
                    last_beat = time.time()
                await asyncio.sleep(0.5)

    return StreamingResponse(generate(), media_type="text/event-stream")


# -- screen and speaker as general-purpose outputs ---------------------------

async def _json_body(request: Request) -> dict:
    try:
        body = json.loads(await request.body() or b"{}")
    except ValueError:
        raise ValueError("request body is not valid JSON")
    if not isinstance(body, dict):
        raise ValueError("request body must be a JSON object")
    return body


@app.on_event("startup")
def _startup() -> None:
    _screen.load()                      # saved default screen + the once-a-second ticker


@app.post("/api/screen/show")
async def screen_show(request: Request) -> Response:
    """Show a source on the camera screen.

    Body: {"source": "<file|http(s) URL|rtsp://...|image>", "loop": false,
    "after": "off"|"hold", "owner": "api", "ttl": seconds}. This pushes an EVENT LAYER: the newest
    layer is on the screen; when it ends the layer below (or the default screen) shows again.
    ``after``: "off" = the layer ends when the source ends (default), "hold" = keep the last frame.
    ``ttl`` ends the layer after that many seconds; ``owner`` names it (a new show with the same
    owner replaces it; see /api/screen/release).
    """
    try:
        body = await _json_body(request)
        source = body.get("source")
        if not isinstance(source, str) or not source:
            return _err(400, "bad_source", "field 'source' is required")
        status = await asyncio.to_thread(
            _screen.show, source, bool(body.get("loop", False)),
            str(body.get("after", "off")), str(body.get("owner", "api")), body.get("ttl"))
    except ValueError as exc:
        return _err(400, "bad_params", str(exc))
    except (OSError, DVRIPError) as exc:
        return _err(503, "camera_unreachable", f"screen show failed: {exc}")
    except RuntimeError as exc:
        return _err(500, "ffmpeg_failed", str(exc))
    return JSONResponse(status)


@app.post("/api/screen/text")
async def screen_text(request: Request) -> Response:
    """Show text: {"title": "...", "messages": [{"role": "user|assistant|
    caller|info", "text": "..."}], "font_size": 15, "after": "hold"}.
    Good for an Assist conversation or a notice; send again to update it."""
    try:
        body = await _json_body(request)
        messages = body.get("messages", [])
        if not isinstance(messages, list):
            return _err(400, "bad_params", "messages must be a list")
        status = await asyncio.to_thread(
            _screen.show_text, str(body.get("title", "")), messages,
            int(body.get("font_size", 15)), str(body.get("after", "hold")),
            str(body.get("owner", "api")), body.get("ttl"))
    except (ValueError, TypeError) as exc:
        return _err(400, "bad_params", str(exc))
    except (OSError, DVRIPError) as exc:
        return _err(503, "camera_unreachable", f"screen text failed: {exc}")
    except RuntimeError as exc:
        return _err(500, "ffmpeg_failed", str(exc))
    return JSONResponse(status)


@app.post("/api/screen/release")
async def screen_release(request: Request) -> Response:
    """End the event layer of one owner: {"owner": "zalo_call"}."""
    try:
        body = await _json_body(request)
        owner = body.get("owner")
        if not isinstance(owner, str) or not owner:
            return _err(400, "bad_params", "field 'owner' is required")
        return JSONResponse(await asyncio.to_thread(_screen.release, owner))
    except ValueError as exc:
        return _err(400, "bad_params", str(exc))
    except (OSError, DVRIPError) as exc:
        return _err(503, "camera_unreachable", f"screen release failed: {exc}")


@app.post("/api/screen/default")
async def screen_default(request: Request) -> Response:
    """Set what the screen shows when no event layer is active:
    {"mode": "off"|"clock"|"image"|"video"|"live", "source": "<url/path, for image|video|live>"}.
    Saved (STATE_DIR) and applied again after a restart. "off" lets the screen close."""
    try:
        body = await _json_body(request)
        status = await asyncio.to_thread(_screen.set_default, str(body.get("mode", "")),
                                         str(body.get("source") or ""))
    except ValueError as exc:
        return _err(400, "bad_params", str(exc))
    except (OSError, DVRIPError) as exc:
        return _err(503, "camera_unreachable", f"screen default failed: {exc}")
    except RuntimeError as exc:
        return _err(500, "ffmpeg_failed", str(exc))
    return JSONResponse(status)


@app.post("/api/screen/off")
async def screen_off() -> dict:
    """End EVERY event layer: the default screen shows again (or the screen closes if the default is off)."""
    return await asyncio.to_thread(_screen.off)


@app.get("/api/screen/status")
def screen_status() -> dict:
    return _screen.status()


@app.post("/api/screen/power")
async def screen_power(request: Request) -> Response:
    """Switch the camera LCD on/off: {"on": true}. The app's "Nguồn điện màn hình" Bật/Tắt."""
    try:
        body = await _json_body(request)
        status = await asyncio.to_thread(_screen.power, body.get("on"))
    except ValueError as exc:
        return _err(400, "bad_params", str(exc))
    except (OSError, DVRIPError) as exc:
        return _err(503, "camera_unreachable", f"screen power failed: {exc}")
    return JSONResponse(status)


@app.get("/api/screen/sleep")
async def screen_sleep_get() -> Response:
    """How long the camera LCD stays lit: {"work_mode", "work_time" (s, 0 = never), "luminance"}."""
    try:
        return JSONResponse(await asyncio.to_thread(_screen.get_sleep))
    except (OSError, DVRIPError) as exc:
        return _err(503, "camera_unreachable", f"screen sleep read failed: {exc}")


@app.post("/api/screen/sleep")
async def screen_sleep_set(request: Request) -> Response:
    """Set how long the camera LCD stays lit and/or its brightness:
    {"seconds": 0..3600 (0 = never sleeps), "luminance": 1..100}; give either or both."""
    try:
        body = await _json_body(request)
        status = await asyncio.to_thread(_screen.set_sleep, body.get("seconds"), body.get("luminance"))
    except ValueError as exc:
        return _err(400, "bad_params", str(exc))
    except (OSError, DVRIPError) as exc:
        return _err(503, "camera_unreachable", f"screen sleep set failed: {exc}")
    return JSONResponse(status)


@app.post("/api/speaker/play")
async def speaker_play(request: Request) -> Response:
    """Play audio out of the camera speaker: {"source": "<file|URL|lavfi:..>",
    "volume": 1.0}. A new play replaces the current one."""
    try:
        body = await _json_body(request)
        source = body.get("source")
        if not isinstance(source, str) or not source:
            return _err(400, "bad_source", "field 'source' is required")
        status = await asyncio.to_thread(
            _speaker.play, source, float(body.get("volume", 1.0)))
    except (ValueError, TypeError) as exc:
        return _err(400, "bad_params", str(exc))
    except (OSError, DVRIPError) as exc:
        return _err(503, "camera_unreachable", f"speaker play failed: {exc}")
    except RuntimeError as exc:
        return _err(500, "ffmpeg_failed", str(exc))
    return JSONResponse(status)


@app.post("/api/speaker/play_bytes")
async def speaker_play_bytes(request: Request, volume: float = 1.0) -> Response:
    """Play audio sent in the request body (any container ffmpeg reads, e.g. a TTS WAV):
    POST the bytes, ``?volume=1.0``. Used when the audio only exists inside HA (Assist replies)."""
    try:
        status = await asyncio.to_thread(_speaker.play_bytes, await request.body(), volume)
    except (ValueError, TypeError) as exc:
        return _err(400, "bad_params", str(exc))
    except (OSError, DVRIPError) as exc:
        return _err(503, "camera_unreachable", f"speaker play failed: {exc}")
    except RuntimeError as exc:
        return _err(500, "ffmpeg_failed", str(exc))
    return JSONResponse(status)


@app.post("/api/speaker/stop")
async def speaker_stop() -> dict:
    return await asyncio.to_thread(_speaker.stop)


@app.get("/api/speaker/status")
def speaker_status() -> dict:
    return _speaker.status()


# -- video to the camera screen (dry-run) -----------------------------------

@app.post("/api/video/start")
async def video_start(request: Request) -> Response:
    """Start the video pipeline: show ``source`` on the camera screen.

    Add ``"send": true`` to actually send the video to the camera screen
    (AVTalk over DVRIP, decoded from a real iCSee call -- app/avtalk.py).
    Without it the pipeline is a dry run: transcode and count, send nothing.

    Body (JSON): {"source": "rtsp://<lan-ip>:554/...", "width": 1920,
    "height": 1080, "fps": 30, "codec": "H265"} -- width/height/fps/codec
    are optional; when all are omitted the service tries the DecoderPram
    negotiation, then falls back to 864x480 / fps 10 / H264.
    The pipeline decodes the source with FFmpeg to YUV420P at the
    negotiated resolution/fps and counts frames. It does NOT send anything
    to the camera screen yet: the DVRIP encapsulation of Dev_SendMsg
    11004/11006 is still unknown (see video_sender.WIRE_BLOCKER), so the
    response always reports video_send.implemented=false.
    """
    try:
        body = json.loads(await request.body()) if await request.body() else {}
    except ValueError:
        return _err(400, "bad_json", "request body is not valid JSON")
    source = body.get("source", "")
    if not isinstance(source, str) or not source:
        return _err(400, "bad_source",
                    "field 'source' is required, e.g. "
                    "rtsp://192.168.1.10:554/user=admin&password=xxxx"
                    "&channel=1&stream=0.sdp?real_stream")
    try:
        status = _video.start(source,
                              width=body.get("width"),
                              height=body.get("height"),
                              fps=body.get("fps"),
                              codec=body.get("codec"),
                              send=bool(body.get("send", False)))
    except (OSError, DVRIPError) as exc:
        return _err(503, "camera_unreachable",
                    f"video start failed: {exc}")
    except RuntimeError as exc:
        return _err(409, "video_already_running", str(exc))
    except ValueError as exc:
        return _err(400, "bad_params", str(exc))
    return JSONResponse(status)


@app.post("/api/video/stop")
def video_stop() -> dict:
    _video.stop()
    return _video.status()


@app.get("/api/video/status")
def video_status() -> dict:
    return _video.status()


@app.post("/api/video/control")
async def video_control(request: Request) -> Response:
    """AV-talk control (pause/continue/resolution/reset) -- 501.

    The control JSON builders are complete (video_sender), but sending
    requires the same unknown 11004 wire encapsulation as the video
    frames, so this endpoint honestly refuses until the capture is done.
    """
    try:
        body = json.loads(await request.body()) if await request.body() else {}
    except ValueError:
        return _err(400, "bad_json", "request body is not valid JSON")
    command = body.get("command", "")
    try:
        ctrl_json = video_sender.build_control_json(
            command, int(body.get("width", 0)), int(body.get("height", 0)))
    except ValueError as exc:
        return _err(400, "bad_command", str(exc))
    return _err(501, "video_send_not_implemented",
                f"control {command!r} would send {ctrl_json}, but the "
                f"AV-talk wire protocol is not implemented. "
                f"{video_sender.WIRE_BLOCKER}")
