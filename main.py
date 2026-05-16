# main.py
# Core game module for the Haunted Mansion text adventure.
# Defines all game state types (AgentState, PlayerState, RoomData, etc.),
# initializes the LLM, loads room and shop data from JSON, and contains
# the main LangGraph nodes: load_room_data, describe_room, get_player_action,
# parse_command, npc_dialogue, combat_node, and resolve_action.
# Builds and compiles the LangGraph state graph and starts the game.


import contextvars
import copy
import os
import json
import random
from typing import Dict, List, Optional
from typing_extensions import TypedDict, NotRequired
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import START, StateGraph, END
from tavily import TavilyClient

from io_context import IOContext, CLIContext
from handlers.movement import handle_unlock
from prompts import (
    ROOM_DESCRIPTION_PROMPT, COMMAND_PARSER_PROMPT,
    COMBAT_PROMPT, FLEE_PROMPT, GAME_SYSTEM_PROMPT, WIN_PROMPT,
    ROOM_FEATURES_PROMPT
)

from utils import invoke_with_system, stream_with_system, find_item, visible_items, total_armor_rating, debug, parse_llm_json, emit_player_state, get_mutable_player, get_mutable_room
from npc_memory import clear_all_memories
from handlers import (
    handle_go, handle_take, handle_examine, handle_open,
    handle_equip, handle_unequip, handle_use,
    handle_inventory, handle_room,
    combat_node, npc_dialogue, handle_bribe, handle_help
)

load_dotenv()

_io_context_var: contextvars.ContextVar[IOContext] = contextvars.ContextVar("io", default=CLIContext())

def io_ctx() -> IOContext:
    return _io_context_var.get()

def set_io(ctx: IOContext) -> contextvars.Token:
    return _io_context_var.set(ctx)

# ── Type definitions ─────────────────────────────────────────────────────────

class MonsterDrops(TypedDict):
    gold: int
    item: Optional[str]

class MonsterData(TypedDict):
    name: str
    health: int
    max_health: int
    defense: int
    damage: int
    damage_variance: int
    weaknesses: List[str]
    drops: MonsterDrops

class ItemData(TypedDict):
    name: str
    hidden: bool
    revealed_by: Optional[str]
    openable: bool
    is_open: bool
    gold: int
    damage: int
    weapon_type: Optional[str]
    armor_slot: Optional[str]
    armor_rating: int
    heal_amount: int

class NPCData(TypedDict):
    name: str
    description: str
    personality: str
    knowledge: str
    can_search_web: bool
    shop_id: NotRequired[str]

class LockedExit(TypedDict):
    required_key: str
    locked: bool

class RoomData(TypedDict):
    name: str
    description: str
    exits: Dict[str, str]
    locked_exits: Dict[str, LockedExit]
    items: List[ItemData]
    monsters: List[MonsterData]
    npcs: List[NPCData]

class RoomState(TypedDict, total=False):
    items: List[ItemData]
    monsters: List[MonsterData]
    visited: bool
    locked_exits: Dict[str, LockedExit]

class PlayerState(TypedDict, total=False):
    inventory: List[ItemData]
    health: int
    max_health: int
    status_effects: List[str]
    gold: int
    equipped_weapon: Optional[str]
    equipped_armor: Dict[str, str]  # slot -> item name

class HandlerResult(TypedDict, total=False):
    player: PlayerState
    room_states: Dict[str, RoomState]
    force_full_description: bool
    route_to: Optional[str]
    current_room_id: str
    combat_target: Optional[str]
    game_over: bool
    game_won: bool
    npc_moods: Dict[str, int]
    npc_fear: Dict[str, int]
    skip_description: bool
    just_fled: bool
    previous_room_id: str

class AgentState(TypedDict):
    current_room_id: str
    current_room_data: NotRequired[RoomData]
    room_states: NotRequired[Dict[str, RoomState]]
    player_input: NotRequired[str]
    player: NotRequired[PlayerState]
    force_full_description: NotRequired[bool]
    game_over: NotRequired[bool]
    route_to: NotRequired[str]
    active_npc: NotRequired[str]
    npc_continue: NotRequired[bool]
    combat_target: NotRequired[str]
    skip_description: NotRequired[bool]
    game_won: NotRequired[bool]
    previous_room_id: NotRequired[str]
    just_fled: NotRequired[bool]
    npc_moods: NotRequired[Dict[str, int]]
    npc_fear: NotRequired[Dict[str, int]]
    npc_gifts_given: NotRequired[List[str]]
    npc_trades_done: NotRequired[List[str]]
    rooms_visited: NotRequired[List[str]]
    last_entity: NotRequired[str]



