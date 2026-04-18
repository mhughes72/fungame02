
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

    print(f"\n--- PLAYER STATUS ---")
    
    # Health
    health = player.get("health", 100)
    max_health = player.get("max_health", 100)
    health_bar = "█" * int((health / max_health) * 10) + "░" * (10 - int((health / max_health) * 10))
    print(f"Health:   {health}/{max_health} [{health_bar}]")
    
    # Gold
    print(f"Gold:     {player.get('gold', 0)} coins")

    # Equipped weapon
    equipped_weapon = player.get("equipped_weapon")
    print(f"Weapon:   {equipped_weapon if equipped_weapon else 'none'}")

    # Equipped armour
    equipped_armor = player.get("equipped_armor", {})
    if equipped_armor:
        print("Armour:")
        for slot, item_name in equipped_armor.items():
            item = next((i for i in inventory if i["name"] == item_name), None)
            rating = item.get("armor_rating", 0) if item else 0
            print(f"  {slot:<8} {item_name} ({rating} armor)")
    else:
        print("Armour:   none")

    # Total armor rating
    from utils import total_armor_rating
    print(f"Total armor rating: {total_armor_rating(player, inventory)}")

    # Inventory
    carried = [i for i in inventory if not i.get("armor_slot") and i["name"] != equipped_weapon]
    if carried:
        print("Carrying:")
        for i in carried:
            if i.get("weapon_type"):
                print(f"  {i['name']} ({i['weapon_type']}, {i['damage']} damage)")
            elif i.get("heal_amount"):
                print(f"  {i['name']} (restores {i['heal_amount']} health)")
            else:
                print(f"  {i['name']}")
    else:
        print("Carrying: nothing")

    # Status effects
    status = player.get("status_effects", [])
    if status:
        print(f"Status:   {', '.join(status)}")

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

    locked_exits = room.get("locked_exits", {})
    exits_display = []
    for direction in room["exits"]:
        if direction in locked_exits and locked_exits[direction].get("locked"):
            exits_display.append(f"{direction} (locked)")
        else:
            exits_display.append(direction)
    print(f"Exits:    {', '.join(exits_display)}")

    print(f"Armor rating: {total_armor_rating(player, inventory)}")
    return {"force_full_description": False}


def handle_help() -> dict:
    print("""
--- COMMANDS ---

Movement:
  north / south / east / west / up / down
  go north / walk south / head east

Items:
  take [item]       pick up an item
  examine [item]    examine an item, monster, NPC or the room
  open [item]       open a container
  equip [item]      equip a weapon or armour
  unequip [item]    remove equipped item
  use [item]        use an item (e.g. health potion)
  unlock [dir]      unlock a door in that direction

Combat:
  attack [monster]  attack a monster
  attack / hit      attack during combat
  flee / run        attempt to flee combat

NPCs:
  talk [name]       talk to an NPC or merchant
  goodbye / bye     end a conversation

Info:
  inventory         show inventory, equipment and gold
  room              show full room state
  look              re-describe the current room
  help              show this list

Debug:
  goto room_X       teleport to a room
  win               trigger win condition
  clearmemory       wipe all NPC memories
  quit              exit the game
""")
    return {"force_full_description": False}


