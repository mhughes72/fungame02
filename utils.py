# audio_utils.py
# Text-to-speech utility for narrating game output.
# Currently unused (speak() calls are commented out in main.py).
# Can be re-enabled to have room descriptions and NPC dialogue spoken aloud.

from langchain_core.messages import SystemMessage
from prompts import GAME_SYSTEM_PROMPT

import os
import contextvars

DEBUG = os.getenv("DEBUG", "true").lower() == "true"
_debug_io_var: contextvars.ContextVar = contextvars.ContextVar('debug_io', default=None)

def debug(msg):
    if DEBUG:
        print(f"\033[2m  ▸ {msg}\033[0m")
        _io = _debug_io_var.get()
        if _io is not None:
            _io.send(f"__debug__{msg}")

def visible_items(room):
    return [i for i in room["items"] if not i["hidden"]]

def find_item(room, name, include_hidden=False):
    items = room["items"] if include_hidden else visible_items(room)
    return next((i for i in items if i["name"] == name), None)

def invoke_with_system(llm, prompt):
    if hasattr(prompt, 'to_messages'):
        messages = prompt.to_messages()
    elif isinstance(prompt, list):
        messages = prompt
    else:
        messages = [prompt]

    # Don't prepend if already has a system message
    if messages and isinstance(messages[0], SystemMessage):
        return llm.invoke(messages)

    return llm.invoke([SystemMessage(content=GAME_SYSTEM_PROMPT)] + messages)


def stream_with_system(llm, prompt, io) -> str:
    """Stream LLM output through io, prepending GAME_SYSTEM_PROMPT if needed. Returns full text."""
    if hasattr(prompt, 'to_messages'):
        messages = prompt.to_messages()
    elif isinstance(prompt, list):
        messages = prompt
    else:
        messages = [prompt]

    if not (messages and isinstance(messages[0], SystemMessage)):
        messages = [SystemMessage(content=GAME_SYSTEM_PROMPT)] + messages

    return io.stream(llm.stream(messages))

def mood_tone_for_score(score: int) -> str:
    """Return a prompt-injectable mood instruction based on the NPC's mood score."""
    if score >= 50:
        return "MOOD INSTRUCTION: You are genuinely fond of this player. Be warm, open, and unusually forthcoming — share more than you normally would."
    elif score >= 20:
        return "MOOD INSTRUCTION: You like this player. Be noticeably cooperative and pleasant, more willing than usual to help."
    elif score >= -9:
        return ""  # neutral — no injection
    elif score >= -40:
        return "MOOD INSTRUCTION: You are irritated by this player. Be noticeably short, guarded, and reluctant. Give clipped answers. Make it clear you'd rather not be talking to them."
    else:
        return "MOOD INSTRUCTION: You strongly dislike this player. Be cold, dismissive, and unhelpful. Refuse to elaborate. Your contempt should be unmistakable."


def fear_tone_for_score(score: int) -> str:
    """Return a prompt-injectable fear instruction based on the NPC's fear score."""
    if score >= 60:
        return "FEAR INSTRUCTION: You are terrified of this player. You are visibly shaking and will do almost anything to avoid provoking them — including volunteering information or help you'd normally withhold."
    elif score >= 30:
        return "FEAR INSTRUCTION: You are afraid of this player. You are nervous and choosing your words very carefully to avoid angering them."
    elif score >= 10:
        return "FEAR INSTRUCTION: This player unnerves you slightly. There is a cautious edge to your manner."
    else:
        return ""  # not afraid — no injection


CONVERSATION_EXIT_WORDS = ["goodbye", "bye", "leave", "exit", "done", "farewell", "stop"]


def mood_price_multiplier(score: int) -> float:
    """Shop price multiplier based on NPC mood. Friendlier = cheaper."""
    if score >= 50:  return 0.85
    elif score >= 20: return 0.92
    elif score >= -19: return 1.0
    elif score >= -50: return 1.10
    else:            return 1.20


