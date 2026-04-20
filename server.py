"""
server.py — FastAPI WebSocket server for the haunted mansion game.

Each WebSocket connection gets its own game session. The synchronous LangGraph
engine runs in a ThreadPoolExecutor. WebSocketContext bridges it to the async
handler via an asyncio.Queue (outbound) and a threading.Queue (inbound).

Server → client message types:
  {"type": "message",  "text": "..."}  — game output line
  {"type": "prompt",   "text": "..."}  — input prompt label (e.g. "You: ")
  {"type": "game_over"}                — session ended

Client → server:
  plain text string — player command
"""

import asyncio
import json as json_lib
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from io_context import WebSocketContext, DisconnectedError
from main import app as game_app, make_initial_state, set_io
from npc_memory import clear_all_memories

executor = ThreadPoolExecutor(max_workers=10)
fastapi_app = FastAPI()

# Mount static files directory
fastapi_app.mount("/static", StaticFiles(directory="static"), name="static")


def run_game(ctx: WebSocketContext) -> None:
    """Synchronous game loop — runs in a worker thread."""
    token = set_io(ctx)
    try:
        clear_all_memories()
        game_app.invoke(make_initial_state())
    except DisconnectedError:
        pass
    except Exception as e:
        ctx.send(f"\n[Server error: {e}]")
    finally:
        from main import _io_var
        _io_var.reset(token)
        ctx.close()


@fastapi_app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    loop = asyncio.get_event_loop()
    ctx = WebSocketContext(loop)

    game_future = loop.run_in_executor(executor, run_game, ctx)

    # Maps prefix → (ws_type, has_json_payload)
    _DISPATCH: dict[str, tuple[str, bool]] = {
        "__prompt__":        ("prompt",          False),
        "__statejson__":     ("state",            True),
        "__encounter_start__": ("encounter_start", True),
        "__encounter_end__": ("encounter_end",    False),
        "__encounter_state__": ("encounter_state", True),
        "__shop_data__":     ("shop_data",        True),
        "__roomfeatures__":  ("room_features",    True),
    }

    async def forward_output() -> None:
        """Drain the game's outbox and send to client."""
        while True:
            msg = await ctx.outbox.get()
            if msg is None:
                await websocket.send_json({"type": "game_over"})
                break
            for prefix, (ws_type, has_json) in _DISPATCH.items():
                if msg.startswith(prefix):
                    payload = msg[len(prefix):]
                    if has_json:
                        await websocket.send_json({"type": ws_type, **json_lib.loads(payload)})
                    elif payload:
                        await websocket.send_json({"type": ws_type, "text": payload})
                    else:
                        await websocket.send_json({"type": ws_type})
                    break
            else:
                await websocket.send_json({"type": "message", "text": msg})

    async def forward_input() -> None:
        """Forward player commands to the game thread."""
        try:
            while True:
                text = await websocket.receive_text()
                ctx.inbox.put(text)
        except WebSocketDisconnect:
            ctx.close()

    await asyncio.gather(forward_output(), forward_input())
    await asyncio.wrap_future(game_future)


@fastapi_app.get("/rooms")
async def get_rooms() -> JSONResponse:
    with open("data/rooms.json", "r") as f:
        return JSONResponse(json_lib.load(f))


@fastapi_app.get("/")
async def index() -> FileResponse:
    return FileResponse("static/index.html")
