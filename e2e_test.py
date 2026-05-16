"""
e2e_test.py — End-to-end WebSocket replay test for the Haunted Mansion win path.

Spins up the FastAPI server in a background thread, connects as a WebSocket
client, and replays a sequence of natural-language commands that should complete
the full win path. Asserts inventory and health state at key checkpoints.

Usage:
    python e2e_test.py              # run all steps, print report
    python e2e_test.py --verbose    # also print every server message
"""

import argparse
import asyncio
import json
import subprocess
import sys
import time
import threading
import traceback
from dataclasses import dataclass, field
from typing import Optional

import websockets

# ── Config ────────────────────────────────────────────────────────────────────

HOST = "localhost"
PORT = 8765          # separate port so we don't clash with the normal dev server
WS_URL = f"ws://{HOST}:{PORT}/ws"
SERVER_STARTUP_SECS = 12
CMD_TIMEOUT_SECS = 45   # max wait for the game to go quiet after one command
IDLE_QUIET_SECS = 1.5   # consider the server "done" after this many silent seconds

# ── Step definitions ──────────────────────────────────────────────────────────

@dataclass
class Step:
    """One player command + optional assertion."""
    command: str
    desc: str
    assert_has: list[str] = field(default_factory=list)   # items that must be in inventory
    assert_not: list[str] = field(default_factory=list)   # items that must NOT be in inventory
    assert_min_hp: Optional[int] = None                   # health floor
    assert_room: Optional[str] = None                     # expected room_id


WIN_PATH: list[Step] = [
    # ── Phase 1: The Study (room_1) ───────────────────────────────────────────
    Step("take rusty key",              "Pick up rusty key",
         assert_has=["rusty key"]),
    Step("take health potion",          "Take health potion from Study",
         assert_has=["health potion"]),
    Step("examine old book",            "Examine old book → secret letter auto-collected",
         assert_has=["secret letter"]),
    # Aldric conversation: enter → give letter → speak oath → exit
    Step("talk to aldric",              "Enter Aldric conversation"),
    Step("give him the secret letter",  "Give secret letter to Aldric"),
    Step("i speak the scholar's oath",  "Speak scholar's oath → iron key granted",
         assert_has=["iron key"]),
    Step("goodbye",                     "Exit Aldric conversation"),
    # Oracle conversation: enter → ask → exit
    Step("talk to the oracle",          "Enter Oracle conversation"),
    Step("ask about the rosetta stone", "Ask Oracle about Rosetta Stone → oracle's note granted",
         assert_has=["oracle's note"]),
    Step("goodbye",                     "Exit Oracle conversation"),

    # ── Phase 2: Hallway → Library (room_2 → room_4) ─────────────────────────
    Step("go north",             "Move to Hallway"),
    Step("go west",              "Move to Library"),
    # Take and equip fire poker BEFORE fighting shadow (fire = shadow weakness)
    Step("take fire poker",      "Pick up fire poker",
         assert_has=["fire poker"]),
    Step("equip fire poker",     "Equip fire poker"),
    Step("fight the shadow",     "Defeat library shadow with fire poker"),
    Step("examine ancient tome", "Examine ancient tome → reveals hidden note"),
    Step("take hidden note",     "Take hidden note",
         assert_has=["hidden note"]),

    # ── Phase 2b: Secret Room (room_7) ───────────────────────────────────────
    Step("go north",             "Move to Secret Room"),
    # Take and equip magic staff BEFORE fighting wraith (magic = wraith weakness)
    Step("take magic staff",     "Pick up magic staff",
         assert_has=["magic staff"]),
    Step("equip magic staff",    "Equip magic staff"),
    Step("fight the wraith",     "Defeat wraith with magic staff"),
    Step("take strange amulet",  "Take Strange Amulet (ritual 1)",
         assert_has=["strange amulet"]),

    # ── Phase 3: Return to Study → defeat ghost (room_4 → room_2 → room_1) ───
    Step("go south",             "Return to Library"),
    Step("go east",              "Return to Hallway"),
    Step("go south",             "Return to Study"),
    Step("use health potion",    "Heal up before ghost fight",
         assert_min_hp=50),
    Step("fight the ghost",      "Defeat ghost with magic staff",
         assert_min_hp=1),
    Step("take unmaking flame",  "Take Unmaking Flame (ritual 2) — auto-collected by combat drop",
         assert_has=["unmaking flame"]),

    # ── Phase 4: Hallway → Grand Foyer → Dining Hall (room_2 → room_5 → room_8)
    Step("go north",             "Move to Hallway"),
    Step("go north",             "Unlock/enter Grand Foyer (rusty key)"),
    Step("go north",             "Move to Dining Hall"),
    Step("open dusty lockbox",   "Open lockbox — health potion revealed"),
    Step("take health potion",   "Take health potion from lockbox"),
    Step("use health potion",    "Heal up before vampire fight",
         assert_min_hp=80),
    Step("fight the vampire",    "Defeat vampire with magic staff",
         assert_min_hp=1),

    # ── Phase 4b: Dining Hall → Basement Stairs → Basement (room_10 → room_11)
    Step("go west",              "Move to Basement Stairs"),
    Step("take health potion",   "Take health potion from Basement Stairs"),
    Step("go down",              "Descend to Basement"),
    Step("fight the ghoul",      "Defeat ghoul"),
    Step("take health potion",   "Take health potion from Basement"),
    Step("use health potion",    "Heal before vault",
         assert_min_hp=70),

    # ── Phase 5: Vault (room_12) ─────────────────────────────────────────────
    Step("go north",             "Enter Lord Harwick's Vault"),
    Step("fight lord harwick",   "Defeat Lord Harwick Vane",
         assert_min_hp=1),
    Step("take blood pact",      "Take Blood Pact (ritual 3)",
         assert_has=["blood pact", "strange amulet", "unmaking flame"]),

    # ── Phase 6: Return to Grand Foyer → Ritual ──────────────────────────────
    Step("go south",             "Leave Vault to Basement"),
    Step("go up",                "Ascend to Basement Stairs"),
    Step("go east",              "Move to Dining Hall"),
    Step("go south",             "Move to Grand Foyer"),
    Step("ritual",               "Perform Ritual of Unmaking — WIN"),
]

