"""Tiện ích dùng chung: che mật khẩu trong địa chỉ, đọc DecoderPram của camera."""

from __future__ import annotations

import re

_SECRET_QUERY = re.compile(r"(?i)((?:^|[?&/;])(?:password|passwd|pass|pwd)=)[^&\s]*")
_SECRET_USERINFO = re.compile(r"(?i)(://[^/@\s:]*:)[^/@\s]*@")


def redact_source(text: str) -> str:
    """Mask camera credentials in a source URL (or any text that quotes it):
    ``password=...`` style parameters and ``user:pass@host`` userinfo. The
    RTSP URL of an ICSee camera carries the password, and neither the API
    status nor an ffmpeg error message may hand it back."""
    text = _SECRET_QUERY.sub(r"\1***", text)
    return _SECRET_USERINFO.sub(r"\1***@", text)


def apply_decoder_pram(pram: dict | None) -> dict:
    """Negotiate video params from a DecoderPram reply (best effort).

    pram is the decoded DecoderPram dict (Video[0].Enc, Video[0].Res[0]
    .W/H/FPS, Audio[0].SR[]) or None. Returns
    {"codec", "width", "height", "fps", "negotiated": bool}.
    On any failure the iCSee app falls back to 864x480, fps 10, H264 --
    we do exactly the same.
    """
    fallback = {"codec": "H264", "width": 864, "height": 480,
                "fps": 10, "negotiated": False}
    if not isinstance(pram, dict):
        return fallback
    try:
        video = (pram.get("Video") or [{}])[0]
        enc = str(video.get("Enc") or "H264").upper()
        if enc not in ("H264", "H265"):
            enc = "H264"
        res = (video.get("Res") or [{}])[0]
        width = int(res.get("W") or 864)
        height = int(res.get("H") or 480)
        fps = int(res.get("FPS") or 10)
        if width <= 0 or height <= 0 or fps <= 0:
            return fallback
        return {"codec": enc, "width": width, "height": height,
                "fps": fps, "negotiated": True}
    except (TypeError, ValueError, IndexError, KeyError):
        return fallback
