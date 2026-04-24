# handlers/items.py
# Handles all item-related player actions.
# Contains:
#   handle_take    — pick up a visible item from the room
#   handle_examine — examine a monster, NPC, item, or the room itself
#   handle_open    — open a container and collect any gold inside
#   handle_equip   — equip a weapon or armour piece from inventory
#   handle_unequip — remove an equipped weapon or armour piece

from utils import find_item, find_npc, invoke_with_system, stream_with_system, debug, emit_player_state, get_mutable_player, get_mutable_room, add_journal_entry
from prompts import EXAMINE_PROMPT

def handle_use(state, target, io) -> dict:
    player, inventory = get_mutable_player(state)

    item = next((i for i in inventory if i["name"] == target), None)

    if not item:
        debug(f"use '{target}': not in inventory")
        io.send(f"You don't have {target} in your inventory.")
        return {"force_full_description": False}

    if item.get("heal_amount"):
        current_health = player.get("health", 100)
        max_health = player.get("max_health", 100)

        if current_health == max_health:
            debug(f"use '{target}': already at full health")
            io.send("You are already at full health.")
            return {"force_full_description": False}

        heal_amount = item.get("heal_amount", 50)
        healed = min(heal_amount, max_health - current_health)
        player["health"] = current_health + healed
        player["inventory"] = [i for i in inventory if i != item]
        debug(f"use '{target}': healed {healed} | health {current_health} → {player['health']}/{max_health}")
        io.send(f"You drink the health potion and recover {healed} health.")
        io.send(f"Health: {player['health']}/{max_health}")
        emit_player_state(player, state["current_room_id"], io, room_data=state.get("current_room_data"))
        return {
            "player": player,
            "force_full_description": False
        }

    debug(f"use '{target}': no effect defined")
    io.send(f"You can't use the {target}.")
    return {"force_full_description": False}

def handle_take(state, target, io) -> dict:
    room = state["current_room_data"]
    room_id = state["current_room_id"]
    room_states, room_override = get_mutable_room(state, room_id)
    player, inventory = get_mutable_player(state)

    item = find_item(room, target)
    if item:
        if item.get("openable"):
            debug(f"take '{target}': is a container, blocked")
            io.send(f"The {target} is too large to carry. Try opening it instead.")
            return {"force_full_description": False}
        new_items = [i for i in room["items"] if i["name"] != target]
        room_override["items"] = new_items
        room_states[room_id] = room_override
        inventory.append(item)
        player["inventory"] = inventory
        debug(f"take '{target}': added to inventory | inventory size: {len(inventory)}")
        io.send(f"You take the {target}.")
        emit_player_state(player, room_id, io, room_data=state.get("current_room_data"))
        return {
            "room_states": room_states,
            "player": player,
            "force_full_description": False,
            "last_entity": target,
        }
    debug(f"take '{target}': not found in {room_id}")
    io.send(f"There's no {target} here.")
    return {"force_full_description": False}


def handle_examine(state, target, llm, io, mini_llm=None) -> dict:
    room = state["current_room_data"]
    room_id = state["current_room_id"]
    room_states, room_override = get_mutable_room(state, room_id)

    debug(f"examine '{target}' in {room_id}")

    # Monster?
    monster = next((m for m in room["monsters"] if m["name"] == target), None)
    if monster:
        io.send(f"\n--- {monster['name'].upper()} ---")
        io.send(f"Health:     {monster['health']}/{monster['max_health']}")
        io.send(f"Defense:    {monster['defense']}")
        io.send(f"Damage:     {monster['damage']}")
        io.send(f"Weaknesses: {', '.join(monster['weaknesses']) if monster['weaknesses'] else 'none'}")
        return {"force_full_description": False}

    # NPC?
    npc = find_npc(room, target)
    if npc:
        io.send(f"\n{npc['name']}: {npc['description']}")
        return {"force_full_description": False}

    # Room itself?
    if target in ["room", "around", "surroundings", None]:
        return {"force_full_description": True}

    # Item
    player, inventory = get_mutable_player(state)
    new_items = []
    newly_collected = []
    for item in room["items"]:
        if item["hidden"] and item["revealed_by"] == target:
            inventory.append(item)
            newly_collected.append(item["name"])
        else:
            new_items.append(item)

    if newly_collected:
        player["inventory"] = inventory
        room_override["items"] = new_items
        room_states[room_id] = room_override
        debug(f"examine '{target}': collected {newly_collected}")
        emit_player_state(player, room_id, io, room_data=state.get("current_room_data"))
        if mini_llm:
            for name in newly_collected:
                add_journal_entry(f"Found {name} hidden inside the {target}.", player, room_id, io, mini_llm)
    else:
        debug(f"examine '{target}': nothing found")

    if newly_collected:
        discovery_instruction = (
            f"As the player examines the {target}, they find hidden within it: {', '.join(newly_collected)}. "
            f"Narrate the moment of discovery and picking it up naturally — describe what physical detail leads to finding it."
        )
    else:
        discovery_instruction = ""

    prompt = EXAMINE_PROMPT.invoke({
        "target": target,
        "room_name": room["name"],
        "room_description": room["description"],
        "discovery_instruction": discovery_instruction,
    })
    stream_with_system(llm, prompt, io)

    if newly_collected:
        return {
            "room_states": room_states,
            "player": player,
            "force_full_description": False,
            "last_entity": target,
        }

    return {"force_full_description": False, "last_entity": target}