# ── Result tracking ───────────────────────────────────────────────────────────

@dataclass
class StepResult:
    step: Step
    passed: bool
    failures: list[str] = field(default_factory=list)
    inventory: list[str] = field(default_factory=list)
    hp: Optional[int] = None
    room: Optional[str] = None
    game_over: bool = False
    game_won: bool = False
    error: Optional[str] = None


# ── WebSocket client ──────────────────────────────────────────────────────────

class GameClient:
    def __init__(self, verbose: bool = False):
        self.verbose = verbose
        self._ws = None
        self._state: dict = {}
        self._messages: list[dict] = []

    async def connect(self) -> None:
        self._ws = await websockets.connect(WS_URL)

    async def close(self) -> None:
        if self._ws:
            await self._ws.close()

    def _apply_state(self, msg: dict) -> None:
        if msg.get("type") == "state":
            self._state = msg

    async def _drain(self, timeout: float = CMD_TIMEOUT_SECS) -> list[dict]:
        """Collect messages until the game signals it's ready for the next input.

        Detects combat rounds automatically: when in combat (after encounter_start),
        sends "attack" for each combat prompt ("> ") until encounter_end, then
        waits for the next main-game prompt ("What do you do?") to stop.
        """
        collected = []
        in_combat = False
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                raw = await asyncio.wait_for(self._ws.recv(), timeout=remaining)
                msg = json.loads(raw)
                collected.append(msg)
                self._apply_state(msg)
                t = msg.get("type", "?")
                if self.verbose:
                    txt = str(msg.get("text", ""))[:80].encode("ascii", errors="replace").decode("ascii")
                    print(f"  <- {t:16s} {txt}")

                if t == "encounter_start":
                    in_combat = True
                elif t == "encounter_end":
                    in_combat = False
                elif t == "prompt":
                    text = msg.get("text", "")
                    if in_combat and text.strip() == ">":
                        # Combat round prompt — auto-send attack
                        await self._ws.send("attack")
                    else:
                        # NPC dialogue prompt or main-game prompt — stop and let
                        # the next test step send its command
                        break
                elif t in ("game_over", "game_won"):
                    break
            except asyncio.TimeoutError:
                break
        return collected

    async def send(self, command: str) -> list[dict]:
        await self._ws.send(command)
        return await self._drain()

    def inventory_names(self) -> set[str]:
        items = self._state.get("inventory", [])
        return {i["name"].lower() if isinstance(i, dict) else str(i).lower() for i in items}

    def hp(self) -> Optional[int]:
        return self._state.get("health")

    def room_id(self) -> Optional[str]:
        return self._state.get("room_id")

    def is_won(self, msgs: list[dict]) -> bool:
        return any(m.get("type") == "game_won" for m in msgs)

    def is_over(self, msgs: list[dict]) -> bool:
        return any(m.get("type") in ("game_over", "game_won") for m in msgs)


