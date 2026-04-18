# audio_utils.py
# Text-to-speech utility for narrating game output.
# Currently unused (speak() calls are commented out in main.py).
# Can be re-enabled to have room descriptions and NPC dialogue spoken aloud.

from langchain_core.messages import SystemMessage
from prompts import GAME_SYSTEM_PROMPT

DEBUG = True

def debug(msg):
    if DEBUG:
        print(f"[DEBUG] {msg}")

def visible_items(room):
    return [i for i in room["items"] if not i["hidden"]]

def find_item(room, name, include_hidden=False):
    items = room["items"] if include_hidden else visible_items(room)
    return next((i for i in items if i["name"] == name), None)

def invoke_with_system(llm, prompt):
    if hasattr(prompt, 'to_messages'):
        messages = prompt.to_messages()
    elif isinstance(prompt, list):
        messages = prompt
    else:
        messages = [prompt]

    # Don't prepend if already has a system message
    if messages and isinstance(messages[0], SystemMessage):
        return llm.invoke(messages)

    return llm.invoke([SystemMessage(content=GAME_SYSTEM_PROMPT)] + messages)

def total_armor_rating(player: dict, inventory: list) -> int:
    """Calculate total armor rating from all equipped armor pieces."""
    equipped_armor = player.get("equipped_armor", {})
    total = 0
    for slot, item_name in equipped_armor.items():
        item = next((i for i in inventory if i["name"] == item_name), None)
        if item:
            total += item.get("armor_rating", 0)
    return total

