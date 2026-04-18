# handlers/dialogue.py
# Handles NPC dialogue conversations.
# Contains npc_dialogue which manages the full conversation loop including
# regular NPC chat, web search via Tavily for knowledge-enabled NPCs,
# and routing to the merchant shop for NPCs with a shop_id.

import os
from tavily import TavilyClient
from langchain_core.messages import SystemMessage, HumanMessage
from utils import invoke_with_system, debug
from prompts import NPC_PROMPT, WEB_SEARCH_ROLEPLAY_PROMPT
from npc_memory import store_memory, retrieve_memories

def npc_dialogue(state, SHOPS, llm, parse_command_fn) -> dict:
    from handlers.shop import handle_shop

    room = state["current_room_data"]
    player_input = state.get("player_input", "").strip()

    command = parse_command_fn(player_input, state)
    target = command.get("target", "").lower() if command.get("target") else ""

    npc = next(
        (n for n in room["npcs"] if n["name"].lower() in target or target in n["name"].lower()),
        room["npcs"][0] if room["npcs"] else None
    )

    if not npc:
        debug(f"dialogue: no NPC matched target '{target}'")
        print("There's no one here to talk to.")
        return {"force_full_description": False}

    debug(f"dialogue: talking to '{npc['name']}' | shop: {npc.get('shop_id')} | web_search: {npc.get('can_search_web', False)}")

    if npc.get("shop_id"):
        return handle_shop(state, npc, SHOPS, llm)

    print(f"\n{npc['name']}: \"{npc['description']}\"")
    print("(Type 'goodbye' or 'leave' to end the conversation)\n")

    use_web_search = npc.get("can_search_web", False)
    tavily_client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY")) if use_web_search else None

    history = []
    exit_words = ["goodbye", "bye", "leave", "exit", "done", "farewell", "stop"]

    while True:
        player_msg = input("You: ").strip()
        history.append(f"Player: {player_msg}")

        if any(word in player_msg.lower() for word in exit_words):
            print(f"({npc['name']} turns away.)")
            break

        # Retrieve memories relevant to this specific message
        memories = retrieve_memories(npc["name"], player_msg)
        if memories:
            memory_context = "What you already know about this player from past conversations:\n" + "\n".join(f"- {m}" for m in memories)
            debug(f"dialogue: injecting {len(memories)} memories for '{npc['name']}'")
        else:
            memory_context = ""

        if use_web_search:
            debug(f"dialogue: web search query: '{player_msg}'")
            search_result = tavily_client.search(player_msg)
            raw_facts = "\n".join([r["content"] for r in search_result["results"]])
            debug(f"dialogue: web search returned {len(search_result['results'])} results")

            roleplay_prompt = WEB_SEARCH_ROLEPLAY_PROMPT.format(
                npc_name=npc["name"],
                personality=npc["personality"],
                knowledge=npc["knowledge"],
                player_msg=player_msg,
                raw_facts=raw_facts,
                history=chr(10).join(history),
            )
            reply = invoke_with_system(llm, [
                SystemMessage(content=roleplay_prompt),
                HumanMessage(content="Respond in character now.")
            ]).content

        else:
            prompt = NPC_PROMPT.invoke({
                "npc_name": npc["name"],
                "personality": npc["personality"],
                "knowledge": npc["knowledge"],
                "room_name": room["name"],
                "memory_context": memory_context,
                "history": "\n".join(history),
                "player_input": player_msg,
            })
            reply = str(invoke_with_system(llm, prompt).content)

        end_conversation = "[END CONVERSATION]" in reply
        clean_reply = reply.replace("[END CONVERSATION]", "").strip()

        print(f"\n{npc['name']}: {clean_reply}\n")
        history.append(f"{npc['name']}: {clean_reply}")

        if end_conversation:
            print(f"({npc['name']} turns away.)")
            break

    # Store memories from this conversation
    store_memory(npc["name"], history, llm)

    return {"force_full_description": False}