# ── Assertion helper ──────────────────────────────────────────────────────────

def check_step(client: GameClient, step: Step, msgs: list[dict]) -> StepResult:
    inv = client.inventory_names()
    hp  = client.hp()
    room = client.room_id()
    failures = []

    for item in step.assert_has:
        if item.lower() not in inv:
            failures.append(f"expected '{item}' in inventory, got {sorted(inv)}")

    for item in step.assert_not:
        if item.lower() in inv:
            failures.append(f"expected '{item}' NOT in inventory but found it")

    if step.assert_min_hp is not None and hp is not None and hp < step.assert_min_hp:
        failures.append(f"HP {hp} < required minimum {step.assert_min_hp}")

    if step.assert_room and room != step.assert_room:
        failures.append(f"expected room '{step.assert_room}', got '{room}'")

    return StepResult(
        step=step,
        passed=len(failures) == 0,
        failures=failures,
        inventory=sorted(inv),
        hp=hp,
        room=room,
        game_over=client.is_over(msgs),
        game_won=client.is_won(msgs),
    )


# ── Server lifecycle ──────────────────────────────────────────────────────────

def start_server() -> subprocess.Popen:
    env = {**__import__("os").environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "server:fastapi_app",
         "--host", HOST, "--port", str(PORT), "--log-level", "error"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    time.sleep(SERVER_STARTUP_SECS)
    return proc


# ── Main test runner ──────────────────────────────────────────────────────────

async def run_test(verbose: bool) -> list[StepResult]:
    client = GameClient(verbose=verbose)
    await client.connect()

    # Drain the initial room description before sending any commands
    print("  Waiting for game intro...", end="", flush=True)
    await client._drain(timeout=20)
    print(" ready.\n")

    results: list[StepResult] = []

    for i, step in enumerate(WIN_PATH):
        label = f"[{i+1:02d}/{len(WIN_PATH)}] {step.desc}".encode("ascii", errors="replace").decode("ascii")
        print(f"  {label}")
        if verbose:
            print(f"    -> sending: {step.command!r}")

        try:
            msgs = await client.send(step.command)
        except Exception as exc:
            r = StepResult(step=step, passed=False,
                           error=f"WebSocket error: {exc}")
            results.append(r)
            print(f"    FAIL (connection error): {exc}")
            break

        r = check_step(client, step, msgs)
        results.append(r)

        status = "PASS" if r.passed else "FAIL"
        print(f"    {status}  hp={r.hp}  inv={r.inventory}")
        for f in r.failures:
            print(f"      FAIL: {f}")

        if r.game_won:
            print("  *** GAME WON ***")
            break
        if r.game_over and not r.game_won:
            print("  *** GAME OVER (died or disconnected) ***")
            break

    await client.close()
    return results


def print_summary(results: list[StepResult]) -> None:
    passed = sum(1 for r in results if r.passed)
    total  = len(results)
    won    = any(r.game_won for r in results)

    print()
    print("=" * 60)
    print(f"SUMMARY: {passed}/{total} steps passed  |  {'WIN' if won else 'NO WIN'}")
    print("=" * 60)

    failures = [(i, r) for i, r in enumerate(results, 1) if not r.passed]
    if failures:
        print(f"\nFailed steps ({len(failures)}):")
        for i, r in failures:
            print(f"  [{i:02d}] {r.step.desc}  (cmd: {r.step.command!r})")
            for f in r.failures:
                print(f"       ✗ {f}")
            if r.error:
                print(f"       error: {r.error}")
    else:
        print("\nAll assertions passed.")


def main() -> None:
    parser = argparse.ArgumentParser(description="E2E win-path test")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Print every server message")
    parser.add_argument("--no-server", action="store_true",
                        help="Don't start a server (assume one is already running)")
    args = parser.parse_args()

    proc = None
    if not args.no_server:
        print(f"Starting game server on port {PORT}...")
        proc = start_server()
        print(f"Server started (pid {proc.pid}). Running test...\n")
    else:
        print(f"Connecting to existing server at {WS_URL}\n")

    try:
        results = asyncio.run(run_test(verbose=args.verbose))
        print_summary(results)
        sys.exit(0 if all(r.passed for r in results) else 1)
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(130)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
    finally:
        if proc:
            proc.terminate()
            proc.wait()
            print("\nServer stopped.")


if __name__ == "__main__":
    main()
