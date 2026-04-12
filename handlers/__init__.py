# handlers/__init__.py
# Exports all action handler functions for use in resolve_action.
# Imports from movement.py, items.py, player.py, and shop.py.

from handlers.movement import handle_go
from handlers.items import handle_take, handle_examine, handle_open, handle_equip, handle_unequip
from handlers.player import handle_inventory, handle_room
from handlers.shop import handle_shop
