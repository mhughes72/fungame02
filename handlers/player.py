def handle_inventory(state) -> dict:
    player = state.get("player", {})
    inventory = list(player.get("inventory", []))

    if inventory:
        print("You are carrying: " + ", ".join(i["name"] for i in inventory))
    else:
        print("Your inventory is empty.")
    equipped = player.get("equipped_weapon")
    print(f"Equipped weapon: {equipped if equipped else 'none'}")
    print(f"Gold: {player.get('gold', 0)} coins")
    return {"force_full_description": False}


def handle_room(state) -> dict:
    room = state["current_room_data"]

    visible = [i for i in room["items"] if not i["hidden"]]
    hidden = [i for i in room["items"] if i["hidden"]]

    print(f"\n--- {room['name']} ---")
    print(f"Visible items: {', '.join(i['name'] for i in visible) if visible else 'none'}")

    if hidden:
        print("Hidden items:")
        for i in hidden:
            print(f"  - {i['name']} (hidden behind: {i['revealed_by']})")
    else:
        print("Hidden items: none")

    containers = [i for i in visible if i.get("openable")]
    if containers:
        print("Containers:")
        for i in containers:
            if i.get("is_open"):
                print(f"  - {i['name']} (open, empty)")
            else:
                print(f"  - {i['name']} (unopened, contains {i.get('gold', 0)} gold)")

    print(f"Monsters: {', '.join(m['name'] for m in room['monsters']) if room['monsters'] else 'none'}")
    print(f"NPCs:     {', '.join(n['name'] for n in room['npcs']) if room['npcs'] else 'none'}")
    print(f"Exits:    {', '.join(room['exits'].keys())}")
    return {"force_full_description": False}