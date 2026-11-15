"""Fake XM/DVRIP camera for local testing of icsee-docker (NOT shipped).

Listens on 127.0.0.1:34567 and implements just enough of the DVRIP
protocol (per go2rtc pkg/dvrip) to exercise the service:
- login (1000) -> Ret 100, SessionID 0x00000001, AliveInterval 20
- KeepAlive (1006) -> Ret 100
- config get (1042/1360) -> DecoderPram H265 1920x1080@30 for name
  "DecoderPram", else Ret 100
- OPMonitor Claim (1413) -> Ret 100; Start (1410) -> streams fake media
  packets: audio 0xFA (G.711 A-law 8 kHz sine wave, 320 bytes/packet,
  one packet per 40 ms) + one video I-frame 0xFC (H265 1920x1080) first
- OPTalk Claim (1434) -> Ret 100; Start (1430) -> Ret 100
- OPTalkData (1432) -> payloads appended to /tmp/fake-cam-talk.bin
- AVTalk claim (1417) -> Ret 100 on 1418, start (1415) -> 1416; media (1419) logged, no reply
- AlarmSet (1500) -> Ret 100, then pushes two AlarmInfo (1504):
  LocalAlarm + VideoMotion

Usage: python3 /tmp/fake-cam.py  (Ctrl-C to stop)
"""

import json
import math
import socket
import struct
import sys
import threading
import time

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import load_core  # noqa: E402

load_core.load()
from dosen_core import g711  # noqa: E402

HOST, PORT = "127.0.0.1", 34567
SCREEN_WORK = {"WorkMode": "Timing", "WorkTime": 30, "WorkLuminance": 60}   # Dev.ScreenWorkMode
CONFIGS = {"fVideo.Volume": [{"AudioMode": "Single", "LeftVolume": 50, "RightVolume": 50}],
           "fVideo.VolumeIn": [{"AnrOpen": True, "AudioMode": "Single", "LeftVolume": 50, "RightVolume": 50}],
           "Consumer.CommDevCfg": {"CheckX1Remote": False, "KeyAudioTip": True}}
TALK_LOG = "/tmp/fake-cam-talk.bin"
AVTALK_LOG = "/tmp/fake-cam-avtalk.log"   # claim JSON + one line per media packet


def chunk(opcode, payload, session=1, seq=0):
    header = struct.pack("<BB2xII2xHI", 0xFF, 0x00, session, seq,
                         opcode, len(payload))
    return header + payload


def json_chunk(opcode, obj, session=1, seq=0):
    return chunk(opcode, json.dumps(obj).encode() + b"\n\x00",
                 session, seq)


def read_exact(conn, n):
    data = bytearray()
    while len(data) < n:
        part = conn.recv(n - len(data))
        if not part:
            raise ConnectionError("closed")
        data.extend(part)
    return bytes(data)


def read_cmd(conn):
    header = read_exact(conn, 20)
    opcode = struct.unpack_from("<H", header, 14)[0]
    length = struct.unpack_from("<I", header, 16)[0]
    payload = read_exact(conn, length) if length else b""
    return opcode, payload


def sine_g711():
    """One 40 ms packet (320 samples) of G.711 A-law sine wave."""
    pcm = b"".join(
        int(12000 * math.sin(2 * math.pi * 440 * i / 8000)).to_bytes(
            2, "little", signed=True) for i in range(320))
    return g711.alaw_encode(pcm)


def monitor_stream(send, session):
    # One fake H.265 I-frame packet first (1920x1080 @30fps).
    video = b"\x00\x00\x00\x01\x40\x01" * 40
    iframe = (b"\x00\x00\x01\xfc" + bytes([0x03, 30, 240, 135])
              + struct.pack("<II", 1000, len(video)) + video)
    send(chunk(1410, iframe, session))
    audio = sine_g711()
    while True:
        packet = (b"\x00\x00\x01\xfa" + bytes([14, 2])
                  + struct.pack("<H", len(audio)) + audio)
        send(chunk(1410, packet, session))
        time.sleep(0.04)


def alarm_stream(send, session):
    """Push two fake AlarmInfo (1504) events after a 1500 subscribe:
    a LocalAlarm (the inferred button-press event) and a VideoMotion
    (not a button press), so tests can check the classification."""
    local = {"Name": "AlarmInfo",
             "AlarmInfo": {"Event": "LocalAlarm", "Channel": 0,
                           "StartTime": "2026-11-14 16:45:00",
                           "Status": "Start"},
             "SessionID": "0x00000001"}
    motion = {"Name": "AlarmInfo",
              "AlarmInfo": {"Event": "VideoMotion", "Channel": 0,
                            "StartTime": "2026-11-14 16:45:01",
                            "Status": "Start"},
              "SessionID": "0x00000001"}
    send(json_chunk(1504, local, session))
    time.sleep(0.1)
    send(json_chunk(1504, motion, session))


