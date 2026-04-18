# handlers/dialogue.py
# Handles NPC dialogue conversations.
# Contains npc_dialogue which manages the full conversation loop including
# regular NPC chat, web search via Tavily for knowledge-enabled NPCs,
# and routing to the merchant shop for NPCs with a shop_id.

import os
import re
from tavily import TavilyClient
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI
from utils import invoke_with_system, debug, mood_tone_for_score, fear_tone_for_score, CONVERSATION_EXIT_WORDS
from prompts import GAME_SYSTEM_PROMPT, NPC_PROMPT, WEB_SEARCH_ROLEPLAY_PROMPT, WEB_SEARCH_REQUIRED_PROMPT, WEB_SEARCH_REFUSED_PROMPT, NPC_BRIBE_PROMPT, NPC_BRIBE_BOOST_PROMPT
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

def _invoke_npc(llm, npc, room, memory_context, history, player_msg, mood_tone, fear_tone):
    """Invoke NPC response with mood/fear injected into the system message."""
    system = GAME_SYSTEM_PROMPT
    overrides = []
    if mood_tone:
        overrides.append(mood_tone)
    if fear_tone:
        overrides.append(fear_tone)
    if overrides:
        system += "\n\nBEHAVIORAL OVERRIDES — these supersede personality and must be reflected in every sentence:\n" + "\n".join(overrides)

    debug(f"npc system tail: ...{system[-200:]!r}")

    prompt = NPC_PROMPT.invoke({
        "npc_name": npc["name"],
        "personality": npc["personality"],
        "knowledge": npc["knowledge"],
        "room_name": room["name"],
        "memory_context": memory_context,
        "history": "\n".join(history),
        "player_input": player_msg,
    })
    messages = prompt.to_messages()
    return llm.invoke([SystemMessage(content=system)] + messages)


def handle_bribe(state: dict, target: str, amount: int, llm, mini_llm) -> dict:
    room = state["current_room_data"]
    player = dict(state.get("player", {}))
    npc_moods = dict(state.get("npc_moods", {}))
    npc_fear = dict(state.get("npc_fear", {}))

    npc = next(
        (n for n in room["npcs"] if n["name"].lower() in target.lower() or target.lower() in n["name"].lower()),
        None
    )
    if not npc:
        print("There's no one here to give gold to.")
        return {"force_full_description": False}

    gold = player.get("gold", 0)
    if gold < amount:
        print(f"You only have {gold} gold.")
        return {"force_full_description": False}

    player["gold"] = gold - amount
    current_mood = npc_moods.get(npc["name"], 0)
    current_fear = npc_fear.get(npc["name"], 0)

    boost_response = mini_llm.invoke([
        HumanMessage(content=NPC_BRIBE_BOOST_PROMPT.format(
            amount=amount,
            personality=npc["personality"],
            current_mood=current_mood,
        ))
    ])
    try:
        boost = int(boost_response.content.strip())
    except (ValueError, AttributeError):
        boost = 0

    new_mood = max(-100, min(100, current_mood + boost))
    npc_moods[npc["name"]] = new_mood
    debug(f"bribe: gave {amount} gold to '{npc['name']}' | boost: {boost:+d} | mood {current_mood} → {new_mood}")

    prompt = NPC_BRIBE_PROMPT.format(
        npc_name=npc["name"],
        personality=npc["personality"],
        amount=amount,
        mood_tone=mood_tone_for_score(new_mood),
        fear_tone=fear_tone_for_score(current_fear),
    )
    reply = invoke_with_system(llm, [
        SystemMessage(content=prompt),
        HumanMessage(content="React to receiving the gold.")
    ]).content.strip()

    print(f"\n{npc['name']}: {reply}\n")
    return {
        "player": player,
        "npc_moods": npc_moods,
        "npc_fear": npc_fear,
        "force_full_description": False,
    }


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

    npc_moods = dict(state.get("npc_moods", {}))
    npc_fear = dict(state.get("npc_fear", {}))
    current_mood = npc_moods.get(npc["name"], 0)
    current_fear = npc_fear.get(npc["name"], 0)
    player = dict(state.get("player", {}))
    debug(f"dialogue: mood for '{npc['name']}': {current_mood} | fear: {current_fear}")

    while True:
        player_msg = input("You: ").strip()
        history.append(f"Player: {player_msg}")

        if any(word in player_msg.lower() for word in CONVERSATION_EXIT_WORDS):
            print(f"({npc['name']} turns away.)")
            break

        # Detect bribe inside conversation
        bribe_match = re.search(r'\b(?:give|offer|bribe|pay)\b.*?(\d+)\s*gold', player_msg.lower())
        if bribe_match:
            amount = int(bribe_match.group(1))
            gold = player.get("gold", 0)
            if gold < amount:
                print(f"You only have {gold} gold.")
                history.pop()
                continue
            player["gold"] = gold - amount
            boost_response = mini_llm.invoke([
                HumanMessage(content=NPC_BRIBE_BOOST_PROMPT.format(
                    amount=amount,
                    personality=npc["personality"],
                    current_mood=current_mood,
                ))
            ])
            try:
                boost = int(boost_response.content.strip())
            except (ValueError, AttributeError):
                boost = 0
            current_mood = max(-100, min(100, current_mood + boost))
            npc_moods[npc["name"]] = current_mood
            debug(f"bribe (in dialogue): gave {amount} gold to '{npc['name']}' | boost: {boost:+d} | mood → {current_mood}")
            mood_tone = mood_tone_for_score(current_mood)
            fear_tone = fear_tone_for_score(current_fear)
            reaction_prompt = NPC_BRIBE_PROMPT.format(
                npc_name=npc["name"],
                personality=npc["personality"],
                amount=amount,
                mood_tone=mood_tone,
                fear_tone=fear_tone,
            )
            reply = llm.invoke([
                SystemMessage(content=GAME_SYSTEM_PROMPT),
                HumanMessage(content=reaction_prompt),
            ]).content.strip()
            print(f"\n{npc['name']}: {reply}\n")
            history.append(f"{npc['name']}: {reply}")
            store_exchange(npc["name"], player_msg, reply)
            continue

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
                reply = str(_invoke_npc(llm, npc, room, memory_context, history, player_msg, mood_tone, fear_tone).content)

        else:
            reply = str(_invoke_npc(llm, npc, room, memory_context, history, player_msg, mood_tone, fear_tone).content)

        end_conversation = "[END CONVERSATION]" in reply
        clean_reply = reply.replace("[END CONVERSATION]", "").strip()

        print(f"\n{npc['name']}: {clean_reply}\n")
        history.append(f"{npc['name']}: {clean_reply}")

        # Store facts immediately so they're available for the rest of this conversation
        store_exchange(npc["name"], player_msg, clean_reply)

        if end_conversation:
            print(f"({npc['name']} turns away.)")
            break

    return {"player": player, "npc_moods": npc_moods, "npc_fear": npc_fear, "force_full_description": False}