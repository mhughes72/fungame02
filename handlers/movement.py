# handlers/movement.py
# Handles player movement between rooms.
# Contains handle_go which checks if the target direction is a valid exit
# and returns the new room ID if so.

def handle_go(state, target, io) -> dict:
    from utils import debug, get_mutable_player, get_mutable_room
    room = state["current_room_data"]
    room_id = state["current_room_id"]
    locked_exits = room.get("locked_exits", {})

    if target in room["exits"]:
        if target in locked_exits and locked_exits[target].get("locked"):
            required_key = locked_exits[target].get("required_key")
            _, inventory = get_mutable_player(state)
            player_keys = [i["name"] for i in inventory]
            if required_key and required_key in player_keys:
                # Auto-unlock and proceed
                room_states, room_override = get_mutable_room(state, room_id)
                new_locks = dict(locked_exits)
                new_locks[target] = {**locked_exits[target], "locked": False}
                room_override["locked_exits"] = new_locks
                room_states[room_id] = room_override
                dest = room["exits"][target]
                debug(f"go {target}: auto-unlock with '{required_key}' | {room_id} → {dest}")
                io.send(f"You use the {required_key} to unlock the door and head {target}.")
                return {
                    "current_room_id": dest,
                    "room_states": room_states,
                    "force_full_description": False
                }
            debug(f"go {target}: blocked — requires '{required_key}'")
            io.send(f"The door to the {target} is locked.")
            return {"force_full_description": False}

        dest = room["exits"][target]
        debug(f"go {target}: {room_id} → {dest}")
        io.send(f"You head {target}.")
        return {
            "current_room_id": dest,
            "force_full_description": False
        }

    debug(f"go {target}: no exit in {room_id}")
    io.send(f"You can't go {target} from here.")
    return {"force_full_description": False}

def handle_unlock(state, target, io) -> dict:
    from utils import debug, get_mutable_player, get_mutable_room
    room = state["current_room_data"]
    room_id = state["current_room_id"]
    room_states, room_override = get_mutable_room(state, room_id)
    player, inventory = get_mutable_player(state)

    locked_exits = dict(room.get("locked_exits", {}))

    if target not in locked_exits:
        debug(f"unlock {target}: no locked exit in {room_id}")
        io.send(f"There is no locked door to the {target}.")
        return {"force_full_description": False}

    exit_data = locked_exits[target]

    if not exit_data.get("locked"):
        debug(f"unlock {target}: already unlocked")
        io.send(f"The door to the {target} is already unlocked.")
        return {"force_full_description": False}

    required_key = exit_data.get("required_key")
    player_keys = [i["name"] for i in inventory]
    debug(f"unlock {target}: requires '{required_key}' | player has: {player_keys}")

    if required_key not in player_keys:
        io.send(f"The door to the {target} is locked.")
        return {"force_full_description": False}

    locked_exits[target] = {**exit_data, "locked": False}
    room_override["locked_exits"] = locked_exits
    room_states[room_id] = room_override

    debug(f"unlock {target}: success with '{required_key}'")
    io.send(f"You use the {required_key} to unlock the door to the {target}.")
    return {
        "room_states": room_states,
        "force_full_description": False
    }
