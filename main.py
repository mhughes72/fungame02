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

from prompts import (
    ROOM_DESCRIPTION_PROMPT, COMMAND_PARSER_PROMPT, NPC_PROMPT,
    WEB_SEARCH_ROLEPLAY_PROMPT, COMBAT_PROMPT, FLEE_PROMPT, 
    GAME_SYSTEM_PROMPT, SHOP_SYSTEM_PROMPT
)

from utils import invoke_with_system, find_item, visible_items, total_armor_rating
from handlers import handle_go, handle_take, handle_examine, handle_open, handle_equip, handle_unequip, handle_use, handle_inventory, handle_room

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

class RoomState(TypedDict, total=False):
    items: List[ItemData]
    monsters: List[MonsterData]
    visited: bool

class RoomData(TypedDict):
    name: str
    description: str
    exits: Dict[str, str]
    items: List[ItemData]
    monsters: List[MonsterData]
    npcs: List[NPCData]

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

# ── Setup ────────────────────────────────────────────────────────────────────

llm = ChatOpenAI(model="gpt-4o", temperature=0.5)

with open(os.path.join("data", "rooms.json"), "r") as f:
    ROOMS = json.load(f)

with open(os.path.join("data", "shop.json"), "r") as f:
    SHOPS = json.load(f)
    
# ── Nodes ────────────────────────────────────────────────────────────────────

