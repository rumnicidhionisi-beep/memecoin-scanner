"""PumpPortal free WebSocket: every new pump.fun token, the second it is created."""
import asyncio
import json
import logging

import websockets

log = logging.getLogger("scanner")
WS_URL = "wss://pumpportal.fun/api/data"


async def stream(on_new_token, duration_s: float | None = None):
    """Listen until `duration_s` seconds pass (None = forever). Reconnects on errors."""
    loop = asyncio.get_running_loop()
    end = loop.time() + duration_s if duration_s else None
    backoff = 2
    while end is None or loop.time() < end:
        try:
            async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20) as ws:
                await ws.send(json.dumps({"method": "subscribeNewToken"}))
                log.info("PumpPortal connected (new tokens)")
                backoff = 2
                while end is None or loop.time() < end:
                    timeout = None if end is None else max(0.1, end - loop.time())
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
                    except asyncio.TimeoutError:
                        return
                    try:
                        msg = json.loads(raw)
                    except ValueError:
                        continue
                    if isinstance(msg, dict) and msg.get("mint") and msg.get("txType") == "create":
                        try:
                            on_new_token(msg)
                        except Exception:  # never let one bad message kill the stream
                            log.exception("new token handler failed")
        except (OSError, websockets.WebSocketException) as e:
            log.warning("PumpPortal disconnected: %s — retry in %ss", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
