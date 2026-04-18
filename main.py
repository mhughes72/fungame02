# main.py
# Core game module for the Haunted Mansion text adventure.
# Defines all game state types (AgentState, PlayerState, RoomData, etc.),
# initializes the LLM, loads room and shop data from JSON, and contains
# the main LangGraph nodes: load_room_data, describe_room, get_player_action,
# parse_command, npc_dialogue, combat_node, and resolve_action.
# Builds and compiles the LangGraph state graph and starts the game.


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

from handlers.movement import handle_unlock
from prompts import (
    ROOM_DESCRIPTION_PROMPT, COMMAND_PARSER_PROMPT, NPC_PROMPT,
    WEB_SEARCH_ROLEPLAY_PROMPT, COMBAT_PROMPT, FLEE_PROMPT, 
    GAME_SYSTEM_PROMPT, SHOP_SYSTEM_PROMPT, WIN_PROMPT
)

from utils import invoke_with_system, find_item, visible_items, total_armor_rating, debug
from handlers import (
    handle_go, handle_take, handle_examine, handle_open,
    handle_equip, handle_unequip, handle_use,
    handle_inventory, handle_room,
    combat_node, npc_dialogue, handle_help
)

load_dotenv()

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



# ── Setup ────────────────────────────────────────────────────────────────────

llm = ChatOpenAI(model="gpt-4o", temperature=0.5)

with open(os.path.join("data", "rooms.json"), "r") as f:
    ROOMS = json.load(f)

with open(os.path.join("data", "shop.json"), "r") as f:
    SHOPS = json.load(f)
    
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

    return {
        "current_room_data": room,
        "route_to": None,
        "previous_room_id": previous_room_id
    }


def describe_room(state: AgentState) -> dict:
    if state.get("skip_description"):
        return {"skip_description": False}

    room = state["current_room_data"]
    room_id = state["current_room_id"]
    room_states = dict(state.get("room_states", {}))
    room_override = dict(room_states.get(room_id, {}))

    already_visited = room_override.get("visited", False)
    force_full_description = state.get("force_full_description", False)

    if not already_visited or force_full_description:
        visible = [i["name"] for i in room["items"] if not i["hidden"]]

        container_hints = []
        for i in room["items"]:
            if not i["hidden"] and i.get("openable") and not i.get("is_open"):
                container_hints.append(f"The {i['name']} looks like it might contain something")

        prompt = ROOM_DESCRIPTION_PROMPT.invoke({
            "name": room["name"],
            "description": room["description"],
            "items": ", ".join(visible) if visible else "none",
            "containers": ". ".join(container_hints) if container_hints else "none",
            "monsters": ", ".join(m["name"] for m in room["monsters"]) if room["monsters"] else "none",
            "npcs": ", ".join(n["name"] for n in room["npcs"]) if room["npcs"] else "none",
            "exits": ", ".join(room["exits"].keys()) if room["exits"] else "none",
        })

        response = invoke_with_system(llm, prompt)
        print(response.content)

        room_override["visited"] = True
        room_states[room_id] = room_override

        return {
            "room_states": room_states,
            "force_full_description": False
        }

    short_desc = (
        f"You are back in the {room['name']}. "
        f"Items: {', '.join(i['name'] for i in room['items'] if not i['hidden']) or 'none'}. "
        f"Exits: {', '.join(room['exits'].keys()) if room['exits'] else 'none'}."
    )
    print(short_desc)
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
        print(f"\nThe {monster['name']} lunges at you before you can react!")
        return {"route_to": "combat", "combat_target": monster["name"]}
    
    return {"route_to": None}

def trigger_win(state: AgentState) -> dict:
    player = state.get("player", {})
    inventory = list(player.get("inventory", []))

    prompt = WIN_PROMPT.invoke({
        "gold": player.get("gold", 0),
        "health": player.get("health", 100),
        "max_health": player.get("max_health", 100),
        "inventory": ", ".join(i["name"] for i in inventory) or "nothing",
        "monsters_defeated": "unknown",
    })

    response = invoke_with_system(llm, prompt)
    print("\n" + "═" * 50)
    print(response.content)
    print("═" * 50 + "\n")

    return {"game_won": True, "game_over": True}