def handle(conn, addr):
    session = 1
    seq = 0
    send_lock = threading.Lock()

    def send(data):
        with send_lock:
            conn.sendall(data)
    monitor_thread = None
    try:
        while True:
            opcode, payload = read_cmd(conn)
            if opcode == 1000:  # login
                send(json_chunk(
                    1000, {"Ret": 100, "SessionID": "0x00000001",
                           "AliveInterval": 20, "DeviceType": "FAKE-CAM"},
                    session, seq))
            elif opcode == 1006:  # keepalive
                send(json_chunk(1006, {"Ret": 100}, session, seq))
            elif opcode == 1450:  # OPMachine: LCD power switch (OPScreenSwitchControl)
                req = json.loads(payload[:-2])
                with open(AVTALK_LOG, "a") as f:
                    f.write("power %s\n" % json.dumps(req.get("OPScreenSwitchControl")))
                send(json_chunk(1450, {"Name": req.get("Name"), "Ret": 100}, session, seq))
            elif opcode == 1040:  # config set
                req = json.loads(payload[:-2])
                if req.get("Name") == "Dev.ScreenWorkMode":
                    SCREEN_WORK.update(req["Dev.ScreenWorkMode"])
                elif req.get("Name") in CONFIGS:
                    CONFIGS[req["Name"]] = req[req["Name"]]
                send(json_chunk(1040, {"Name": req.get("Name"), "Ret": 100}, session, seq))
            elif opcode == 1042 and json.loads(payload[:-2]).get("Name") in CONFIGS:
                name_ = json.loads(payload[:-2])["Name"]
                send(json_chunk(1042, {"Name": name_, name_: CONFIGS[name_], "Ret": 100}, session, seq))
            elif opcode == 1042 and json.loads(payload[:-2]).get("Name") == "Dev.ScreenWorkMode":
                send(json_chunk(1042, {"Name": "Dev.ScreenWorkMode", "Dev.ScreenWorkMode": dict(SCREEN_WORK),
                                       "Ret": 100}, session, seq))
            elif opcode in (1042, 1360):  # config get
                try:
                    req = json.loads(payload[:-2])
                except ValueError:
                    req = {}
                name = req.get("Name", "")
                if name == "DecoderPram":
                    send(json_chunk(opcode, {
                        "Ret": 100,
                        "DecoderPram": {
                            "Video": [{"Enc": "H265",
                                       "Res": [{"W": 1920, "H": 1080,
                                                "FPS": 30}]}],
                            "Audio": [{"Enc": "G711A", "SR": [8000]}]}},
                        session, seq))
                else:
                    send(json_chunk(opcode, {"Ret": 100},
                                            session, seq))
            elif opcode == 1413:  # OPMonitor Claim
                send(json_chunk(1413, {"Ret": 100}, session, seq))
            elif opcode == 1410:  # OPMonitor Start -> stream media
                monitor_thread = threading.Thread(
                    target=monitor_stream, args=(send, session), daemon=True)
                monitor_thread.start()
            elif opcode == 1434:  # OPTalk Claim
                send(json_chunk(1434, {"Ret": 100}, session, seq))
            elif opcode == 1430:  # OPTalk Start
                send(json_chunk(1430, {"Ret": 100}, session, seq))
            elif opcode == 1432:  # OPTalkData
                with open(TALK_LOG, "ab") as f:
                    f.write(payload)
            elif opcode == 1415:  # AVTalk start (control connection) -> 1416
                with open(AVTALK_LOG, "a") as f:
                    f.write("start %s\n" % payload[:-2].decode())
                send(json_chunk(1416, {"AVTalk": {"Channel": 0}, "Ret": 100},
                                session, seq))
            elif opcode == 1417:  # AVTalk claim -> 1418 Ret 100
                with open(AVTALK_LOG, "a") as f:
                    f.write("claim %s\n" % payload[:-2].decode())
                send(json_chunk(1418, {"Ret": 100}, session, seq))
            elif opcode == 1419:  # AVTalk media: no reply (like the camera)
                with open(AVTALK_LOG, "a") as f:
                    f.write("media %s %d\n" % (payload[:16].hex(), len(payload)))
            elif opcode == 1500:  # AlarmSet -> Ret 100, then push 1504s
                send(json_chunk(1500, {"Ret": 100}, session, seq))
                threading.Thread(target=alarm_stream, args=(send, session),
                                 daemon=True).start()
            else:
                send(json_chunk(opcode, {"Ret": 100}, session, seq))
            seq += 1
    except (ConnectionError, OSError, struct.error):
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


def main():
    import os
    for path in (TALK_LOG, AVTALK_LOG):
        if os.path.exists(path):
            os.remove(path)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if os.environ.get("FAKE_RCVBUF"):                       # bộ đệm nhận nhỏ: camera «chết» (SIGSTOP) làm cửa sổ TCP đóng nhanh như camera mất nguồn thật
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, int(os.environ["FAKE_RCVBUF"]))
    srv.bind((HOST, PORT))
    srv.listen(8)
    print(f"fake DVRIP camera listening on {HOST}:{PORT}", flush=True)
    while True:
        conn, addr = srv.accept()
        threading.Thread(target=handle, args=(conn, addr),
                         daemon=True).start()


if __name__ == "__main__":
    main()
