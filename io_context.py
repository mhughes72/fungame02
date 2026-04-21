# io_context.py
# Defines the IOContext protocol for I/O abstraction.
# Allows the game engine to be decoupled from CLI-specific print/input,
# enabling future web UI or other frontends to implement their own adapters.

import asyncio
import queue
from typing import Protocol


class IOContext(Protocol):
    def send(self, message: str) -> None: ...
    def recv(self, prompt: str = "") -> str: ...
    def stream(self, chunks) -> str: ...


class CLIContext:
    def send(self, message: str) -> None:
        if message.startswith("__"):
            return
        print(message, flush=True)

    def recv(self, prompt: str = "\nWhat do you do? ") -> str:
        print(prompt, end="", flush=True)
        return input().strip().lower()

    def stream(self, chunks) -> str:
        full = ""
        for chunk in chunks:
            text = chunk.content if hasattr(chunk, "content") else str(chunk)
            if text:
                print(text, end="", flush=True)
                full += text
        print()
        return full


class WebSocketContext:
    """IOContext for the FastAPI/WebSocket layer.

    outbox: asyncio.Queue — game thread pushes via loop.call_soon_threadsafe;
            async handler awaits it directly (no thread blocking).
    inbox:  threading.Queue — game thread blocks on .get();
            async handler puts player input with .put().
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self.outbox: asyncio.Queue[str | None] = asyncio.Queue()
        self.inbox: queue.Queue[str | None] = queue.Queue()

    def send(self, message: str) -> None:
        self._loop.call_soon_threadsafe(self.outbox.put_nowait, message)

    def stream(self, chunks) -> str:
        full = ""
        for chunk in chunks:
            text = chunk.content if hasattr(chunk, "content") else str(chunk)
            if text:
                self._loop.call_soon_threadsafe(self.outbox.put_nowait, f"__token__{text}")
                full += text
        self._loop.call_soon_threadsafe(self.outbox.put_nowait, "__token_end__")
        return full

    def recv(self, prompt: str = "") -> str:
        if prompt:
            self._loop.call_soon_threadsafe(self.outbox.put_nowait, f"__prompt__{prompt}")
        value = self.inbox.get()
        if value is None:
            raise DisconnectedError()
        return value

    def close(self) -> None:
        """Unblock any recv() call in the game thread and stop outbox draining."""
        self.inbox.put(None)
        self._loop.call_soon_threadsafe(self.outbox.put_nowait, None)


class DisconnectedError(Exception):
    """Raised inside the game thread when the WebSocket client disconnects."""
