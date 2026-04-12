def handle_go(state, target) -> dict:
    room = state["current_room_data"]
    if target in room["exits"]:
        print(f"You head {target}.")
        return {
            "current_room_id": room["exits"][target],
            "force_full_description": False
        }
    print(f"You can't go {target} from here.")
    return {"force_full_description": False}