# handlers/movement.py
# Handles player movement between rooms.
# Contains handle_go which checks if the target direction is a valid exit
# and returns the new room ID if so.

def handle_go(state, target) -> dict:
    from utils import debug
    room = state["current_room_data"]
    locked_exits = room.get("locked_exits", {})

    if target in room["exits"]:
        if target in locked_exits and locked_exits[target].get("locked"):
            required_key = locked_exits[target].get("required_key")
            debug(f"go {target}: blocked — requires '{required_key}'")
            print(f"The door to the {target} is locked.")
            return {"force_full_description": False}

        dest = room["exits"][target]
        debug(f"go {target}: {state['current_room_id']} → {dest}")
        print(f"You head {target}.")
        return {
            "current_room_id": dest,
            "force_full_description": False
        }

    debug(f"go {target}: no exit in {state['current_room_id']}")
    print(f"You can't go {target} from here.")
    return {"force_full_description": False}

def handle_unlock(state, target) -> dict:
    from utils import debug
    room = state["current_room_data"]
    room_id = state["current_room_id"]
    room_states = dict(state.get("room_states", {}))
    room_override = dict(room_states.get(room_id, {}))
    player = dict(state.get("player", {}))
    inventory = list(player.get("inventory", []))

    locked_exits = dict(room.get("locked_exits", {}))

    if target not in locked_exits:
        debug(f"unlock {target}: no locked exit in {room_id}")
        print(f"There is no locked door to the {target}.")
        return {"force_full_description": False}

    exit_data = locked_exits[target]

    if not exit_data.get("locked"):
        debug(f"unlock {target}: already unlocked")
        print(f"The door to the {target} is already unlocked.")
        return {"force_full_description": False}

    required_key = exit_data.get("required_key")
    player_keys = [i["name"] for i in inventory]
    debug(f"unlock {target}: requires '{required_key}' | player has: {player_keys}")

    if required_key not in player_keys:
        print(f"The door to the {target} is locked.")
        return {"force_full_description": False}

    locked_exits[target] = {**exit_data, "locked": False}
    room_override["locked_exits"] = locked_exits
    room_states[room_id] = room_override

    debug(f"unlock {target}: success with '{required_key}'")
    print(f"You use the {required_key} to unlock the door to the {target}.")
    return {
        "room_states": room_states,
        "force_full_description": False
    }
    
    
    