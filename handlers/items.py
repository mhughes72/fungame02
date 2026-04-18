# handlers/items.py
# Handles all item-related player actions.
# Contains:
#   handle_take    — pick up a visible item from the room
#   handle_examine — examine a monster, NPC, item, or the room itself
#   handle_open    — open a container and collect any gold inside
#   handle_equip   — equip a weapon or armour piece from inventory
#   handle_unequip — remove an equipped weapon or armour piece

from utils import find_item, invoke_with_system, debug
from prompts import EXAMINE_PROMPT

def handle_use(state, target) -> dict:
    player = dict(state.get("player", {}))
    inventory = list(player.get("inventory", []))

    item = next((i for i in inventory if i["name"] == target), None)

    if not item:
        debug(f"use '{target}': not in inventory")
        print(f"You don't have {target} in your inventory.")
        return {"force_full_description": False}

    if item.get("heal_amount"):
        current_health = player.get("health", 100)
        max_health = player.get("max_health", 100)

        if current_health == max_health:
            debug(f"use '{target}': already at full health")
            print("You are already at full health.")
            return {"force_full_description": False}

        heal_amount = item.get("heal_amount", 50)
        healed = min(heal_amount, max_health - current_health)
        player["health"] = current_health + healed
        player["inventory"] = [i for i in inventory if i != item]
        debug(f"use '{target}': healed {healed} | health {current_health} → {player['health']}/{max_health}")
        print(f"You drink the health potion and recover {healed} health.")
        print(f"Health: {player['health']}/{max_health}")
        return {
            "player": player,
            "force_full_description": False
        }

    debug(f"use '{target}': no effect defined")
    print(f"You can't use the {target}.")
    return {"force_full_description": False}

def handle_take(state, target) -> dict:
    room = state["current_room_data"]
    room_id = state["current_room_id"]
    room_states = dict(state.get("room_states", {}))
    room_override = dict(room_states.get(room_id, {}))
    player = dict(state.get("player", {}))
    inventory = list(player.get("inventory", []))

    item = find_item(room, target)
    if item:
        if item.get("openable"):
            debug(f"take '{target}': is a container, blocked")
            print(f"The {target} is too large to carry. Try opening it instead.")
            return {"force_full_description": False}
        new_items = [i for i in room["items"] if i["name"] != target]
        room_override["items"] = new_items
        room_states[room_id] = room_override
        inventory.append(item)
        player["inventory"] = inventory
        debug(f"take '{target}': added to inventory | inventory size: {len(inventory)}")
        print(f"You take the {target}.")
        return {
            "room_states": room_states,
            "player": player,
            "force_full_description": False
        }
    debug(f"take '{target}': not found in {room_id}")
    print(f"There's no {target} here.")
    return {"force_full_description": False}


def handle_examine(state, target, llm) -> dict:
    room = state["current_room_data"]
    room_id = state["current_room_id"]
    room_states = dict(state.get("room_states", {}))
    room_override = dict(room_states.get(room_id, {}))

    debug(f"examine '{target}' in {room_id}")

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
        debug(f"examine '{target}': revealed {newly_revealed}")
    else:
        debug(f"examine '{target}': nothing revealed")

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
        debug(f"open '{target}': found {gold_found}g | total gold: {player['gold']}")
        print(f"You open the {target} and find {gold_found} gold coins inside!")
        print(f"You now have {player['gold']} gold.")
        return {
            "room_states": room_states,
            "player": player,
            "force_full_description": False
        }

    debug(f"open '{target}': empty")
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

    if item_data.get("weapon_type"):
        prev = player.get("equipped_weapon")
        player["equipped_weapon"] = target
        debug(f"equip weapon: '{target}' ({item_data['weapon_type']}, {item_data['damage']} dmg) | replaced: {prev}")
        print(f"You equip the {target}. ({item_data['weapon_type']}, {item_data['damage']} damage)")
        return {"player": player, "force_full_description": False}

    if item_data.get("armor_slot"):
        slot = item_data["armor_slot"]
        equipped_armor = dict(player.get("equipped_armor", {}))
        prev = equipped_armor.get(slot)
        if prev:
            print(f"You remove the {prev}.")
        equipped_armor[slot] = target
        player["equipped_armor"] = equipped_armor
        debug(f"equip armor: '{target}' → slot '{slot}' ({item_data['armor_rating']} rating) | replaced: {prev}")
        print(f"You equip the {target}. ({slot}, {item_data['armor_rating']} armor rating)")
        return {"player": player, "force_full_description": False}

    debug(f"equip '{target}': not a weapon or armor")
    print(f"You can't equip the {target}.")
    return {"force_full_description": False}

def handle_unequip(state, target) -> dict:
    player = dict(state.get("player", {}))
    equipped_armor = dict(player.get("equipped_armor", {}))

    if player.get("equipped_weapon") == target:
        player["equipped_weapon"] = None
        debug(f"unequip weapon: '{target}'")
        print(f"You unequip the {target}.")
        return {"player": player, "force_full_description": False}

    for slot, item_name in equipped_armor.items():
        if item_name == target:
            del equipped_armor[slot]
            player["equipped_armor"] = equipped_armor
            debug(f"unequip armor: '{target}' from slot '{slot}'")
            print(f"You remove the {target}.")
            return {"player": player, "force_full_description": False}

    debug(f"unequip '{target}': not currently equipped")
    print(f"You don't have {target} equipped.")
    return {"force_full_description": False}