# AI Driven Haunted Mansion — Codex Context

## Project
Gothic text adventure powered by LangGraph + OpenAI. FastAPI WebSocket server, CLI and web UI.

## Stack
- `main.py` — LangGraph state graph, all game nodes, AgentState
- `handlers/` — combat, dialogue, items, movement, player, shop
- `prompts.py` — all LLM prompts
- `npc_memory.py` — RAG/HyDE memory with embeddings
- `utils.py` — emit_player_state, debug, mood/fear helpers
- `server.py` — FastAPI WebSocket, bridges async server ↔ sync game thread
- `static/index.html` — entire frontend (single file)
- `data/rooms.json` — room definitions (items inline; monsters/npcs as ID references)
- `data/monsters.json` — monster definitions catalogue (keyed by ID)
- `data/npcs.json` — NPC definitions catalogue (keyed by ID)
- `data/items.json` — item descriptions catalogue (keyed by slug; used by knows_about lookups)
- `data/shop.json` — shop inventory

## Models
- `llm` = gpt-4o (main narrative, combat, NPC dialogue)
- `mini_llm` = gpt-4o-mini (command parsing, routing, cheap calls)

## WebSocket message protocol
Server → client prefixes (stripped before JSON parse):
- `__statejson__` → `{type: "state", ...player/room fields}`
- `__roomfeatures__` → `{type: "room_features", room_id, features}`
- `__shop_data__` → `{type: "shop_data", items, mood_score, ...}`
- `__encounter_start__` → `{type: "encounter_start", ...}`
- `__encounter_end__` → `{type: "encounter_end"}`
- `__encounter_state__` → `{type: "encounter_state", ...}`
- `__prompt__` → `{type: "prompt", text}`
- `__debug__` → `{type: "debug", text}` — streams to Debug tab in sidebar
- `__token__` → `{type: "token", text}` — single streamed LLM token
- `__token_end__` → `{type: "token_end"}` — marks end of streamed response

CLI strips all `__`-prefixed messages (`CLIContext.send` in `io_context.py`).

## Game Mechanics

### NPC Emotional State
- **mood**: -100 to +100, affects cooperativeness and shop prices
- **fear**: 0 to 100, affects prices and dialogue behavior
- Thresholds in `utils.py`: mood (50/20/-9/-40), fear (60/30/10)

### Combat
- Weapon damage + d6, minus monster defense
- Armor reduces damage by `armor_rating * 0.05` (capped 75%)
- Weakness bonus: +5 if weapon type matches monster weakness

### Shop
- Aldous uses LangChain tool-calling for transactions
- Price multiplier: `min(mood_multiplier, fear_multiplier)`

### Oracle NPC
- ReAct-style tool-calling loop: given a `web_search` tool, decides autonomously when/whether to call it
- Implemented in `handlers/dialogue.py`: `_make_oracle_tool`, `_run_oracle_loop`, prompt `ORACLE_SYSTEM_PROMPT`
- Mood ≤ -30 blocks web search entirely (in-character refusal via `WEB_SEARCH_REFUSED_PROMPT`)

### NPC Memory
- `NPC_MEMORY_EXTRACT_PROMPT` captures player interests/goals/questions, not just biographical facts
- `store_exchange()` in `npc_memory.py` returns extracted facts so the caller can pass them to `gossip_facts()`
- `last_entity` in `AgentState` tracks the last item/NPC/monster interacted with; injected into `COMMAND_PARSER_PROMPT` for pronoun resolution ("take satchel" → "open it")
- `_debug_io_var` in `utils.py` (ContextVar) lets `debug()` stream to the web UI Debug tab

### NPC Gossip Network
- After each exchange, `gossip_facts()` in `npc_memory.py` filters facts via `NPC_GOSSIP_FILTER_PROMPT` (strips bribes/threats, keeps goals/name/kills) then upserts to connected NPCs' namespaces with `"Rumour (from X): "` prefix
- Social graph defined per NPC in `npcs.json` via `gossips_with: [list of NPC names]`
- The Oracle has `gossips_with: []` (isolated); Shadow gossips with all

### Gossip Emotional Impact
- `evaluate_gossip_impact(npc_name)` in `npc_memory.py` queries the NPC's Pinecone namespace for rumour-type memories at conversation start
- Passes found rumours to `gpt-4o-mini` via `NPC_GOSSIP_IMPACT_PROMPT` → returns `(mood_delta, fear_delta)` capped at ±20
- Applied once in `npc_dialogue` before the greeting; `emit_encounter_state` fires immediately so the UI reflects the adjusted scores

### NPC Conversation Opening
- `opens_conversation: true` in `npcs.json` triggers a greeting LLM call before the input loop
- Uses `_get_npc_reply()` with synthetic `"(The player approaches...)"` message — mood, fear, and memory all apply
- Opening line added to `history`; no `store_exchange` call (no real player input to extract from)
- The Oracle has `opens_conversation: false`