def fear_price_multiplier(score: int) -> float:
    """Shop price multiplier based on NPC fear. More scared = steeper discount."""
    if score >= 60:  return 0.70
    elif score >= 30: return 0.82
    elif score >= 10: return 0.93
    else:            return 1.0

# Combat constants
FLEE_SUCCESS_THRESHOLD = 40       # d100 roll must exceed this to flee
WEAKNESS_BONUS_DAMAGE = 5         # extra damage when weapon type matches weakness
ARMOR_REDUCTION_RATE = 0.05       # damage reduction per armor point
ARMOR_REDUCTION_CAP = 0.75        # maximum damage reduction (75%)


def emit_encounter_start(io, **kwargs) -> None:
    import json
    io.send(f"__encounter_start__{json.dumps(kwargs)}")


def emit_encounter_end(io) -> None:
    io.send("__encounter_end__")


def emit_encounter_state(io, **kwargs) -> None:
    import json
    io.send(f"__encounter_state__{json.dumps(kwargs)}")


_item_catalogue_cache: dict | None = None

def _get_item_catalogue() -> dict:
    global _item_catalogue_cache
    if _item_catalogue_cache is None:
        import json, os
        path = os.path.join(os.path.dirname(__file__), "data", "items.json")
        with open(path) as f:
            _item_catalogue_cache = json.load(f)
    return _item_catalogue_cache


def emit_player_state(player: dict, room_id: str, io, room_data: dict = None) -> None:
    """Send a structured player state update to the frontend via the IO context."""
    import json
    inventory = player.get("inventory", [])
    item_cat = _get_item_catalogue()
    data = {
        "room_id": room_id,
        "health": player.get("health", 100),
        "max_health": player.get("max_health", 100),
        "gold": player.get("gold", 0),
        "equipped_weapon": player.get("equipped_weapon"),
        "equipped_armor": player.get("equipped_armor", {}),
        "inventory": [
            {
                "name": i["name"],
                "damage": i.get("damage", 0),
                "weapon_type": i.get("weapon_type"),
                "armor_slot": i.get("armor_slot"),
                "armor_rating": i.get("armor_rating", 0),
                "description": item_cat.get(make_slug(i["name"]), {}).get("description", ""),
            }
            for i in inventory
        ],
    }
    if room_data:
        data["room_name"] = room_data.get("name", "")
        data["room_monsters"] = [
            {"name": m["name"], "health": m.get("health", 0), "max_health": m.get("max_health", 1)}
            for m in room_data.get("monsters", [])
        ]
        data["room_npcs"]  = [{"name": n["name"]} for n in room_data.get("npcs", [])]
        data["room_items"] = [{"name": i["name"]} for i in room_data.get("items", []) if not i.get("hidden")]
        data["room_exits"] = list(room_data.get("exits", {}).keys())
    io.send(f"__statejson__{json.dumps(data)}")


def parse_llm_json(text: str) -> str:
    """Strip markdown code fences from LLM output before JSON parsing."""
    text = text.strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


def make_slug(name: str) -> str:
    return name.lower().replace(' ', '_').replace("'", '').replace('-', '_')


def find_npc(room: dict, target: str) -> dict | None:
    """Find an NPC in a room by partial name match. target should be lowercased."""
    return next(
        (n for n in room.get("npcs", []) if n["name"].lower() in target or target in n["name"].lower()),
        None
    )


def get_mutable_player(state: dict) -> tuple[dict, list]:
    player = dict(state.get("player", {}))
    inventory = list(player.get("inventory", []))
    return player, inventory


def get_mutable_room(state: dict, room_id: str) -> tuple[dict, dict]:
    room_states = dict(state.get("room_states", {}))
    room_override = dict(room_states.get(room_id, {}))
    return room_states, room_override


def total_armor_rating(player: dict, inventory: list) -> int:
    """Calculate total armor rating from all equipped armor pieces."""
    equipped_armor = player.get("equipped_armor", {})
    total = 0
    for slot, item_name in equipped_armor.items():
        item = next((i for i in inventory if i["name"] == item_name), None)
        if item:
            total += item.get("armor_rating", 0)
    return total

