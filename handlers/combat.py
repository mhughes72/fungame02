# handlers/combat.py
# Handles turn-based combat between the player and monsters.
# Contains combat_node which manages the full combat loop including
# player attacks, monster retaliation, flee attempts, weapon damage,
# armor reduction, weakness bonuses, and monster drop handling.

import random
from utils import (invoke_with_system, total_armor_rating, debug,
                   FLEE_SUCCESS_THRESHOLD, WEAKNESS_BONUS_DAMAGE,
                   ARMOR_REDUCTION_RATE, ARMOR_REDUCTION_CAP)
from prompts import COMBAT_PROMPT, FLEE_PROMPT

def combat_node(state, ROOMS, llm) -> dict:
    target = state.get("combat_target")
    room = state["current_room_data"]
    room_id = state["current_room_id"]
    room_states = dict(state.get("room_states", {}))
    room_override = dict(room_states.get(room_id, {}))
    player = dict(state.get("player", {}))
    inventory = list(player.get("inventory", []))

    debug(f"combat: targeting '{target}' in {room_id}")
    monster = next((m for m in room["monsters"] if m["name"] == target), None)
    if not monster:
        debug(f"combat: '{target}' not found in room")
        print(f"There is no {target} here.")
        return {"force_full_description": False, "route_to": None}

    monster = dict(monster)

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
            success = flee_chance > FLEE_SUCCESS_THRESHOLD

            prompt = FLEE_PROMPT.invoke({
                "room_name": room["name"],
                "monster_name": monster["name"],
                "success": success,
            })
            response = invoke_with_system(llm, prompt)
            print(f"\n{response.content}")

            if success:
                previous_room = state.get("previous_room_id")
                debug(f"flee: success | returning to {previous_room}")

                # Save wounded monster back to room state
                updated_monsters = []
                for m in room["monsters"]:
                    if m["name"] == target:
                        updated_monsters.append({**m, "health": monster["health"]})
                    else:
                        updated_monsters.append(m)
                room_override["monsters"] = updated_monsters
                room_states[room_id] = room_override

                print("\n[You escaped back the way you came!]")
                return {
                    "player": player,
                    "route_to": None,
                    "force_full_description": True,
                    "skip_description": False,
                    "current_room_id": previous_room if previous_room else state["current_room_id"],
                    "just_fled": True,
                    "room_states": room_states
                }
            else:
                variance = monster.get("damage_variance", 2)
                monster_dmg = max(0, monster["damage"] + random.randint(-variance, variance))
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
            weakness_bonus = WEAKNESS_BONUS_DAMAGE
            print(f"[Weakness hit! +{weakness_bonus} bonus damage]")

        player_dmg = max(0, base_damage + dice_roll + weakness_bonus - monster["defense"])
        monster["health"] -= player_dmg

        monster_dmg = 0
        if monster["health"] > 0:
            variance = monster.get("damage_variance", 2)
            armor = total_armor_rating(player, player.get("inventory", []))
            raw_dmg = monster["damage"] + random.randint(-variance, variance)
            damage_reduction = min(ARMOR_REDUCTION_CAP, armor * ARMOR_REDUCTION_RATE)
            monster_dmg = max(1, int(raw_dmg * (1 - damage_reduction)))
            player["health"] -= monster_dmg

        debug(f"round: player dealt {player_dmg} (base {base_damage}+d6({dice_roll})+weak({weakness_bonus})-def({monster['defense']})) | {monster['name']} hp: {max(0, monster['health'])}/{monster['max_health']} | monster dealt {monster_dmg} | player hp: {player['health']}/{player['max_health']}")

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

    debug(f"combat: '{target}' defeated | drops: {gold_drop}g, item: {item_drop}")
    if gold_drop > 0:
        player["gold"] = player.get("gold", 0) + gold_drop
        print(f"You find {gold_drop} gold coins.")

    if item_drop:
        drop_item_data = next(
            (i for i in room["items"] if i["name"] == item_drop),
            {"name": item_drop, "hidden": False, "revealed_by": None,
             "openable": False, "is_open": False, "gold": 0,
             "damage": 0, "weapon_type": None, "armor_slot": None,
             "armor_rating": 0, "heal_amount": 0}
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