# handlers/shop.py
# Handles the merchant shop system using LangChain tools.
# Contains make_shop_tools which creates tool functions (get_player_gold,
# get_player_inventory, get_shop_stock, buy_item, sell_item) with current
# game state baked in, and handle_shop which runs the merchant conversation
# using an agentic tool-calling loop so the LLM can process transactions
# in character as Aldous the Peddler.

import json
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from prompts import GAME_SYSTEM_PROMPT, SHOP_SYSTEM_PROMPT
from utils import debug
from npc_memory import store_exchange, retrieve_memories

def _mood_price_multiplier(mood_score: int) -> float:
    """Return a price multiplier based on Aldous's mood. Friendlier = cheaper."""
    if mood_score >= 50:
        return 0.85
    elif mood_score >= 20:
        return 0.92
    elif mood_score >= -19:
        return 1.0
    elif mood_score >= -50:
        return 1.10
    else:
        return 1.20


def make_shop_tools(player: dict, shop_data: dict, shops: dict, mood_score: int = 0, fear_score: int = 0):
    """Create shop tools with current game state baked in."""

    stock = shop_data["stock"]
    sell_multiplier = shop_data.get("sell_multiplier", 0.5)
    def _fear_price_multiplier(fear: int) -> float:
        if fear >= 60:
            return 0.70  # terrified — steep discount
        elif fear >= 30:
            return 0.82  # afraid — noticeable discount
        elif fear >= 10:
            return 0.93  # unnerved — slight discount
        return 1.0

    # Use whichever gives the lower price (best deal for the player)
    price_multiplier = min(_mood_price_multiplier(mood_score), _fear_price_multiplier(fear_score))

    @tool
    def get_player_gold() -> str:
        """Check how much gold the player currently has."""
        return f"The player has {player.get('gold', 0)} gold coins."

    @tool
    def get_player_inventory() -> str:
        """Check what items the player is currently carrying."""
        inventory = player.get("inventory", [])
        if not inventory:
            return "The player's inventory is empty."
        items = ", ".join(f"{i['name']}" for i in inventory)
        return f"Player is carrying: {items}"

    @tool
    def get_shop_stock() -> str:
        """Get the current stock available in the shop."""
        available = [i for i in stock if i.get("quantity", 1) > 0]
        if not available:
            return "The shop is out of stock."
        lines = []
        for item in available:
            adjusted_price = max(1, int(item["price"] * price_multiplier))
            if item.get("weapon_type"):
                lines.append(f"{item['name']} — {item['weapon_type']}, {item['damage']} damage — {adjusted_price} gold")
            else:
                lines.append(f"{item['name']} — {adjusted_price} gold")
        lines.append("\nTo buy an item, say 'buy [item name]'")
        lines.append("To sell an item, say 'sell [item name]'")
        lines.append("To check your gold, say 'how much gold do I have'")
        return "Available items:\n" + "\n".join(lines)


    @tool
    def buy_item(item_name: str) -> str:
        """Buy an item from the shop. Deducts gold and adds item to player inventory."""
        shop_item = next((i for i in stock if i["name"].lower() == item_name.lower()), None)

        if not shop_item:
            return f"'{item_name}' is not available in the shop."

        price = max(1, int(shop_item["price"] * price_multiplier))
        if player.get("gold", 0) < price:
            return f"Not enough gold. {item_name} costs {price} gold but you only have {player.get('gold', 0)}."

        # Deduct gold
        player["gold"] = player.get("gold", 0) - price

        # Add to inventory — full ItemData dict without price field
        item_data = {k: v for k, v in shop_item.items() if k != "price"}
        player.setdefault("inventory", []).append(item_data)

        return f"Purchased {item_name} for {price} gold. Remaining gold: {player['gold']}."

    @tool
    def sell_item(item_name: str) -> str:
        """Sell an item from player inventory to the shop."""
        inventory = player.get("inventory", [])
        item = next((i for i in inventory if i["name"].lower() == item_name.lower()), None)

        if not item:
            return f"You don't have '{item_name}' in your inventory."

        # Calculate sell price
        buy_price = next(
            (s["price"] for s in stock if s["name"].lower() == item_name.lower()),
            10  # default value for items not in shop stock
        )
        sell_price = max(1, int(buy_price * sell_multiplier))

        # Remove from inventory
        player["inventory"] = [i for i in inventory if i["name"].lower() != item_name.lower()]
        player["gold"] = player.get("gold", 0) + sell_price

        return f"Sold {item_name} for {sell_price} gold. Total gold: {player['gold']}."

    return [get_player_gold, get_player_inventory, get_shop_stock, buy_item, sell_item]


