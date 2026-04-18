# audio_utils.py
# Text-to-speech utility for narrating game output.
# Currently unused (speak() calls are commented out in main.py).
# Can be re-enabled to have room descriptions and NPC dialogue spoken aloud.

from langchain_core.messages import SystemMessage
from prompts import GAME_SYSTEM_PROMPT

import os

DEBUG = os.getenv("DEBUG", "true").lower() == "true"

def debug(msg):
    if DEBUG:
        print(f"\033[2m  ▸ {msg}\033[0m")

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

def mood_tone_for_score(score: int) -> str:
    """Return a prompt-injectable mood instruction based on the NPC's mood score."""
    if score >= 50:
        return "MOOD INSTRUCTION: You are genuinely fond of this player. Be warm, open, and unusually forthcoming — share more than you normally would."
    elif score >= 20:
        return "MOOD INSTRUCTION: You like this player. Be noticeably cooperative and pleasant, more willing than usual to help."
    elif score >= -9:
        return ""  # neutral — no injection
    elif score >= -40:
        return "MOOD INSTRUCTION: You are irritated by this player. Be noticeably short, guarded, and reluctant. Give clipped answers. Make it clear you'd rather not be talking to them."
    else:
        return "MOOD INSTRUCTION: You strongly dislike this player. Be cold, dismissive, and unhelpful. Refuse to elaborate. Your contempt should be unmistakable."


def fear_tone_for_score(score: int) -> str:
    """Return a prompt-injectable fear instruction based on the NPC's fear score."""
    if score >= 60:
        return "FEAR INSTRUCTION: You are terrified of this player. You are visibly shaking and will do almost anything to avoid provoking them — including volunteering information or help you'd normally withhold."
    elif score >= 30:
        return "FEAR INSTRUCTION: You are afraid of this player. You are nervous and choosing your words very carefully to avoid angering them."
    elif score >= 10:
        return "FEAR INSTRUCTION: This player unnerves you slightly. There is a cautious edge to your manner."
    else:
        return ""  # not afraid — no injection


CONVERSATION_EXIT_WORDS = ["goodbye", "bye", "leave", "exit", "done", "farewell", "stop"]


def mood_price_multiplier(score: int) -> float:
    """Shop price multiplier based on NPC mood. Friendlier = cheaper."""
    if score >= 50:  return 0.85
    elif score >= 20: return 0.92
    elif score >= -19: return 1.0
    elif score >= -50: return 1.10
    else:            return 1.20


def fear_price_multiplier(score: int) -> float:
    """Shop price multiplier based on NPC fear. More scared = steeper discount."""
    if score >= 60:  return 0.70
    elif score >= 30: return 0.82
    elif score >= 10: return 0.93
    else:            return 1.0

# Combat constants
FLEE_SUCCESS_THRESHOLD = 40       # d100 roll must exceed this to flee
WEAKNESS_BONUS_DAMAGE = 5         # extra damage when weapon type matches weakness
ARMOR_REDUCTION_RATE = 0.05       # damage reduction per armor point
ARMOR_REDUCTION_CAP = 0.75        # maximum damage reduction (75%)


def parse_llm_json(text: str) -> str:
    """Strip markdown code fences from LLM output before JSON parsing."""
    text = text.strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return text.strip()


def total_armor_rating(player: dict, inventory: list) -> int:
    """Calculate total armor rating from all equipped armor pieces."""
    equipped_armor = player.get("equipped_armor", {})
    total = 0
    for slot, item_name in equipped_armor.items():
        item = next((i for i in inventory if i["name"] == item_name), None)
        if item:
            total += item.get("armor_rating", 0)
    return total