def get_player_action(state: AgentState) -> dict:
    player_input = input("\nWhat do you do? ").strip().lower()
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
    })

    response = invoke_with_system(llm, prompt)

    try:
        text = response.content.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()
        parsed = json.loads(text)
        debug(f"parse_command: {parsed}")
        return parsed
    except Exception as e:
        debug(f"parse_command error: {e} | raw: {response.content}")
        return {"action": "unknown", "target": None}




def resolve_action(state: AgentState) -> dict:
    player_input = state.get("player_input", "").strip()

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
        "go":        lambda: handle_go(state, target),
        "take":      lambda: handle_take(state, target),
        "examine":   lambda: handle_examine(state, target, llm),
        "open":      lambda: handle_open(state, target),
        "equip":     lambda: handle_equip(state, target),
        "inventory": lambda: handle_inventory(state),
        "room":      lambda: handle_room(state),
        "look":      lambda: {"force_full_description": True},
        "talk":      lambda: {"route_to": "npc_dialogue"},
        "attack":    lambda: {"route_to": "combat", "combat_target": target},
        "quit":      lambda: (print("Goodbye.") or {"game_over": True}),
        "unequip":   lambda: handle_unequip(state, target),
        "use": lambda: handle_use(state, target),
        "win": lambda: trigger_win(state),
        "unlock": lambda: handle_unlock(state, target),
        "help": lambda: handle_help(),

    }

    handler = handlers.get(action)
    if handler:
        return handler()

    print("You're not sure how to do that.")
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
graph.add_node("combat", lambda state: combat_node(state, ROOMS, llm))
graph.add_node("npc_dialogue", lambda state: npc_dialogue(state, SHOPS, llm, parse_command))

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
graph.add_edge("combat", "load_room_data")

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

# ── Run ──────────────────────────────────────────────────────────────────────
'''

# Fresh game start
initial_state_1 = AgentState(
    current_room_id="room_1",
    player={
        "inventory": [],
        "health": 100,
        "max_health": 100,
        "status_effects": [],
        "gold": 0,
        "equipped_weapon": None,
        "equipped_armor": {}
    }
)


'''



# Testing state — fully loaded
initial_state_1 = AgentState(
    current_room_id="room_1",
    player={
        "inventory": [
            {"name": "golden sword",   "hidden": False, "revealed_by": None, "openable": False, "is_open": False, "gold": 0, "damage": 25, "weapon_type": "blade",  "armor_slot": None,     "armor_rating": 0, "heal_amount": 0},
            {"name": "magic staff",    "hidden": False, "revealed_by": None, "openable": False, "is_open": False, "gold": 0, "damage": 18, "weapon_type": "magic",  "armor_slot": None,     "armor_rating": 0, "heal_amount": 0},
            {"name": "leather armour", "hidden": False, "revealed_by": None, "openable": False, "is_open": False, "gold": 0, "damage": 0,  "weapon_type": None,     "armor_slot": "chest",  "armor_rating": 5, "heal_amount": 0},
            {"name": "iron helmet",    "hidden": False, "revealed_by": None, "openable": False, "is_open": False, "gold": 0, "damage": 0,  "weapon_type": None,     "armor_slot": "helmet", "armor_rating": 4, "heal_amount": 0},
            {"name": "sturdy boots",   "hidden": False, "revealed_by": None, "openable": False, "is_open": False, "gold": 0, "damage": 0,  "weapon_type": None,     "armor_slot": "boots",  "armor_rating": 3, "heal_amount": 0},
            {"name": "chain gloves",   "hidden": False, "revealed_by": None, "openable": False, "is_open": False, "gold": 0, "damage": 0,  "weapon_type": None,     "armor_slot": "gloves", "armor_rating": 2, "heal_amount": 0},
            {"name": "health potion",  "hidden": False, "revealed_by": None, "openable": False, "is_open": False, "gold": 0, "damage": 0,  "weapon_type": None,     "armor_slot": None,     "armor_rating": 0, "heal_amount": 50},
         ],
        "health": 50,
        "max_health": 100,
        "status_effects": [],
        "gold": 1000,
        "equipped_weapon": "golden sword",
        "equipped_armor": {
            "chest":  "leather armour",
            "helmet": "iron helmet",
            "boots":  "sturdy boots",
            "gloves": "chain gloves"
        }
    }
)

app.invoke(initial_state_1)

# Draw the graph
img = app.get_graph().draw_mermaid_png()
with open("graph.png", "wb") as f:
    f.write(img)
print("Saved graph.png")