def handle_shop(state: dict, npc: dict, shops: dict, llm, npc_moods: dict = None, npc_fear: dict = None) -> dict:
    """Run the merchant shop conversation with LangChain tools."""
    
    shop_id = npc.get("shop_id", "aldous")
    shop_data = shops.get(shop_id, {})

    if not shop_data:
        print(f"{npc['name']}: I'm afraid I have nothing to sell right now.")
        return {"force_full_description": False}

    # Make mutable copy of player state
    player = dict(state.get("player", {}))
    player["inventory"] = list(player.get("inventory", []))

    mood_score = (npc_moods or {}).get(npc["name"], 0)
    fear_score = (npc_fear or {}).get(npc["name"], 0)
    debug(f"shop: mood for '{npc['name']}': {mood_score} | fear: {fear_score}")

    tools = make_shop_tools(player, shop_data, shops, mood_score, fear_score)
    shop_llm = llm.bind_tools(tools)

    memories = retrieve_memories(npc["name"], "player background and past interactions")
    memory_context = ""
    if memories:
        memory_context = "\nWhat you already know about this player:\n" + "\n".join(f"- {m}" for m in memories)
        debug(f"shop: injecting {len(memories)} memories for '{npc['name']}'")

    system_prompt = SHOP_SYSTEM_PROMPT.format(
        npc_name=npc["name"],
        personality=npc["personality"],
    ) + memory_context

    print(f"\n{npc['name']}: \"{npc['description']}\"")
    print("(Type 'goodbye' to leave the shop)\n")

    history = [SystemMessage(content=system_prompt)]
    history.append(HumanMessage(content="Hello, show me what you have for sale."))

    # Run one LLM turn before player input to get opening stock display
    while True:
        response = shop_llm.invoke(history)
        history.append(response)
        if not response.tool_calls:
            break
        from langchain_core.messages import ToolMessage
        for tool_call in response.tool_calls:
            tool_fn = next((t for t in tools if t.name == tool_call["name"]), None)
            if tool_fn:
                debug(f"shop tool: {tool_call['name']}({tool_call['args']})")
                result = tool_fn.invoke(tool_call["args"])
                debug(f"shop tool result: {result}")
                history.append(ToolMessage(
                    content=str(result),
                    tool_call_id=tool_call["id"]
                ))

    print(f"\n{npc['name']}: {response.content}\n")

    exit_words = ["goodbye", "bye", "leave", "exit", "done", "farewell", "stop"]

    while True:
        player_msg = input("You: ").strip()

        if any(word in player_msg.lower() for word in exit_words):
            print(f"\n{npc['name']}: Safe travels, and remember — Aldous always has the best prices!")
            break

        history.append(HumanMessage(content=player_msg))

        # Agentic loop — keep calling until no more tool calls
        while True:
            response = shop_llm.invoke(history)
            history.append(response)

            if not response.tool_calls:
                break

            from langchain_core.messages import ToolMessage
            for tool_call in response.tool_calls:
                tool_fn = next((t for t in tools if t.name == tool_call["name"]), None)
                if tool_fn:
                    debug(f"shop tool: {tool_call['name']}({tool_call['args']})")
                    result = tool_fn.invoke(tool_call["args"])
                    debug(f"shop tool result: {result}")
                    history.append(ToolMessage(
                        content=str(result),
                        tool_call_id=tool_call["id"]
                    ))

        reply = response.content
        end_conversation = "[END CONVERSATION]" in reply
        clean_reply = reply.replace("[END CONVERSATION]", "").strip()

        print(f"\n{npc['name']}: {clean_reply}\n")
        store_exchange(npc["name"], player_msg, clean_reply)

        if end_conversation:
            break

    return {
        "player": player,
        "npc_moods": npc_moods or {},
        "npc_fear": npc_fear or {},
        "force_full_description": False
    }