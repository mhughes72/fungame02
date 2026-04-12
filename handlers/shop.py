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

def make_shop_tools(player: dict, shop_data: dict, shops: dict):
    """Create shop tools with current game state baked in."""

    stock = shop_data["stock"]
    sell_multiplier = shop_data.get("sell_multiplier", 0.5)

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
            if item.get("weapon_type"):
                lines.append(f"{item['name']} — {item['weapon_type']}, {item['damage']} damage — {item['price']} gold")
            else:
                lines.append(f"{item['name']} — {item['price']} gold")
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

        price = shop_item["price"]
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


def handle_shop(state: dict, npc: dict, shops: dict, llm) -> dict:
    """Run the merchant shop conversation with LangChain tools."""
    
    shop_id = npc.get("shop_id", "aldous")
    shop_data = shops.get(shop_id, {})

    if not shop_data:
        print(f"{npc['name']}: I'm afraid I have nothing to sell right now.")
        return {"force_full_description": False}

    # Make mutable copy of player state
    player = dict(state.get("player", {}))
    player["inventory"] = list(player.get("inventory", []))

    tools = make_shop_tools(player, shop_data, shops)
    shop_llm = llm.bind_tools(tools)

    system_prompt = SHOP_SYSTEM_PROMPT.format(
        npc_name=npc["name"],
        personality=npc["personality"],
    )

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
                result = tool_fn.invoke(tool_call["args"])
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
                    result = tool_fn.invoke(tool_call["args"])
                    history.append(ToolMessage(
                        content=str(result),
                        tool_call_id=tool_call["id"]
                    ))

        reply = response.content
        end_conversation = "[END CONVERSATION]" in reply
        clean_reply = reply.replace("[END CONVERSATION]", "").strip()

        print(f"\n{npc['name']}: {clean_reply}\n")

        if end_conversation:
            break

    return {
        "player": player,
        "force_full_description": False
    }