def load_room_data(state: AgentState) -> dict:
    room_id = state["current_room_id"]
    base_room = ROOMS[room_id]
    room_override = state.get("room_states", {}).get(room_id, {})

    room: RoomData = {
        "name": base_room["name"],
        "description": base_room["description"],
        "exits": dict(base_room["exits"]),
        "monsters": list(room_override.get("monsters", base_room["monsters"])),
        "items": list(room_override.get("items", base_room["items"])),
        "npcs": list(base_room.get("npcs", [])),
    }

    return {
        "current_room_data": room,
        "route_to": None
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
        print(f"[DEBUG] Parsed command: {parsed}")
        return parsed
    except Exception as e:
        print(f"[DEBUG] Parse error: {e}, raw response: {response.content}")
        return {"action": "unknown", "target": None}


def npc_dialogue(state: AgentState) -> dict:
    room = state["current_room_data"]
    player_input = state.get("player_input", "").strip()

    command = parse_command(player_input, state)
    target = command.get("target", "").lower() if command.get("target") else ""

    npc = next(
        (n for n in room["npcs"] if n["name"].lower() in target or target in n["name"].lower()),
        room["npcs"][0] if room["npcs"] else None
    )

    if not npc:
        print("There's no one here to talk to.")
        return {"force_full_description": False}

    # Route to shop if NPC is a merchant
    if npc.get("shop_id"):
        from handlers.shop import handle_shop
        return handle_shop(state, npc, SHOPS, llm)

    print(f"\n{npc['name']}: \"{npc['description']}\"")
    print("(Type 'goodbye' or 'leave' to end the conversation)\n")

    use_web_search = npc.get("can_search_web", False)
    tavily_client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY")) if use_web_search else None

    history = []
    exit_words = ["goodbye", "bye", "leave", "exit", "done", "farewell", "stop"]

    while True:
        player_msg = input("You: ").strip()
        history.append(f"Player: {player_msg}")

        if any(word in player_msg.lower() for word in exit_words):
            print(f"({npc['name']} turns away.)")
            break

        if use_web_search:
            search_result = tavily_client.search(player_msg)
            raw_facts = "\n".join([r["content"] for r in search_result["results"]])

            roleplay_prompt = WEB_SEARCH_ROLEPLAY_PROMPT.format(
                npc_name=npc["name"],
                personality=npc["personality"],
                knowledge=npc["knowledge"],
                player_msg=player_msg,
                raw_facts=raw_facts,
                history=chr(10).join(history),
            )
            reply = invoke_with_system(llm, [
                SystemMessage(content=roleplay_prompt),
                HumanMessage(content="Respond in character now.")
            ]).content

        else:
            prompt = NPC_PROMPT.invoke({
                "npc_name": npc["name"],
                "personality": npc["personality"],
                "knowledge": npc["knowledge"],
                "room_name": room["name"],
                "history": "\n".join(history),
                "player_input": player_msg,
            })
            reply = str(invoke_with_system(llm, prompt).content)

        end_conversation = "[END CONVERSATION]" in reply
        clean_reply = reply.replace("[END CONVERSATION]", "").strip()

        print(f"\n{npc['name']}: {clean_reply}\n")
        history.append(f"{npc['name']}: {clean_reply}")

        if end_conversation:
            print(f"({npc['name']} turns away.)")
            break

    return {"force_full_description": False}


def combat_node(state: AgentState) -> dict:
    target = state.get("combat_target")
    room = state["current_room_data"]
    room_id = state["current_room_id"]
    room_states = dict(state.get("room_states", {}))
    room_override = dict(room_states.get(room_id, {}))
    player = dict(state.get("player", {}))
    inventory = list(player.get("inventory", []))

    monster = next((m for m in room["monsters"] if m["name"] == target), None)
    if not monster:
        print(f"There is no {target} here.")
        return {"force_full_description": False, "route_to": None}

    monster = dict(monster)

    # Find weapon data from inventory
    equipped_weapon = player.get("equipped_weapon")
    weapon_data = None
    if equipped_weapon:
        weapon_data = next(
            (i for i in player.get("inventory", []) if i["name"] == equipped_weapon),
            None
        )

    print(f"\nYou engage the {monster['name']}!")
    print(f"Your health: {player['health']}/{player['max_health']}")
    print(f"Weapon: {equipped_weapon if equipped_weapon else 'bare hands (1 damage)'}\n")

    while monster["health"] > 0 and player["health"] > 0:
        print(f"\n[{monster['name'].upper()} — HP: {monster['health']}/{monster['max_health']}]")
        print(f"[YOUR HP: {player['health']}/{player['max_health']}]")
        print("What do you do? (attack / flee)")
        combat_input = input("> ").strip().lower()

        if any(word in combat_input for word in ["flee", "run", "escape", "retreat"]):
            flee_chance = random.randint(1, 100)
            success = flee_chance > 40

            prompt = FLEE_PROMPT.invoke({
                "room_name": room["name"],
                "monster_name": monster["name"],
                "success": success,
            })
            response = invoke_with_system(llm, prompt)
            print(f"\n{response.content}")

            if success:
                print("\n[You escaped!]")
                return {
                    "player": player,
                    "route_to": None,
                    "force_full_description": False,
                    "skip_description": True
                }
            else:
                monster_dmg = max(0, monster["damage"] - random.randint(0, 3))
                player["health"] -= monster_dmg
                print(f"[Failed to flee — {monster['name']} hits you for {monster_dmg} damage]")
                if player["health"] <= 0:
                    print("\nYou have been slain. Game over.")
                    return {
                        "player": player,
                        "game_over": True,
                        "route_to": None
                    }
                continue

        base_damage = weapon_data.get("damage", 1) if weapon_data else 1
        dice_roll = random.randint(1, 6)
        weakness_bonus = 0

        if weapon_data and weapon_data.get("weapon_type") in monster.get("weaknesses", []):
            weakness_bonus = 5
            print(f"[Weakness hit! +{weakness_bonus} bonus damage]")

        player_dmg = max(0, base_damage + dice_roll + weakness_bonus - monster["defense"])
        monster["health"] -= player_dmg

        monster_dmg = 0
        if monster["health"] > 0:
            variance = monster.get("damage_variance", 2)
            armor = total_armor_rating(player, player.get("inventory", []))
            raw_dmg = monster["damage"] + random.randint(-variance, variance)
            damage_reduction = min(0.75, armor * 0.05)
            monster_dmg = max(1, int(raw_dmg * (1 - damage_reduction)))
            player["health"] -= monster_dmg

        round_events = f"Player dealt {player_dmg} damage to the {monster['name']}."
        if monster["health"] > 0:
            round_events += f" The {monster['name']} struck back for {monster_dmg} damage."
        else:
            round_events += f" The {monster['name']} has been defeated."

        prompt = COMBAT_PROMPT.invoke({
            "room_name": room["name"],
            "player_health": player["health"],
            "player_max_health": player["max_health"],
            "weapon": equipped_weapon if equipped_weapon else "bare hands",
            "monster_name": monster["name"],
            "monster_health": max(0, monster["health"]),
            "monster_max_health": monster["max_health"],
            "round_events": round_events,
        })
        response = invoke_with_system(llm, prompt)
        print(f"\n{response.content}")

        if player["health"] <= 0:
            print("\nYou have been slain. Game over.")
            return {
                "player": player,
                "game_over": True,
                "route_to": None
            }

    print(f"\n[{monster['name'].upper()} DEFEATED]")

    drops = monster.get("drops", {})
    gold_drop = drops.get("gold", 0)
    item_drop = drops.get("item", None)

    if gold_drop > 0:
        player["gold"] = player.get("gold", 0) + gold_drop
        print(f"You find {gold_drop} gold coins.")

    if item_drop:
        drop_item_data = next(
            (i for i in room["items"] if i["name"] == item_drop),
            {"name": item_drop, "hidden": False, "revealed_by": None,
             "openable": False, "is_open": False, "gold": 0,
             "damage": 0, "weapon_type": None}
        )
        inventory.append(drop_item_data)
        player["inventory"] = inventory
        print(f"You find: {item_drop}")

    new_monsters = [m for m in room["monsters"] if m["name"] != target]
    room_override["monsters"] = new_monsters
    room_states[room_id] = room_override

    return {
        "room_states": room_states,
        "player": player,
        "route_to": None,
        "force_full_description": False,
        "skip_description": True
    }


def resolve_action(state: AgentState) -> dict:
    player_input = state.get("player_input", "").strip()

    # Debug teleport
    if player_input.startswith("goto "):
        target_room = player_input.split(" ")[1].strip()
        if target_room in ROOMS:
            print(f"[DEBUG] Teleporting to {target_room}")
            return {"current_room_id": target_room, "force_full_description": True}
        print(f"[DEBUG] Room '{target_room}' not found.")
        return {"force_full_description": False}

    command = parse_command(player_input, state)
    action = command.get("action", "unknown")
    target = command.get("target")
    print(f"\n[Input: '{player_input}' → action: '{action}', target: '{target}']")

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
    }

    handler = handlers.get(action)
    if handler:
        return handler()

    print("You're not sure how to do that.")
    return {"force_full_description": False}


def next_step(state: AgentState) -> str:
    if state.get("game_over"):
        return END
    if state.get("route_to") == "npc_dialogue":
        return "npc_dialogue"
    if state.get("route_to") == "combat":
        return "combat"
    return "load_room_data"


# ── Graph ────────────────────────────────────────────────────────────────────

graph = StateGraph(AgentState)
graph.add_node("load_room_data", load_room_data)
graph.add_node("describe_room", describe_room)
graph.add_node("get_player_action", get_player_action)
graph.add_node("resolve_action", resolve_action)
graph.add_node("npc_dialogue", npc_dialogue)
graph.add_node("combat", combat_node)

graph.add_edge(START, "load_room_data")
graph.add_edge("load_room_data", "describe_room")
graph.add_edge("describe_room", "get_player_action")
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
            {"name": "rusty key",      "hidden": False, "revealed_by": None, "openable": False, "is_open": False, "gold": 0, "damage": 0,  "weapon_type": None,     "armor_slot": None,     "armor_rating": 0, "heal_amount": 0},
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