# ── Setup ────────────────────────────────────────────────────────────────────

DATA_DIR = "data"

ITEM_DEFAULTS: dict = {
    "damage": 0, "weapon_type": None, "armor_slot": None,
    "armor_rating": 0, "heal_amount": 0, "openable": False,
    "gold": 0, "is_open": False, "revealed_by": None,
}

llm = ChatOpenAI(model="gpt-4o", temperature=0.5)
mini_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.5)

def load_game_data() -> tuple[dict, dict, dict, dict]:
    with open(os.path.join(DATA_DIR, "rooms.json"), "r") as f:
        rooms = json.load(f)
    with open(os.path.join(DATA_DIR, "monsters.json"), "r") as f:
        monsters = json.load(f)
    with open(os.path.join(DATA_DIR, "npcs.json"), "r") as f:
        npcs = json.load(f)
    with open(os.path.join(DATA_DIR, "items.json"), "r") as f:
        items = json.load(f)

    for room_id, room in rooms.items():
        room["monsters"] = [copy.deepcopy(monsters[m]) for m in room.get("monsters", [])]
        room["npcs"] = [copy.deepcopy(npcs[n]) for n in room.get("npcs", [])]
        resolved_items = []
        for ref in room.get("items", []):
            item_id = ref["id"]
            catalogue_entry = copy.deepcopy(items.get(item_id, {}))
            merged = {**ITEM_DEFAULTS, **catalogue_entry, **ref}
            resolved_items.append(merged)
        room["items"] = resolved_items

    return rooms, npcs, monsters, items


def resolve_shop_stock(shops: dict, items: dict) -> None:
    for shop in shops.values():
        resolved = []
        for ref in shop.get("stock", []):
            item_id = ref["id"]
            catalogue_entry = copy.deepcopy(items.get(item_id, {}))
            merged = {**ITEM_DEFAULTS, **catalogue_entry, **ref}
            resolved.append(merged)
        shop["stock"] = resolved


with open(os.path.join(DATA_DIR, "shop.json"), "r") as f:
    SHOPS = json.load(f)

ROOMS, NPC_CATALOGUE, MONSTER_CATALOGUE, ITEM_CATALOGUE = load_game_data()
resolve_shop_stock(SHOPS, ITEM_CATALOGUE)


def validate_game_data(rooms: dict, shops: dict, items: dict | None = None) -> None:
    """Check referential integrity of game data files at startup."""
    all_room_ids = set(rooms.keys())
    all_shop_ids = set(shops.keys())
    all_item_ids = set(items.keys()) if items else set()
    errors = []

    for room_id, room in rooms.items():
        for direction, dest in room.get("exits", {}).items():
            if dest not in all_room_ids:
                errors.append(f"{room_id}: exit '{direction}' → '{dest}' (room not found)")
        for npc in room.get("npcs", []):
            shop_id = npc.get("shop_id")
            if shop_id and shop_id not in all_shop_ids:
                errors.append(f"{room_id}: NPC '{npc['name']}' references shop_id '{shop_id}' (not found)")
        for direction, lock in room.get("locked_exits", {}).items():
            if direction not in room.get("exits", {}):
                errors.append(f"{room_id}: locked_exit '{direction}' has no matching exit")
        for item in room.get("items", []):
            item_id = item.get("id")
            if item_id and all_item_ids and item_id not in all_item_ids:
                errors.append(f"{room_id}: item '{item_id}' not found in items.json")

    for shop_id, shop in shops.items():
        for item in shop.get("stock", []):
            item_id = item.get("id")
            if item_id and all_item_ids and item_id not in all_item_ids:
                errors.append(f"shop '{shop_id}': item '{item_id}' not found in items.json")

    if errors:
        raise ValueError("Game data validation failed:\n" + "\n".join(f"  - {e}" for e in errors))

    debug(f"validate: {len(rooms)} rooms, {len(shops)} shops — OK")


