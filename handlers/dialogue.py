# handlers/dialogue.py
# Handles NPC dialogue conversations.
# Contains npc_dialogue which manages the full conversation loop including
# regular NPC chat, web search via Tavily for knowledge-enabled NPCs,
# and routing to the merchant shop for NPCs with a shop_id.

import os
import re
from tavily import TavilyClient
from langchain_core.messages import AIMessage, SystemMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from utils import (invoke_with_system, debug, mood_tone_for_score, fear_tone_for_score,
                   CONVERSATION_EXIT_WORDS, MAX_CONVERSATION_TURNS, MAX_TOOL_CALL_ITERATIONS,
                   get_mutable_player, find_npc, make_slug,
                   emit_encounter_start, emit_encounter_end, emit_encounter_state,
                   emit_player_state, add_journal_entry)
from prompts import GAME_SYSTEM_PROMPT, NPC_PROMPT, ORACLE_SYSTEM_PROMPT, WEB_SEARCH_REFUSED_PROMPT, NPC_BRIBE_PROMPT, NPC_BRIBE_BOOST_PROMPT, NPC_GIFT_TRIGGER_PROMPT
from npc_memory import store_exchange, gossip_facts, retrieve_memories, evaluate_mood_delta, evaluate_fear_delta, evaluate_gossip_impact


def _make_oracle_tool(tavily_client) -> list:
    @tool
    def web_search(query: str) -> str:
        """Search the web for real-world facts, current events, people, or information."""
        debug(f"oracle tool: web_search('{query}')")
        results = tavily_client.search(query)
        debug(f"oracle tool: got {len(results['results'])} results")
        return "\n".join(r["content"] for r in results["results"])
    return [web_search]


def _run_oracle_loop(oracle_llm, oracle_tools, messages) -> AIMessage:
    """Invoke Oracle LLM and execute web_search tool calls until the model responds without tools."""
    iteration = 0
    while True:
        iteration += 1
        debug(f"oracle loop [{iteration}]: invoking LLM ({len(messages)} messages in context)")
        response = oracle_llm.invoke(messages)
        messages.append(response)
        if response.content:
            debug(f"oracle loop [{iteration}]: reasoning: {response.content}")
        if not response.tool_calls or iteration >= MAX_TOOL_CALL_ITERATIONS:
            if iteration >= MAX_TOOL_CALL_ITERATIONS:
                debug(f"oracle loop: hit {MAX_TOOL_CALL_ITERATIONS}-iteration cap — returning")
            else:
                debug(f"oracle loop [{iteration}]: no tool calls — final reply ({len(response.content)} chars)")
            return response
        debug(f"oracle loop [{iteration}]: {len(response.tool_calls)} tool call(s) requested")
        for tc in response.tool_calls:
            debug(f"oracle loop [{iteration}]:   → {tc['name']}({tc['args']})")
            tool_fn = next((t for t in oracle_tools if t.name == tc["name"]), None)
            if tool_fn:
                result = tool_fn.invoke(tc["args"])
                debug(f"oracle loop [{iteration}]:   ← {len(str(result))} chars returned")
                messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))

def _invoke_npc(llm, npc, room, memory_context, history, player_msg, mood_tone, fear_tone, io, knowledge=None) -> str:
    """Stream NPC response through io with mood/fear injected into the system message. Returns full text."""
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
        "knowledge": knowledge if knowledge is not None else npc["knowledge"],
        "room_name": room["name"],
        "memory_context": memory_context,
        "history": "\n".join(history),
        "player_input": player_msg,
    })
    messages = prompt.to_messages()
    return io.stream(llm.stream([SystemMessage(content=system)] + messages))


def _execute_bribe(npc: dict, amount: int, player: dict, npc_moods: dict,
                   current_fear: int, llm, mini_llm) -> tuple[str, int]:
    """Deduct gold, evaluate boost, update mood, generate NPC reaction.

    Returns (reply_text, new_mood). Caller is responsible for updating state.
    """
    current_mood = npc_moods.get(npc["name"], 0)

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

    reaction_prompt = NPC_BRIBE_PROMPT.format(
        npc_name=npc["name"],
        personality=npc["personality"],
        amount=amount,
        mood_tone=mood_tone_for_score(new_mood),
        fear_tone=fear_tone_for_score(current_fear),
    )
    reply = llm.invoke([
        SystemMessage(content=GAME_SYSTEM_PROMPT),
        HumanMessage(content=reaction_prompt),
    ]).content.strip()

    return reply, new_mood


