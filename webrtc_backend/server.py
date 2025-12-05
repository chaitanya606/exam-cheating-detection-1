# webrtc_backend/server.py
import asyncio
import json
import logging
import os
import sys
from datetime import datetime

from aiohttp import web
from aiortc import RTCPeerConnection, RTCSessionDescription

# Add project root to sys.path so we can import src/* detectors
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from screen_event_recorder import ScreenEventRecorder
from detection_integration import run_detection_pipeline

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webrtc_server")

STATIC = os.path.join(CURRENT_DIR, "static")
pcs = set()  # Peer connections

# GLOBAL ScreenEventRecorder
screen_recorder = ScreenEventRecorder(
    buffer_size=5,
    after_frames=10,
    base_dir=os.path.join(PROJECT_ROOT, "recordings", "screen_events")
)


class VideoConsumer:
    """
    Receives WebRTC video frames and sends them to the detection pipeline.
    """
    def __init__(self, track, kind, pc_id, data_channel=None):
        self.track = track
        self.kind = kind        # 'camera' or 'screen'
        self.pc_id = pc_id
        self.data_channel = data_channel
        self._task = asyncio.create_task(self._run())

    async def _run(self):
        logger.info("[%s] VideoConsumer started for %s", self.pc_id, self.kind)
        try:
            while True:
                frame = await self.track.recv()  # aiortc VideoFrame

                # Convert to OpenCV BGR frame
                bgr = frame.to_ndarray(format="bgr24")

                # Timestamp
                ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")

                # If screen frame → add to rolling buffer
                if self.kind == "screen":
                    try:
                        screen_recorder.add_frame(bgr)
                    except Exception as e:
                        logger.error("Screen recorder error: %s", e)

                # Call your detector pipeline (async)
                asyncio.create_task(
                    run_detection_pipeline(
                        bgr,
                        kind=self.kind,
                        timestamp=ts,
                        data_channel=self.data_channel
                    )
                )

        except asyncio.CancelledError:
            logger.info("[%s] Consumer cancelled", self.pc_id)
        except Exception as e:
            logger.exception("[%s] Error in VideoConsumer: %s", self.pc_id, e)


async def index(request):
    """Serve exam UI."""
    with open(os.path.join(STATIC, "index.html"), "r", encoding="utf-8") as f:
        return web.Response(text=f.read(), content_type="text/html")


async def offer(request):
    """
    Handle incoming SDP offer and return the WebRTC answer.
    """
    params = await request.json()
    offer = RTCSessionDescription(params["sdp"], params["type"])

    pc = RTCPeerConnection()
    pcs.add(pc)
    pc_id = f"PC-{id(pc)}"

    logger.info("%s - New RTCPeerConnection", pc_id)

    pc._video_consumers = []
    pc._data_channels = []

    # DATA CHANNEL (for sending violations)
    @pc.on("datachannel")
    def on_datachannel(channel):
        logger.info("%s - DataChannel received: %s", pc_id, channel.label)
        pc._data_channels.append(channel)

        @channel.on("message")
        def on_message(message):
            logger.info("%s - DataChannel message: %s", pc_id, message)
            try:
                msg = json.loads(message)
            except:
                return

            if msg.get("cmd") == "trigger_screen_save":
                screen_recorder.trigger_save("MANUAL_TRIGGER")
                channel.send(json.dumps({"type": "ACK", "detail": "screen_saved"}))

    # VIDEO TRACK HANDLER
    @pc.on("track")
    def on_track(track):
        logger.info("%s - Track received: %s (%s)", pc_id, track.id, track.kind)

        if track.kind == "video":
            # First video track → camera, second → screen
            idx = len(pc._video_consumers)
            kind = "camera" if idx == 0 else "screen"

            # Pass the DataChannel if present
            dc = pc._data_channels[0] if pc._data_channels else None

            consumer = VideoConsumer(track, kind=kind, pc_id=pc_id, data_channel=dc)
            pc._video_consumers.append(consumer)

        @track.on("ended")
        async def on_ended():
            logger.info("%s - Track %s ended", pc_id, track.id)

    # Finalize SDP exchange
    await pc.setRemoteDescription(offer)
    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    logger.info("%s - Answer ready", pc_id)
    return web.json_response({
        "sdp": pc.localDescription.sdp,
        "type": pc.localDescription.type
    })


async def on_shutdown(app):
    logger.info("Shutting down WebRTC server...")
    await asyncio.gather(*[pc.close() for pc in pcs])
    pcs.clear()


# -----------------------------
# START WEB SERVER
# -----------------------------
app = web.Application()
app.router.add_get("/", index)
app.router.add_post("/offer", offer)
app.router.add_static("/static/", STATIC, show_index=True)
app.on_shutdown.append(on_shutdown)

if __name__ == "__main__":
    web.run_app(app, port=8080)