### NPC Knowledge Graph
- `knows_about` field in `npcs.json` is a dict of namespaced entity keys → supplemental secret text
- Key format: `"npc:<id>"`, `"monster:<id>"`, `"item:<id>"` (bare keys treated as NPC for backwards compat)
- `_build_knowledge()` in `handlers/dialogue.py` resolves each key against `NPC_CATALOGUE`, `MONSTER_CATALOGUE`, or `ITEM_CATALOGUE` and appends name + description + secret to the NPC's knowledge string
- Empty secret string = description only; non-empty = appended as "Your private knowledge: ..."
- `data/items.json` is the item descriptions catalogue; gameplay data (damage, armor, etc.) stays inline in `rooms.json`

### Monster Data
- `monsters.json` has `description` (appearance) and `behavior` (combat style) fields
- Both injected into `COMBAT_PROMPT` and `FLEE_PROMPT`; description injected inline into `ROOM_DESCRIPTION_PROMPT`

## Cheat / Debug Panel
- CHEAT button in header opens modal with room-specific AI feature list
- Features generated by `gpt-4o-mini` on first room visit, cached to `data/room_features_cache.json`
- Cache keyed by MD5 hash of room NPC/monster data — auto-invalidates if `rooms.json` changes
- `cheat gold` command adds 100 gold (handled pre-LLM in `resolve_action`, uses `skip_description: True`)
- Debug commands in modal: `win`, `room`, `goto room_X`, `clearmemory`

## Known Tech Debt

### Graph always routes through load_room_data
Every action cycles back through `load_room_data → describe_room → check_aggressive` even when the room didn't change. We use `skip_description: True` / `force_full_description` as workarounds. A cleaner design would route non-room-changing actions directly back to `get_player_action`, only hitting `load_room_data` on actual room transitions. Revisit when a third `skip_description` workaround appears or new action types need to bypass room logic.

## Planned Refactoring

Work through these in priority order. Check off each one as completed.

### HIGH — Across multiple handlers, high leverage

- [x] **State dict copy boilerplate** — `get_mutable_player(state)` → `(player, inventory)` and `get_mutable_room(state, room_id)` → `(room_states, room_override)` added to `utils.py`. All handlers updated.

- [x] **JSON emitter duplication** — `emit_encounter_start()`, `emit_encounter_state()`, `emit_encounter_end()` added to `utils.py`. All handlers updated.

- [x] **server.py message dispatch** — `_DISPATCH` dict maps prefix → `(ws_type, has_json)` in `server.py`. Adding a new message type is a one-liner.

### MEDIUM — Isolated but messy

- [x] **NPC lookup helper** — `find_npc(room, target) -> dict | None` added to `utils.py`. Updated dialogue (bribe + npc_dialogue) and items (handle_examine).

- [x] **`npc_slug` construction** — `make_slug(name: str)` added to `utils.py`. Updated combat (monster_slug), dialogue, and shop (npc_slug).

- [x] **Split long handlers** — `combat_node`, `npc_dialogue`, `handle_shop` broken into private phase functions: `_find_monster`, `_handle_flee`, `_execute_attack_round`, `_handle_victory` (combat); `_handle_bribe_in_loop`, `_get_npc_reply` (dialogue); `_run_tool_loop` (shop, eliminates duplicated agentic loop).

- [x] **Dead code in audio_utils.py** — duplicates already removed; `audio_utils.py` now only contains `speak()`.

### LOW — Polish

- [x] **Hardcoded cache path** — `DATA_DIR = "data"` constant added to `main.py`. Used in loader and cache path.
- [x] **`_io_var` naming** — renamed to `_io_context_var` in `main.py`.
- [x] **Missing return type hints** — added `-> dict` and other return types across all handlers, utils, and private helpers.

## Future AI Feature Ideas

- **Streaming LLM output** — Stream tokens to browser as generated via `.stream()` instead of `.invoke()`. Add `io.stream(chunks)` to IOContext, `__token__` WebSocket prefix, frontend appends tokens to a live span. Affects: room descriptions, NPC dialogue, combat narration, examine. **[DONE]**
- **NPC gossip network** — NPCs share memories via the existing Pinecone RAG. After a conversation, push key facts to other NPCs' namespaces so they react to player reputation, not just direct interaction. **[DONE]**
- **Dungeon master agent** — Background LangGraph node on a timer that monitors game state and injects narrative events autonomously (notes, monster migrations, rumors). Teaches autonomous agent behavior.
- **Vision-grounded room descriptions** — Feed pre-generated room image into GPT-4o vision when describing a room so text references what's actually visible. Teaches multimodal pipelines.
- **Player persona inference** — Build a behavioral profile from actions (bribes vs. fights, hoards vs. spends), inject into NPC prompts so characters react to reputation not just words.

## Workflow
- Work directly on `main` branch — do NOT create worktrees unless absolutely necessary for a specific change, and always ask first
- Keep this file under 200 lines