def handle_bribe(state: dict, target: str, amount: int, llm, mini_llm, io) -> dict:
    room = state["current_room_data"]
    player, _ = get_mutable_player(state)
    npc_moods = dict(state.get("npc_moods", {}))
    npc_fear = dict(state.get("npc_fear", {}))

    npc = find_npc(room, target.lower())
    if not npc:
        io.send("There's no one here to give gold to.")
        return {"force_full_description": False}

    gold = player.get("gold", 0)
    if gold < amount:
        io.send(f"You only have {gold} gold.")
        return {"force_full_description": False}

    player["gold"] = gold - amount
    reply, _ = _execute_bribe(npc, amount, player, npc_moods, npc_fear.get(npc["name"], 0), llm, mini_llm)
    io.send(f"\n{npc['name']}: {reply}\n")
    return {"player": player, "npc_moods": npc_moods, "npc_fear": npc_fear, "force_full_description": False}


def _handle_bribe_in_loop(player_msg, npc, player, npc_moods, current_fear, current_mood, llm, mini_llm, history, io) -> tuple[bool, int]:
    """Detect and process a bribe offer within the conversation loop. Returns (was_bribe, new_mood)."""
    bribe_match = re.search(r'\b(?:give|offer|bribe|pay)\b.*?(\d+)\s*gold', player_msg.lower())
    if not bribe_match:
        return False, current_mood

    amount = int(bribe_match.group(1))
    if player.get("gold", 0) < amount:
        io.send(f"You only have {player.get('gold', 0)} gold.")
        history.pop()
        return True, current_mood

    player["gold"] = player.get("gold", 0) - amount
    reply, new_mood = _execute_bribe(npc, amount, player, npc_moods, current_fear, llm, mini_llm)
    io.send(f"\n{npc['name']}: {reply}\n")
    history.append(f"{npc['name']}: {reply}")
    store_exchange(npc["name"], player_msg, reply)
    emit_encounter_state(io, npc_mood=new_mood, npc_fear=current_fear)
    return True, new_mood


def _get_npc_reply(player_msg, npc, room, memory_context, history, mood_tone, fear_tone,
                   current_mood, use_web_search, tavily_client, llm, io) -> tuple[str, bool]:
    """Get NPC reply, handling web-search refusal/tool loop or standard NPC response. Returns (clean_reply, end_conversation)."""
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
            io.send(reply)
            return reply.replace("[END CONVERSATION]", "").strip(), "[END CONVERSATION]" in reply

        overrides = [t for t in [mood_tone, fear_tone] if t]
        mood_overrides = "\nBEHAVIORAL OVERRIDES — reflected in every sentence:\n" + "\n".join(overrides) if overrides else ""
        system = ORACLE_SYSTEM_PROMPT.format(
            npc_name=npc["name"],
            personality=npc["personality"],
            knowledge=npc["knowledge"],
            memory_context=memory_context,
            history="\n".join(history[:-1]),
            mood_overrides=mood_overrides,
        )
        oracle_tools = _make_oracle_tool(tavily_client)
        oracle_llm = llm.bind_tools(oracle_tools)
        messages = [SystemMessage(content=system), HumanMessage(content=player_msg)]
        response = _run_oracle_loop(oracle_llm, oracle_tools, messages)
        reply = response.content or ""
        io.send(reply)
    else:
        reply = _invoke_npc(llm, npc, room, memory_context, history, player_msg, mood_tone, fear_tone, io)

    return reply.replace("[END CONVERSATION]", "").strip(), "[END CONVERSATION]" in reply


