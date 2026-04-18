# handlers/__init__.py
# Exports all action handler functions for use in resolve_action.
# Imports from movement.py, items.py, player.py, and shop.py.

from handlers.movement import handle_go, handle_unlock
from handlers.items import handle_take, handle_examine, handle_open, handle_equip, handle_unequip, handle_use
from handlers.player import handle_inventory, handle_room, handle_help
from handlers.combat import combat_node
from handlers.dialogue import npc_dialogue, handle_bribe