validate_game_data(ROOMS, SHOPS, ITEM_CATALOGUE)
    
# ── Nodes ────────────────────────────────────────────────────────────────────

def load_room_data(state: AgentState) -> dict:
    new_room_id = state["current_room_id"]
    base_room = ROOMS[new_room_id]
    room_override = state.get("room_states", {}).get(new_room_id, {})

    # Track previous room
    current_room_data = state.get("current_room_data")
    if current_room_data:
        old_room_id = next(
            (rid for rid, r in ROOMS.items() if r["name"] == current_room_data["name"]),
            None
        )
        if old_room_id and old_room_id != new_room_id:
            previous_room_id = old_room_id
        else:
            previous_room_id = state.get("previous_room_id")
    else:
        previous_room_id = state.get("previous_room_id")

    room: RoomData = {
        "name": base_room["name"],
        "description": base_room["description"],
        "exits": dict(base_room["exits"]),
        "locked_exits": dict(room_override.get("locked_exits", base_room.get("locked_exits", {}))),
        "monsters": list(room_override.get("monsters", base_room["monsters"])),
        "items": list(room_override.get("items", base_room["items"])),
        "npcs": list(base_room.get("npcs", [])),
    }

    override_keys = list(room_override.keys()) if room_override else []
    debug(f"load_room: {new_room_id} ({base_room['name']}) | overrides: {override_keys or 'none'} | prev: {previous_room_id}")

    emit_player_state(state.get("player", {}), new_room_id, io_ctx(), room_data=room)

    rooms_visited = list(state.get("rooms_visited", []))
    if new_room_id not in rooms_visited:
        rooms_visited.append(new_room_id)
        import json as _json
        _entry = _json.dumps({"text": f"Discovered: {room['name']}."})
        io_ctx().send(f"__journal_entry__{_entry}")

    return {
        "current_room_data": room,
        "route_to": None,
        "previous_room_id": previous_room_id,
        "rooms_visited": rooms_visited
    }


_FEATURES_CACHE_PATH = os.path.join("data", "room_features_cache.json")

def _room_hash(room: dict) -> str:
    """Stable hash of room data fields that affect feature generation."""
    import hashlib
    key = json.dumps({
        "name": room.get("name"),
        "npcs": [{"name": n["name"], "can_search_web": n.get("can_search_web"), "shop_id": n.get("shop_id")} for n in room.get("npcs", [])],
        "monsters": [m["name"] for m in room.get("monsters", [])],
    }, sort_keys=True)
    return hashlib.md5(key.encode()).hexdigest()[:12]