def _build_knowledge(npc: dict, npc_catalogue: dict, monster_catalogue: dict, item_catalogue: dict) -> str:
    """Append known entity descriptions (and any secrets) to the NPC's own knowledge string.

    knows_about keys are namespaced: "npc:<id>", "monster:<id>", "item:<id>".
    Values are supplemental secret knowledge (empty string if none).
    """
    base = npc.get("knowledge", "")
    knows_about = npc.get("knows_about", {})
    if not knows_about:
        return base

    entries = []
    for key, secret in knows_about.items():
        if ":" in key:
            kind, entity_id = key.split(":", 1)
        else:
            kind, entity_id = "npc", key  # backwards-compat with un-namespaced keys

        if kind == "npc":
            other = npc_catalogue.get(entity_id)
            if other:
                entry = f"{other['name']} (person): {other['description']}. {other['personality']}"
        elif kind == "monster":
            other = monster_catalogue.get(entity_id)
            if other:
                entry = f"{other['name']} (creature): {other['description']}"
        elif kind == "item":
            other = item_catalogue.get(entity_id)
            if other:
                entry = f"{other['name']} (object): {other['description']}"
        else:
            other = None

        if other:
            if secret:
                entry += f" (Your private knowledge: {secret})"
            entries.append(entry)

    if not entries:
        return base
    return base + "\n\nThings you know about:\n" + "\n".join(f"- {e}" for e in entries)


def _check_gift_trigger(npc: dict, player_msg: str, npc_gifts_given: list,
                        player: dict, state: dict, mini_llm, io) -> bool:
    """Evaluate if player said the right thing to unlock the NPC's gift (item, secret, or both).

    Returns True if triggered. Mutates player inventory and npc_gifts_given in place.
    """
    gift = npc.get("gift")
    if not gift or npc["name"] in npc_gifts_given:
        return False

    response = mini_llm.invoke([HumanMessage(content=NPC_GIFT_TRIGGER_PROMPT.format(
        trigger_description=gift["trigger_description"],
        player_message=player_msg,
    ))])
    if "YES" not in response.content.strip().upper():
        return False

    npc_gifts_given.append(npc["name"])

    parts = []
    if gift.get("item_name"):
        item_entry = {
            "name": gift["item_name"], "openable": False, "is_open": False,
            "gold": 0, "damage": 0, "weapon_type": None, "armor_slot": None, "armor_rating": 0,
        }
        player.setdefault("inventory", []).append(item_entry)
        parts.append(f"received the {gift['item_name']} from {npc['name']}")
        debug(f"gift: '{npc['name']}' gave item '{gift['item_name']}' to player")

    if gift.get("secret"):
        io.send(f"\n[You learn: {gift['secret']}]\n")
        parts.append(f"learned a secret from {npc['name']}: {gift['secret']}")
        debug(f"gift: '{npc['name']}' revealed secret to player")

    emit_player_state(player, state["current_room_id"], io, room_data=state.get("current_room_data"))
    add_journal_entry(", and ".join(parts), player, state["current_room_id"], io, mini_llm)
    return True


def _handle_give_in_loop(player_msg: str, npc: dict, player: dict, npc_trades_done: list,
                         state: dict, io, mini_llm) -> tuple[bool, str | None]:
    """Detect 'give <item>' in player message and process a matching NPC trade.

    Returns (was_give, history_note).
    - was_give=False: no give intent detected; caller handles normally.
    - was_give=True, note=str: trade completed or rejected; note injected into history for NPC reply.
    - was_give=True, note=None: player doesn't own the named item; error already sent.
    """
    if not re.search(r'\b(?:give|hand|offer|trade)\b', player_msg.lower()):
        return False, None

    inventory = player.get("inventory", [])
    msg_lower = player_msg.lower()

    # Find which inventory item the player is trying to give
    offered = next((it for it in inventory if it["name"].lower() in msg_lower), None)
    if not offered:
        return False, None  # give keyword but no inventory item named — let LLM handle naturally

    trades = npc.get("trades", [])
    for trade in trades:
        trade_key = f"{npc['name']}:{trade['required_item']}"
        if trade_key in npc_trades_done:
            continue
        if offered["name"].lower() != trade["required_item"].lower():
            continue

        inventory.remove(offered)
        npc_trades_done.append(trade_key)

        parts = []
        if trade.get("item_name"):
            item_entry = {
                "name": trade["item_name"], "openable": False, "is_open": False,
                "gold": 0, "damage": 0, "weapon_type": None, "armor_slot": None, "armor_rating": 0,
            }
            inventory.append(item_entry)
            parts.append(f"accepted the {trade['required_item']} and gave the player your {trade['item_name']}")

        if trade.get("gold"):
            player["gold"] = player.get("gold", 0) + trade["gold"]
            parts.append(f"paid the player {trade['gold']} gold for the {trade['required_item']}")

        if trade.get("secret"):
            io.send(f"\n[You learn: {trade['secret']}]\n")
            parts.append(f"revealed the secret: {trade['secret']}")

        emit_player_state(player, state["current_room_id"], io, room_data=state.get("current_room_data"))
        debug(f"trade: '{npc['name']}' traded for '{trade['required_item']}'")
        event = f"Player gave {npc['name']} the {trade['required_item']} and {', and '.join(parts)}"
        add_journal_entry(event, player, state["current_room_id"], io, mini_llm)
        return True, f"[You just {' and '.join(parts)}. Acknowledge this naturally in your next reply.]"

    # Player offered an item but NPC has no use for it — reject in character
    debug(f"trade: '{npc['name']}' has no use for '{offered['name']}'")
    return True, f"[The player just tried to give you their {offered['name']}. Refuse it in character — you have no use for it.]"


