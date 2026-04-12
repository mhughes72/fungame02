
# handlers/player.py
# Handles player status display actions.
# Contains:
#   handle_inventory — display carried items, equipped weapon, armor and gold
#   handle_room      — display full debug-style room state including hidden items,
#                      containers, monsters, NPCs, exits and total armor rating

from utils import total_armor_rating


def handle_inventory(state) -> dict:
    player = state.get("player", {})
    inventory = list(player.get("inventory", []))

    if inventory:
        print("You are carrying: " + ", ".join(i["name"] for i in inventory))
    else:
        print("Your inventory is empty.")

    equipped_weapon = player.get("equipped_weapon")
    print(f"Equipped weapon: {equipped_weapon if equipped_weapon else 'none'}")

    equipped_armor = player.get("equipped_armor", {})
    if equipped_armor:
        print("Equipped armour:")
        for slot, item_name in equipped_armor.items():
            print(f"  {slot}: {item_name}")
    else:
        print("Equipped armour: none")

    print(f"Gold: {player.get('gold', 0)} coins")
    return {"force_full_description": False}


def handle_room(state) -> dict:
    room = state["current_room_data"]
    player = state.get("player", {})
    inventory = list(player.get("inventory", []))

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
    print(f"Armor rating: {total_armor_rating(player, inventory)}")
    return {"force_full_description": False}