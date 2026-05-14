"""
fun_test.py — LLM-as-judge playability test.

Drives the game with a GPT-4o-mini "curious player" bot that wanders organically,
talks to NPCs in passing, and examines things it finds interesting.
Captures the full narrative transcript, then asks Claude to score six playability
dimensions. Uses a different model family as judge to reduce self-rating bias.

Usage:
    python fun_test.py
    python fun_test.py --turns 80 --output fun_report.json
"""

import argparse
import json
import os
import sys
from datetime import datetime

from openai import OpenAI

from io_context import CLIContext
from main import app, make_initial_state, set_io, _io_context_var


class _TurnLimitReached(Exception):
    pass

# ── Clients ──────────────────────────────────────────────────────────────────

oai = OpenAI()

# Judge uses o3-mini — different model family from the gpt-4o that generates game content,
# reducing self-rating bias. Swap to Claude once ANTHROPIC_API_KEY is available:
#
#   import anthropic
#   anth = anthropic.Anthropic()
#   ...in judge_transcript():
#     response = anth.messages.create(model="claude-opus-4-7", ...)
#     return json.loads(response.content[0].text)
JUDGE_MODEL = "o3-mini"

DEFAULT_MAX_TURNS   = 60
CONTEXT_WINDOW      = 8   # last N game messages fed to player bot each turn


# ── Curious player context ───────────────────────────────────────────────────

class CuriousPlayerContext:
    """
    IOContext that captures all narrative output and decides commands using
    GPT-4o-mini acting as a first-time curious player.
    Strips all __ protocol messages; collects both streaming and non-streaming text.
    """

    def __init__(self, max_turns: int = DEFAULT_MAX_TURNS) -> None:
        self.transcript: list[dict] = []
        self._recent_output: list[str] = []
        self._turn = 0
        self._max_turns = max_turns
        self._visited_rooms: list[str] = []        # room names in visit order
        self._current_room: str = ""
        self._current_exits: list[str] = []        # exits from the current room
        self._taken_exits: dict[str, set[str]] = {}  # room_id → set of directions already taken

    # ── IOContext protocol ───────────────────────────────────────────────────

    def send(self, text: str) -> None:
        if text.startswith("__statejson__"):
            try:
                data = json.loads(text[len("__statejson__"):])
                room_id = data.get("room_id", "")
                if room_id and room_id != self._current_room:
                    self._current_room = room_id
                    room_name = data.get("room_name", room_id)
                    if room_name not in self._visited_rooms:
                        self._visited_rooms.append(room_name)
                self._current_exits = data.get("room_exits", [])
            except Exception:
                pass
            return
        if text.startswith("__"):
            return
        clean = text.strip()
        if clean:
            self.transcript.append({"role": "game", "text": clean})
            self._recent_output.append(clean)

    def stream(self, chunks) -> str:
        """Consume a streaming LLM response, capture as a single transcript entry."""
        full = ""
        for chunk in chunks:
            text = chunk.content if hasattr(chunk, "content") else str(chunk)
            if text:
                full += text
        if full.strip():
            self.transcript.append({"role": "game", "text": full.strip()})
            self._recent_output.append(full.strip())
        return full

    def recv(self, prompt: str = "") -> str:
        if self._turn >= self._max_turns:
            raise _TurnLimitReached

        recent_game_text = "\n\n".join(self._recent_output[-CONTEXT_WINDOW:])
        self._recent_output.clear()

        recent_commands = [
            e["text"] for e in self.transcript if e["role"] == "player"
        ][-12:]

        taken_here = self._taken_exits.get(self._current_room, set())
        fresh_exits = [e for e in self._current_exits if e not in taken_here]

        command = _decide_command(
            recent_game_text, recent_commands,
            self._turn, self._max_turns, self._visited_rooms,
            self._current_exits, fresh_exits,
        )

        # Record the exit being taken (if the command is a direction)
        for direction in self._current_exits:
            if command.strip() == direction or command.strip().endswith(f" {direction}"):
                self._taken_exits.setdefault(self._current_room, set()).add(direction)
                break
        self._turn += 1

        self.transcript.append({"role": "player", "text": command})
        print(f"  [{self._turn}/{self._max_turns}] > {command}", flush=True)
        return command


# ── Player bot ────────────────────────────────────────────────────────────────

_PLAYER_SYSTEM = """\
You are playing a gothic text adventure game for the very first time.
You are curious, slightly cautious, and enjoy the atmosphere.
Your goal is NOT to win — it is to experience the world naturally.

How to play:
- Explore different rooms. Try exits you haven't visited yet.
- When you encounter an NPC, greet them and ask 1-2 casual questions, then say goodbye.
  Keep conversations short — 2 exchanges max before moving on.
- Examine items or features in the room that catch your interest.
- Pick up items that seem useful (weapons, potions, keys).
- If a monster is present and it seems manageable, try fighting it.
  If you're losing badly, type: flee
- Don't repeat the same command twice in a row.
- Only type 'quit' if you have completely run out of things to explore.

Respond with ONLY the exact command to type. No explanation, no punctuation around it.\
"""


def _decide_command(
    recent_output: str,
    recent_commands: list[str],
    turn: int,
    max_turns: int,
    visited_rooms: list[str],
    current_exits: list[str],
    fresh_exits: list[str],
) -> str:
    history_str = ", ".join(f'"{c}"' for c in recent_commands) if recent_commands else "none yet"
    visited_str = ", ".join(visited_rooms) if visited_rooms else "none yet"
    exits_str   = ", ".join(current_exits) if current_exits else "unknown"
    fresh_str   = ", ".join(fresh_exits) if fresh_exits else "none — all exits from here explored"

    response = oai.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": _PLAYER_SYSTEM},
            {"role": "user", "content": (
                f"Turn {turn + 1} of {max_turns}.\n"
                f"All exits from your current location: {exits_str}\n"
                f"FRESH exits you haven't taken yet from here: {fresh_str}\n"
                f"Rooms visited so far: {visited_str}\n"
                f"Recent commands: {history_str}\n\n"
                f"What the game just showed you:\n{recent_output}\n\n"
                "If there are FRESH exits, strongly prefer taking one of those.\n"
                "What do you type?"
            )},
        ],
        max_tokens=30,
        temperature=0.9,
    )
    return response.choices[0].message.content.strip().lower()


