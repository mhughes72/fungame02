from utils import find_item, invoke_with_system
from prompts import EXAMINE_PROMPT

def handle_take(state, target) -> dict:
    room = state["current_room_data"]
    room_id = state["current_room_id"]
    room_states = dict(state.get("room_states", {}))
    room_override = dict(room_states.get(room_id, {}))
    player = dict(state.get("player", {}))
    inventory = list(player.get("inventory", []))

    item = find_item(room, target)
    if item:
        new_items = [i for i in room["items"] if i["name"] != target]
        room_override["items"] = new_items
        room_states[room_id] = room_override
        inventory.append(item)  # store full ItemData dict
        player["inventory"] = inventory
        print(f"You take the {target}.")
        return {
            "room_states": room_states,
            "player": player,
            "force_full_description": False
        }
    print(f"There's no {target} here.")
    return {"force_full_description": False}


def handle_examine(state, target, llm) -> dict:
    room = state["current_room_data"]
    room_id = state["current_room_id"]
    room_states = dict(state.get("room_states", {}))
    room_override = dict(room_states.get(room_id, {}))

    # Monster?
    monster = next((m for m in room["monsters"] if m["name"] == target), None)
    if monster:
        print(f"\n--- {monster['name'].upper()} ---")
        print(f"Health:     {monster['health']}/{monster['max_health']}")
        print(f"Defense:    {monster['defense']}")
        print(f"Damage:     {monster['damage']}")
        print(f"Weaknesses: {', '.join(monster['weaknesses']) if monster['weaknesses'] else 'none'}")
        return {"force_full_description": False}

    # NPC?
    npc = next((n for n in room["npcs"] if n["name"].lower() == target), None)
    if npc:
        print(f"\n{npc['name']}: {npc['description']}")
        return {"force_full_description": False}

    # Room itself?
    if target in ["room", "around", "surroundings", None]:
        return {"force_full_description": True}

    # Item
    new_items = []
    newly_revealed = []
    for item in room["items"]:
        if item["hidden"] and item["revealed_by"] == target:
            new_items.append({**item, "hidden": False})
            newly_revealed.append(item["name"])
        else:
            new_items.append(item)

    if newly_revealed:
        room_override["items"] = new_items
        room_states[room_id] = room_override

    discovery_text = f"The player discovers: {', '.join(newly_revealed)}" if newly_revealed else ""

    prompt = EXAMINE_PROMPT.invoke({
        "target": target,
        "room_name": room["name"],
        "room_description": room["description"],
        "discovery_text": discovery_text,
    })
    response = invoke_with_system(llm, prompt)
    print(response.content)

    if newly_revealed:
        print(f"\n[You find: {', '.join(newly_revealed)}]")
        return {
            "room_states": room_states,
            "force_full_description": False
        }

    return {"force_full_description": False}


def handle_open(state, target) -> dict:
    room = state["current_room_data"]
    room_id = state["current_room_id"]
    room_states = dict(state.get("room_states", {}))
    room_override = dict(room_states.get(room_id, {}))
    player = dict(state.get("player", {}))

    item = find_item(room, target)

    if not item:
        print(f"There's no {target} here.")
        return {"force_full_description": False}

    if not item.get("openable"):
        print(f"You can't open the {target}.")
        return {"force_full_description": False}

    if item.get("is_open"):
        print(f"The {target} is already open.")
        return {"force_full_description": False}

    gold_found = item.get("gold", 0)

    new_items = []
    for i in room["items"]:
        if i["name"] == target:
            new_items.append({**i, "is_open": True, "gold": 0})
        else:
            new_items.append(i)

    room_override["items"] = new_items
    room_states[room_id] = room_override

    if gold_found > 0:
        player["gold"] = player.get("gold", 0) + gold_found
        print(f"You open the {target} and find {gold_found} gold coins inside!")
        print(f"You now have {player['gold']} gold.")
        return {
            "room_states": room_states,
            "player": player,
            "force_full_description": False
        }

    print(f"You open the {target} but find nothing of value inside.")
    return {
        "room_states": room_states,
        "force_full_description": False
    }


def handle_equip(state, target) -> dict:
    player = dict(state.get("player", {}))
    inventory = list(player.get("inventory", []))

    item_data = next((i for i in inventory if i["name"] == target), None)

    if not item_data:
        print(f"You don't have {target} in your inventory.")
        return {"force_full_description": False}

    if not item_data.get("weapon_type"):
        print(f"The {target} is not a weapon.")
        return {"force_full_description": False}

    player["equipped_weapon"] = target
    print(f"You equip the {target}. ({item_data['weapon_type']}, {item_data['damage']} damage)")
    return {
        "player": player,
        "force_full_description": False
    }