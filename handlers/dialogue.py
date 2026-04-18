# handlers/dialogue.py
# Handles NPC dialogue conversations.
# Contains npc_dialogue which manages the full conversation loop including
# regular NPC chat, web search via Tavily for knowledge-enabled NPCs,
# and routing to the merchant shop for NPCs with a shop_id.

import os
from tavily import TavilyClient
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI
from utils import invoke_with_system, debug, mood_tone_for_score, fear_tone_for_score
from prompts import NPC_PROMPT, WEB_SEARCH_ROLEPLAY_PROMPT, WEB_SEARCH_REQUIRED_PROMPT, WEB_SEARCH_REFUSED_PROMPT
from npc_memory import store_exchange, retrieve_memories, evaluate_mood_delta, evaluate_fear_delta

_router_llm = None

def _requires_web_search(player_msg: str, llm) -> bool:
    global _router_llm
    if _router_llm is None:
        _router_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    from langchain_core.messages import HumanMessage
    response = _router_llm.invoke([
        HumanMessage(content=WEB_SEARCH_REQUIRED_PROMPT.format(player_msg=player_msg))
    ])
    return response.content.strip().upper().startswith("YES")

def npc_dialogue(state, SHOPS, llm, mini_llm, parse_command_fn) -> dict:
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
        npc_moods = dict(state.get("npc_moods", {}))
        npc_fear = dict(state.get("npc_fear", {}))
        return handle_shop(state, npc, SHOPS, mini_llm, npc_moods, npc_fear)

    print(f"\n{npc['name']}: \"{npc['description']}\"")
    print("(Type 'goodbye' or 'leave' to end the conversation)\n")

    use_web_search = npc.get("can_search_web", False)
    tavily_client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY")) if use_web_search else None

    history = []
    exit_words = ["goodbye", "bye", "leave", "exit", "done", "farewell", "stop"]

    npc_moods = dict(state.get("npc_moods", {}))
    npc_fear = dict(state.get("npc_fear", {}))
    current_mood = npc_moods.get(npc["name"], 0)
    current_fear = npc_fear.get(npc["name"], 0)
    debug(f"dialogue: mood for '{npc['name']}': {current_mood} | fear: {current_fear}")

    while True:
        player_msg = input("You: ").strip()
        history.append(f"Player: {player_msg}")

        if any(word in player_msg.lower() for word in exit_words):
            print(f"({npc['name']} turns away.)")
            break

        # Evaluate attitude and update mood + fear in parallel
        mood_delta = evaluate_mood_delta(player_msg)
        fear_delta = evaluate_fear_delta(player_msg)
        current_mood = max(-100, min(100, current_mood + mood_delta))
        current_fear = max(0, min(100, current_fear + fear_delta))
        npc_moods[npc["name"]] = current_mood
        npc_fear[npc["name"]] = current_fear
        debug(f"dialogue: mood delta for '{npc['name']}': {mood_delta:+d} → total: {current_mood}")
        debug(f"dialogue: fear delta for '{npc['name']}': {fear_delta:+d} → total: {current_fear}")

        # Retrieve memories relevant to this specific message
        memories = retrieve_memories(npc["name"], player_msg)
        if memories:
            memory_context = "What you already know about this player from past conversations:\n" + "\n".join(f"- {m}" for m in memories)
            debug(f"dialogue: injecting {len(memories)} memories for '{npc['name']}'")
        else:
            memory_context = ""

        mood_tone = mood_tone_for_score(current_mood)
        fear_tone = fear_tone_for_score(current_fear)

        if use_web_search:
            if current_mood <= -30:
                debug(f"dialogue: web search blocked — mood too low ({current_mood})")
                refusal_prompt = WEB_SEARCH_REFUSED_PROMPT.format(
                    npc_name=npc["name"],
                    personality=npc["personality"],
                    player_msg=player_msg,
                )
                reply = invoke_with_system(llm, [
                    SystemMessage(content=refusal_prompt),
                    HumanMessage(content="Refuse in character now.")
                ]).content
                end_conversation = "[END CONVERSATION]" in reply
                clean_reply = reply.replace("[END CONVERSATION]", "").strip()
                print(f"\n{npc['name']}: {clean_reply}\n")
                history.append(f"{npc['name']}: {clean_reply}")
                store_exchange(npc["name"], player_msg, clean_reply)
                if end_conversation:
                    print(f"({npc['name']} turns away.)")
                    break
                continue

            needs_search = _requires_web_search(player_msg, llm)
            debug(f"dialogue: web search required: {needs_search}")

            if needs_search:
                debug(f"dialogue: web search query: '{player_msg}'")
                search_result = tavily_client.search(player_msg)
                raw_facts = "\n".join([r["content"] for r in search_result["results"]])
                debug(f"dialogue: web search returned {len(search_result['results'])} results")

                roleplay_prompt = WEB_SEARCH_ROLEPLAY_PROMPT.format(
                    npc_name=npc["name"],
                    personality=npc["personality"],
                    knowledge=npc["knowledge"],
                    memory_context=memory_context,
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
                    "mood_tone": mood_tone,
                    "fear_tone": fear_tone,
                    "history": "\n".join(history),
                    "player_input": player_msg,
                })
                reply = str(invoke_with_system(llm, prompt).content)

        else:
            prompt = NPC_PROMPT.invoke({
                "npc_name": npc["name"],
                "personality": npc["personality"],
                "knowledge": npc["knowledge"],
                "room_name": room["name"],
                "memory_context": memory_context,
                "mood_tone": mood_tone,
                "fear_tone": fear_tone,
                "history": "\n".join(history),
                "player_input": player_msg,
            })
            reply = str(invoke_with_system(llm, prompt).content)

        end_conversation = "[END CONVERSATION]" in reply
        clean_reply = reply.replace("[END CONVERSATION]", "").strip()

        print(f"\n{npc['name']}: {clean_reply}\n")
        history.append(f"{npc['name']}: {clean_reply}")

        # Store facts immediately so they're available for the rest of this conversation
        store_exchange(npc["name"], player_msg, clean_reply)

        if end_conversation:
            print(f"({npc['name']} turns away.)")
            break

    return {"npc_moods": npc_moods, "npc_fear": npc_fear, "force_full_description": False}