def _load_features_cache() -> dict:
    try:
        with open(_FEATURES_CACHE_PATH, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _save_features_cache(cache: dict) -> None:
    with open(_FEATURES_CACHE_PATH, "w") as f:
        json.dump(cache, f, indent=2)

def _emit_room_features(room: dict, room_id: str) -> None:
    """Emit AI feature descriptions, using disk cache unless room data changed."""
    room_hash = _room_hash(room)
    cache = _load_features_cache()
    entry = cache.get(room_id, {})

    if entry.get("hash") == room_hash:
        debug(f"room_features: cache hit for {room_id}")
        io_ctx().send(f"__roomfeatures__{json.dumps({'room_id': room_id, 'features': entry['features']})}")
        return

    npcs = room.get("npcs", [])
    npc_parts = []
    for n in npcs:
        tags = []
        if n.get("can_search_web"):
            tags.append("web_search=true")
        if n.get("shop_id"):
            tags.append("shop=true")
        npc_parts.append(n["name"] + (f" [{', '.join(tags)}]" if tags else ""))
    npc_summary = ", ".join(npc_parts) if npc_parts else "none"
    monsters = ", ".join(m["name"] for m in room.get("monsters", [])) or "none"

    try:
        response = mini_llm.invoke([
            {"role": "user", "content": ROOM_FEATURES_PROMPT.format(
                room_name=room["name"],
                npc_summary=npc_summary,
                monsters=monsters,
            )}
        ])
        features = json.loads(parse_llm_json(response.content))
        cache[room_id] = {"hash": room_hash, "features": features}
        _save_features_cache(cache)
        debug(f"room_features: generated and cached for {room_id}")
        io_ctx().send(f"__roomfeatures__{json.dumps({'room_id': room_id, 'features': features})}")
    except Exception as e:  # intentionally broad — wraps LLM call; features are non-critical
        debug(f"room_features error: {e}")


def describe_room(state: AgentState) -> dict:
    if state.get("skip_description"):
        return {"skip_description": False}

    room = state["current_room_data"]
    room_id = state["current_room_id"]
    room_states, room_override = get_mutable_room(state, room_id)

    already_visited = room_override.get("visited", False)
    force_full_description = state.get("force_full_description", False)

    if not already_visited or force_full_description:
        visible = [i["name"] for i in room["items"] if not i["hidden"]]

        container_hints = []
        for i in room["items"]:
            if not i["hidden"] and i.get("openable") and not i.get("is_open"):
                container_hints.append(f"The {i['name']} looks like it might contain something")

        hidden_hints = [
            f"Something may be concealed within or beneath the {i['revealed_by']}"
            for i in room["items"] if i.get("hidden") and i.get("revealed_by")
        ]

        prompt = ROOM_DESCRIPTION_PROMPT.invoke({
            "name": room["name"],
            "description": room["description"],
            "items": ", ".join(visible) if visible else "none",
            "containers": ". ".join(container_hints) if container_hints else "none",
            "monsters": "; ".join(
                f"{m['name']} — {m['description']}" if m.get("description") else m["name"]
                for m in room["monsters"]
            ) if room["monsters"] else "none",
            "npcs": ", ".join(n["name"] for n in room["npcs"]) if room["npcs"] else "none",
            "exits": ", ".join(room["exits"].keys()) if room["exits"] else "none",
            "hidden_hints": "Concealment hints (do not reveal directly): " + "; ".join(hidden_hints) if hidden_hints else "",
        })

        stream_with_system(llm, prompt, io_ctx())

        room_override["visited"] = True
        room_states[room_id] = room_override

        # Generate AI features list once per room using cheap model
        _emit_room_features(room, room_id)

        return {
            "room_states": room_states,
            "force_full_description": False
        }

    short_desc = (
        f"You are back in the {room['name']}. "
        f"Items: {', '.join(i['name'] for i in room['items'] if not i['hidden']) or 'none'}. "
        f"Exits: {', '.join(room['exits'].keys()) if room['exits'] else 'none'}."
    )
    io_ctx().send(short_desc)
    return {"force_full_description": False}

def check_aggressive(state: AgentState) -> dict:
    # Skip aggressive check if player just fled
    debug(f"check_aggressive | just_fled: {state.get('just_fled')}")
    if state.get("just_fled"):
        return {"route_to": None, "just_fled": False}

    room = state["current_room_data"]
    aggressive_monsters = [m for m in room["monsters"] if m.get("aggressive")]

    if aggressive_monsters:
        monster = aggressive_monsters[0]
        io_ctx().send(f"\nThe {monster['name']} lunges at you before you can react!")
        return {"route_to": "combat", "combat_target": monster["name"]}

    return {"route_to": None}

RITUAL_ITEMS = {"strange amulet", "blood pact", "unmaking flame"}

def handle_ritual(state: AgentState) -> dict:
    if state["current_room_id"] != "room_5":
        io_ctx().send(
            "The ritual must be performed at the sealed front door. Make your way to the Grand Foyer."
        )
        return {"force_full_description": False}

    player, inventory = get_mutable_player(state)
    held = {i["name"] for i in inventory}
    missing = RITUAL_ITEMS - held

    if missing:
        missing_list = ", ".join(f"the {name}" for name in sorted(missing))
        io_ctx().send(
            f"The ritual is incomplete. You are still missing: {missing_list}.\n"
            "You need the Strange Amulet, the Blood Pact, and the Unmaking Flame."
        )
        return {"force_full_description": False}

    return trigger_win(state)


def trigger_win(state: AgentState) -> dict:
    player, inventory = get_mutable_player(state)

    gifts        = state.get("npc_gifts_given", [])
    trades       = state.get("npc_trades_done", [])
    rooms        = state.get("rooms_visited", set())

    beats = []
    if any("secret letter" in t for t in trades):
        beats.append("The player read Harwick's secret letter and learned the full history of the bargain — his family's blood, the Hollow's promise, and what it cost.")
    if "Professor Aldric" in gifts:
        beats.append("Professor Aldric trusted the player with the iron key and the scholar's oath, knowing what waited below.")
    if "Edmund" in gifts:
        beats.append("The player showed mercy to Edmund Marsh — the groundskeeper cursed into wolf form for witnessing Pemberton's murder — offering the silver bullet as release rather than a weapon. Edmund is at rest beside Pemberton's grave.")
    if "Lady Vespera" in gifts:
        beats.append("Lady Vespera, who has watched this mansion for centuries and created the wraith herself, helped the player understand what was needed. She has been waiting for this night.")
    if "The Oracle" in gifts:
        beats.append("The player consulted the Oracle — an anachronistic figure with knowledge of the outside world — to unlock one of the mansion's hidden secrets.")
    if "Shadow" in gifts:
        beats.append("Even Shadow the cat, who claims ancient wisdom and is usually wrong, pointed the player toward the garden and Edmund's vigil.")
    beats.append(f"The player explored {len(rooms)} of 12 rooms.")

    story_context = "\n".join(f"- {b}" for b in beats)

    prompt = WIN_PROMPT.invoke({
        "story_context": story_context,
        "gold": player.get("gold", 0),
        "health": player.get("health", 100),
        "max_health": player.get("max_health", 100),
        "inventory": ", ".join(i["name"] for i in inventory) or "nothing",
    })

    response = invoke_with_system(llm, prompt)
    io_ctx().send("\n" + "═" * 50)
    io_ctx().send(response.content)
    io_ctx().send("═" * 50 + "\n")
    io_ctx().send("__game_won__")

    return {"game_won": True, "game_over": True}

def get_player_action(state: AgentState) -> dict:
    player_input = io_ctx().recv("\nWhat do you do? ")
    return {"player_input": player_input}


def parse_command(player_input: str, state: AgentState) -> dict:
    room = state["current_room_data"]
    player = state.get("player", {})
    inventory = player.get("inventory", [])

    prompt = COMMAND_PARSER_PROMPT.invoke({
        "room_name": room["name"],
        "exits": ", ".join(room["exits"].keys()) or "none",
        "room_items": ", ".join(i["name"] for i in room["items"] if not i["hidden"]) or "none",
        "monsters": ", ".join(m["name"] for m in room["monsters"]) or "none",
        "npcs": ", ".join(n["name"] for n in room["npcs"]) or "none",
        "inventory": ", ".join(i["name"] for i in inventory) or "nothing",
        "player_input": player_input,
        "last_entity": state.get("last_entity") or "none",
    })

    response = invoke_with_system(mini_llm, prompt)

    try:
        parsed = json.loads(parse_llm_json(response.content))
        debug(f"parse_command: {parsed}")
        return parsed
    except (json.JSONDecodeError, ValueError) as e:
        debug(f"parse_command error: {e} | raw: {response.content}")
        return {"action": "unknown", "target": None}




def resolve_action(state: AgentState) -> dict:
    player_input = state.get("player_input", "").strip()

    # Cheat: give gold
    if player_input == "cheat gold":
        player = dict(state.get("player", {}))
        player["gold"] = player.get("gold", 0) + 100
        emit_player_state(player, state["current_room_id"], io_ctx())
        io_ctx().send("[CHEAT] +100 gold conjured from thin air.")
        return {"player": player, "skip_description": True}

    # Debug teleport
    if player_input.startswith("goto "):
        target_room = player_input.split(" ")[1].strip()
        if target_room in ROOMS:
            debug(f"goto: teleporting to {target_room}")
            return {"current_room_id": target_room, "force_full_description": True}
        debug(f"goto: room '{target_room}' not found")
        return {"force_full_description": False}

    command = parse_command(player_input, state)
    action = command.get("action", "unknown")
    target = command.get("target")
    debug(f"resolve: '{player_input}' → action: {action} | target: {target}")

    handlers = {
        "go":        lambda: handle_go(state, target, io_ctx()),
        "take":      lambda: handle_take(state, target, io_ctx()),
        "examine":   lambda: handle_examine(state, target, mini_llm, io_ctx(), mini_llm),
        "open":      lambda: handle_open(state, target, io_ctx()),
        "equip":     lambda: handle_equip(state, target, io_ctx()),
        "inventory": lambda: handle_inventory(state, io_ctx()),
        "room":      lambda: handle_room(state, io_ctx()),
        "look":      lambda: {"force_full_description": True},
        "talk":      lambda: {"route_to": "npc_dialogue"},
        "attack":    lambda: {"route_to": "combat", "combat_target": target},
        "quit":      lambda: (io_ctx().send("Goodbye.") or {"game_over": True}),
        "unequip":   lambda: handle_unequip(state, target, io_ctx()),
        "use": lambda: handle_use(state, target, io_ctx()),
        "win": lambda: trigger_win(state),
        "ritual": lambda: handle_ritual(state),
        "unlock": lambda: handle_unlock(state, target, io_ctx()),
        "help": lambda: handle_help(io_ctx()),
        "bribe":     lambda: handle_bribe(state, target or "", command.get("amount", 10), llm, mini_llm, io_ctx()),
        "clearmemory": lambda: (clear_all_memories() or io_ctx().send("[NPC memories cleared.]")) or {"force_full_description": False},

    }

    handler = handlers.get(action)
    if handler:
        return handler()

    io_ctx().send("You're not sure how to do that.")
    return {"force_full_description": False}


def next_step(state: AgentState) -> str:
    if state.get("game_over"):
        return END
    if state.get("game_won"):
        return END
    if state.get("route_to") == "npc_dialogue":
        return "npc_dialogue"
    if state.get("route_to") == "combat":
        return "combat"
    return "load_room_data"

def after_describe(state: AgentState) -> str:
    if state.get("route_to") == "combat":
        return "combat"
    return "get_player_action"


# ── Graph ────────────────────────────────────────────────────────────────────

graph = StateGraph(AgentState)
graph.add_node("load_room_data", load_room_data)
graph.add_node("describe_room", describe_room)
graph.add_node("check_aggressive", check_aggressive)
graph.add_node("get_player_action", get_player_action)
graph.add_node("resolve_action", resolve_action)
graph.add_node("combat", lambda state: combat_node(state, ROOMS, mini_llm, io_ctx(), ITEM_CATALOGUE))
graph.add_node("npc_dialogue", lambda state: npc_dialogue(state, SHOPS, NPC_CATALOGUE, MONSTER_CATALOGUE, ITEM_CATALOGUE, llm, mini_llm, parse_command, io_ctx()))

graph.add_edge(START, "load_room_data")
graph.add_edge("load_room_data", "describe_room")
graph.add_edge("describe_room", "check_aggressive")
graph.add_conditional_edges(
    "check_aggressive",
    after_describe,
    {
        "get_player_action": "get_player_action",
        "combat": "combat",
    }
)
graph.add_edge("get_player_action", "resolve_action")
graph.add_edge("npc_dialogue", "load_room_data")
graph.add_conditional_edges("combat", next_step, {END: END, "load_room_data": "load_room_data"})

graph.add_conditional_edges(
    "resolve_action",
    next_step,
    {
        "load_room_data": "load_room_data",
        "npc_dialogue": "npc_dialogue",
        "combat": "combat",
        END: END,
    }
)

app = graph.compile()


def make_initial_state() -> AgentState:
    """Return a fresh game state. Swap in the testing state below when needed."""
    return AgentState(
        current_room_id="room_1",
        player={
            "inventory": [],
            "health": 100,
            "max_health": 100,
            "status_effects": [],
            "gold": 0,
            "equipped_weapon": None,
            "equipped_armor": {},
        },
        npc_moods={},
        npc_fear={},
        npc_gifts_given=[],
        npc_trades_done=[],
        rooms_visited=[],
    )


if __name__ == "__main__":
    clear_all_memories()
    app.invoke(make_initial_state())

    img = app.get_graph().draw_mermaid_png()
    with open("graph.png", "wb") as f:
        f.write(img)
    print("Saved graph.png")