def _init_conversation(state: dict, npc: dict, room: dict, io) -> tuple:
    """Emit encounter start, apply gossip impact, return initialized conversation state variables."""
    npc_slug = make_slug(npc["name"])
    npc_moods = dict(state.get("npc_moods", {}))
    npc_fear  = dict(state.get("npc_fear", {}))
    emit_encounter_start(io, encounter_type="dialogue", target_name=npc["name"],
                         target_slug=npc_slug, npc_mood=npc_moods.get(npc["name"], 0),
                         npc_fear=npc_fear.get(npc["name"], 0))

    io.send(f"\n{npc['name']}: \"{npc['description']}\"")
    io.send("(Type 'goodbye' or 'leave' to end the conversation)\n")

    current_mood = npc_moods.get(npc["name"], 0)
    current_fear = npc_fear.get(npc["name"], 0)

    gossip_mood, gossip_fear = evaluate_gossip_impact(npc["name"])
    if gossip_mood or gossip_fear:
        current_mood = max(-100, min(100, current_mood + gossip_mood))
        current_fear = max(0,    min(100, current_fear + gossip_fear))
        npc_moods[npc["name"]] = current_mood
        npc_fear[npc["name"]]  = current_fear
        debug(f"dialogue: gossip adjusted '{npc['name']}' mood → {current_mood}, fear → {current_fear}")
        emit_encounter_state(io, npc_mood=current_mood, npc_fear=current_fear)

    player, _ = get_mutable_player(state)
    debug(f"dialogue: mood for '{npc['name']}': {current_mood} | fear: {current_fear}")
    return (player, npc_moods, npc_fear,
            list(state.get("npc_gifts_given", [])),
            list(state.get("npc_trades_done", [])),
            current_mood, current_fear)


def _run_conversation_loop(npc, room, player, npc_moods, npc_fear, npc_gifts_given, npc_trades_done,
                           current_mood: int, current_fear: int, use_web_search, tavily_client,
                           history, state, llm, mini_llm, io) -> None:
    """Run the main conversation turn loop until the player exits or the NPC ends it.

    Mutates npc_moods, npc_fear, npc_gifts_given, npc_trades_done, and player in place.
    """
    turn = 0
    while True:
        turn += 1
        if turn > MAX_CONVERSATION_TURNS:
            debug(f"dialogue: hit {MAX_CONVERSATION_TURNS}-turn cap for '{npc['name']}'")
            io.send(f"({npc['name']} looks exhausted and turns away.)")
            emit_encounter_end(io)
            break

        player_msg = io.recv("You: ")
        history.append(f"Player: {player_msg}")

        if any(word in player_msg.lower() for word in CONVERSATION_EXIT_WORDS):
            io.send(f"({npc['name']} turns away.)")
            emit_encounter_end(io)
            break

        was_bribe, current_mood = _handle_bribe_in_loop(
            player_msg, npc, player, npc_moods, current_fear, current_mood, llm, mini_llm, history, io
        )
        if was_bribe:
            continue

        was_give, give_note = _handle_give_in_loop(player_msg, npc, player, npc_trades_done, state, io, mini_llm)
        if give_note:
            history.append(give_note)

        gift_given = _check_gift_trigger(npc, player_msg, npc_gifts_given, player, state, mini_llm, io)
        if gift_given:
            gift = npc["gift"]
            parts = []
            if gift.get("item_name"):
                parts.append(f"gave the player your {gift['item_name']}")
            if gift.get("secret"):
                parts.append(f"revealed the secret: {gift['secret']}")
            history.append(f"[You just {' and '.join(parts)}. Acknowledge this naturally in your next reply.]")

        mood_delta = evaluate_mood_delta(player_msg)
        fear_delta = evaluate_fear_delta(player_msg)
        current_mood = max(-100, min(100, current_mood + mood_delta))
        current_fear = max(0,    min(100, current_fear + fear_delta))
        npc_moods[npc["name"]] = current_mood
        npc_fear[npc["name"]]  = current_fear
        debug(f"dialogue: mood delta for '{npc['name']}': {mood_delta:+d} → total: {current_mood}")
        debug(f"dialogue: fear delta for '{npc['name']}': {fear_delta:+d} → total: {current_fear}")
        emit_encounter_state(io, npc_mood=current_mood, npc_fear=current_fear)

        memories = retrieve_memories(npc["name"], player_msg)
        if memories:
            memory_context = ("What you already know about this player from past conversations:\n"
                              + "\n".join(f"- {m}" for m in memories))
            debug(f"dialogue: injecting {len(memories)} memories for '{npc['name']}'")
        else:
            memory_context = ""

        mood_tone = mood_tone_for_score(current_mood)
        fear_tone = fear_tone_for_score(current_fear)
        io.send(f"\n{npc['name']}: ")
        clean_reply, end_conversation = _get_npc_reply(
            player_msg, npc, room, memory_context, history, mood_tone, fear_tone,
            current_mood, use_web_search, tavily_client, llm, io
        )
        io.send("\n")
        history.append(f"{npc['name']}: {clean_reply}")
        facts = store_exchange(npc["name"], player_msg, clean_reply)
        gossip_targets = npc.get("gossips_with", [])
        if facts and gossip_targets:
            gossip_facts(npc["name"], facts, gossip_targets)

        if end_conversation:
            io.send(f"({npc['name']} turns away.)")
            emit_encounter_end(io)
            break