# ── Playthrough runner ────────────────────────────────────────────────────────

def run_playthrough(max_turns: int = DEFAULT_MAX_TURNS) -> tuple[list[dict], list[str]]:
    ctx = CuriousPlayerContext(max_turns=max_turns)
    token = set_io(ctx)
    try:
        app.invoke(make_initial_state())
    except (_TurnLimitReached, RuntimeError) as e:
        # RuntimeError wraps _TurnLimitReached when raised inside LangGraph's generator (PEP 479)
        if isinstance(e, RuntimeError) and "_TurnLimitReached" not in str(e):
            raise
    finally:
        _io_context_var.reset(token)
    return ctx.transcript, ctx._visited_rooms


# ── Judge ─────────────────────────────────────────────────────────────────────

_JUDGE_SYSTEM = """\
You are an experienced game critic evaluating a gothic text adventure game transcript.
The transcript shows a first-time player exploring a haunted mansion.

Score each dimension from 1 to 10. Be critical — a 7 should feel genuinely earned.
A score of 5 means "fine but unremarkable". Below 5 means "this actively hurts the game".

Dimensions:
  atmosphere     — Is the writing gothic, immersive, and tonally consistent?
                   Does it feel like a real haunted mansion, or generic horror filler?
  variety        — Are room descriptions, combat lines, and NPC replies distinct from
                   each other, or do they recycle the same phrases and sentence structures?
  npc_voice      — Do NPCs feel like different characters with distinct personalities,
                   or do they all sound like the same narrator wearing different hats?
  pacing         — Does the game move at a satisfying rhythm? Not so fast the player
                   can't absorb it, not so slow or repetitive that it drags?
  discovery      — Do secrets, hidden items, and reveals feel rewarding when found?
                   Is there a sense of "I'm glad I looked"?
  world_coherence — Does the world feel consistent and reactive? Do NPCs remember things,
                   react to your reputation, and behave in ways that make sense given
                   what's happened? Or does it feel like each interaction is isolated?

Respond with valid JSON only, exactly this structure:
{
  "scores": {
    "atmosphere":      {"score": N, "reason": "one or two sentences"},
    "variety":         {"score": N, "reason": "one or two sentences"},
    "npc_voice":       {"score": N, "reason": "one or two sentences"},
    "pacing":          {"score": N, "reason": "one or two sentences"},
    "discovery":       {"score": N, "reason": "one or two sentences"},
    "world_coherence": {"score": N, "reason": "one or two sentences"}
  },
  "overall": N,
  "top_issue": "The single most important thing that would improve the experience.",
  "standout":  "The single strongest thing the game does well."
}\
"""


def judge_transcript(transcript: list[dict]) -> dict:
    flat = "\n".join(
        f"[{'GAME' if t['role'] == 'game' else 'PLAYER'}] {t['text']}"
        for t in transcript
    )

    response = oai.chat.completions.create(
        model=JUDGE_MODEL,
        messages=[
            {"role": "system", "content": _JUDGE_SYSTEM},
            {"role": "user",   "content": flat},
        ],
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)


# ── Report ────────────────────────────────────────────────────────────────────

def print_report(result: dict, transcript: list[dict], visited_rooms: list[str]) -> None:
    scores  = result["scores"]
    overall = result["overall"]

    turns = sum(1 for e in transcript if e["role"] == "player")
    rooms = len(visited_rooms)

    print()
    print("=" * 65)
    print("FUN TEST REPORT")
    print("=" * 65)
    print(f"Turns played: {turns}   Rooms visited (est.): {rooms}")
    print()
    print(f"{'Dimension':<18} {'Score':>5}   Reason")
    print("-" * 65)
    for dim, data in scores.items():
        bar = "█" * data["score"] + "░" * (10 - data["score"])
        print(f"{dim:<18} {data['score']:>2}/10  {bar}")
        print(f"{'':18}        {data['reason']}")
        print()
    print(f"Overall:           {overall:>2}/10")
    print()
    print(f"Top issue:  {result['top_issue']}")
    print(f"Standout:   {result['standout']}")
    print("=" * 65)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Fun test — LLM-as-judge playability scorer")
    parser.add_argument("--turns",  type=int, default=DEFAULT_MAX_TURNS, help="Max player turns")
    parser.add_argument("--output", type=str, default=None, help="Save JSON report to file")
    args = parser.parse_args()

    print(f"Running {args.turns}-turn curious-player playthrough...")
    transcript, visited_rooms = run_playthrough(max_turns=args.turns)
    print(f"Playthrough complete. {len(transcript)} transcript entries, {len(visited_rooms)} rooms visited: {', '.join(visited_rooms)}")

    print(f"Judging with {JUDGE_MODEL}...")
    result = judge_transcript(transcript)

    print_report(result, transcript, visited_rooms)

    if args.output:
        payload = {
            "meta": {
                "timestamp": datetime.now().isoformat(),
                "turns": args.turns,
                "transcript_entries": len(transcript),
            },
            "result": result,
            "transcript": transcript,
        }
        with open(args.output, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"\nFull report saved to: {args.output}")


if __name__ == "__main__":
    main()