def handle_open(state, target, io) -> dict:
    room = state["current_room_data"]
    room_id = state["current_room_id"]
    room_states, room_override = get_mutable_room(state, room_id)
    player, _ = get_mutable_player(state)

    item = find_item(room, target)

    if not item:
        io.send(f"There's no {target} here.")
        return {"force_full_description": False}

    if not item.get("openable"):
        io.send(f"You can't open the {target}.")
        return {"force_full_description": False}

    if item.get("is_open"):
        io.send(f"The {target} is already open.")
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
        io.send(f"You open the {target} and find {gold_found} gold coins inside!")
        io.send(f"You now have {player['gold']} gold.")
        emit_player_state(player, room_id, io, room_data=state.get("current_room_data"))
        return {
            "room_states": room_states,
            "player": player,
            "force_full_description": False,
            "last_entity": target,
        }

    debug(f"open '{target}': empty")
    io.send(f"You open the {target} but find nothing of value inside.")
    return {
        "room_states": room_states,
        "force_full_description": False,
        "last_entity": target,
    }


def handle_equip(state, target, io) -> dict:
    player, inventory = get_mutable_player(state)

    item_data = next((i for i in inventory if i["name"] == target), None)

    if not item_data:
        io.send(f"You don't have {target} in your inventory.")
        return {"force_full_description": False}

    if item_data.get("weapon_type"):
        prev = player.get("equipped_weapon")
        player["equipped_weapon"] = target
        debug(f"equip weapon: '{target}' ({item_data['weapon_type']}, {item_data['damage']} dmg) | replaced: {prev}")
        io.send(f"You equip the {target}. ({item_data['weapon_type']}, {item_data['damage']} damage)")
        emit_player_state(player, state["current_room_id"], io, room_data=state.get("current_room_data"))
        return {"player": player, "force_full_description": False}

    if item_data.get("armor_slot"):
        slot = item_data["armor_slot"]
        equipped_armor = dict(player.get("equipped_armor", {}))
        prev = equipped_armor.get(slot)
        if prev:
            io.send(f"You remove the {prev}.")
        equipped_armor[slot] = target
        player["equipped_armor"] = equipped_armor
        debug(f"equip armor: '{target}' → slot '{slot}' ({item_data['armor_rating']} rating) | replaced: {prev}")
        io.send(f"You equip the {target}. ({slot}, {item_data['armor_rating']} armor rating)")
        emit_player_state(player, state["current_room_id"], io, room_data=state.get("current_room_data"))
        return {"player": player, "force_full_description": False}

    debug(f"equip '{target}': not a weapon or armor")
    io.send(f"You can't equip the {target}.")
    return {"force_full_description": False}

def handle_unequip(state, target, io) -> dict:
    player = dict(state.get("player", {}))
    equipped_armor = dict(player.get("equipped_armor", {}))

    if player.get("equipped_weapon") == target:
        player["equipped_weapon"] = None
        debug(f"unequip weapon: '{target}'")
        io.send(f"You unequip the {target}.")
        emit_player_state(player, state["current_room_id"], io, room_data=state.get("current_room_data"))
        return {"player": player, "force_full_description": False}

    for slot, item_name in equipped_armor.items():
        if item_name == target:
            del equipped_armor[slot]
            player["equipped_armor"] = equipped_armor
            debug(f"unequip armor: '{target}' from slot '{slot}'")
            io.send(f"You remove the {target}.")
            emit_player_state(player, state["current_room_id"], io, room_data=state.get("current_room_data"))
            return {"player": player, "force_full_description": False}

    debug(f"unequip '{target}': not currently equipped")
    io.send(f"You don't have {target} equipped.")
    return {"force_full_description": False}