def npc_dialogue(state, SHOPS, npc_catalogue, monster_catalogue, item_catalogue, llm, mini_llm, parse_command_fn, io) -> dict:
    from handlers.shop import handle_shop

    room = state["current_room_data"]
    player_input = state.get("player_input", "").strip()
    command = parse_command_fn(player_input, state)
    target = command.get("target", "").lower() if command.get("target") else ""
    npc = find_npc(room, target) or (room["npcs"][0] if room["npcs"] else None)

    if not npc:
        debug(f"dialogue: no NPC matched target '{target}'")
        io.send("There's no one here to talk to.")
        return {"force_full_description": False}

    debug(f"dialogue: talking to '{npc['name']}' | shop: {npc.get('shop_id')} | web_search: {npc.get('can_search_web', False)}")

    if npc.get("shop_id"):
        return handle_shop(state, npc, SHOPS, mini_llm,
                           dict(state.get("npc_moods", {})), dict(state.get("npc_fear", {})), io)

    npc = {**npc, "knowledge": _build_knowledge(npc, npc_catalogue, monster_catalogue, item_catalogue)}
    player, npc_moods, npc_fear, npc_gifts_given, npc_trades_done, current_mood, current_fear = \
        _init_conversation(state, npc, room, io)

    use_web_search = npc.get("can_search_web", False)
    tavily_client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY")) if use_web_search else None

    history = []
    if npc.get("opens_conversation", True):
        opening_memories = retrieve_memories(npc["name"], "(player approaches)")
        memory_context = (
            "What you already know about this player from past conversations:\n"
            + "\n".join(f"- {m}" for m in opening_memories)
        ) if opening_memories else ""
        io.send(f"\n{npc['name']}: ")
        opening, _ = _get_npc_reply(
            "(The player approaches. Open the conversation with a greeting in character.)",
            npc, room, memory_context, [], mood_tone_for_score(current_mood),
            fear_tone_for_score(current_fear), current_mood, use_web_search, tavily_client, llm, io
        )
        io.send("\n")
        history.append(f"{npc['name']}: {opening}")

    _run_conversation_loop(npc, room, player, npc_moods, npc_fear, npc_gifts_given, npc_trades_done,
                           current_mood, current_fear, use_web_search, tavily_client,
                           history, state, llm, mini_llm, io)

    return {"player": player, "npc_moods": npc_moods, "npc_fear": npc_fear,
            "npc_gifts_given": npc_gifts_given, "npc_trades_done": npc_trades_done,
            "force_full_description": False}
