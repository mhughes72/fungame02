# AI Driven Haunted Mansion — Claude Context

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
- `data/rooms.json` — room definitions including NPCs, monsters, items
- `data/shop.json` — shop inventory

## Models
- `llm` = gpt-4o (main narrative, combat, NPC dialogue)
- `mini_llm` = gpt-4o-mini (command parsing, routing, cheap calls)

## WebSocket protocol
Server → client prefixes (stripped before JSON parse):
- `__statejson__` → `{type: "state", ...player/room fields}`
- `__roomfeatures__` → `{type: "room_features", room_id, features}`
- `__shop_data__` → `{type: "shop_data", items, mood_score, ...}`
- `__encounter_start__` / `__encounter_end__` / `__encounter_state__` → encounter events
- `__prompt__` → `{type: "prompt", text}`
- `__debug__` → `{type: "debug", text}` — streams to Debug tab in sidebar
- `__token__` / `__token_end__` → streaming LLM tokens

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
- ReAct-style tool-calling loop with `web_search` tool — decides autonomously when/whether to search
- `_make_oracle_tool`, `_run_oracle_loop` in `handlers/dialogue.py`; `ORACLE_SYSTEM_PROMPT` in `prompts.py`
- Mood ≤ -30 blocks web search entirely (in-character refusal via `WEB_SEARCH_REFUSED_PROMPT`)

### NPC Memory
- Extracts player interests/goals/questions, not just biographical facts (`NPC_MEMORY_EXTRACT_PROMPT`)
- `last_entity` in `AgentState` enables pronoun resolution ("take it" → last interacted entity)
- `_debug_io_var` in `utils.py` (ContextVar) routes `debug()` to the web UI Debug tab

## Cheat / Debug Panel
- CHEAT button opens modal with room AI feature list, cached to `data/room_features_cache.json`
- Cache keyed by MD5 hash of room NPC/monster data — auto-invalidates if `rooms.json` changes
- Commands: `cheat gold` (+100g), `win`, `room`, `goto room_X`, `clearmemory`

## Known Tech Debt
- **Graph routing**: every action cycles `load_room_data → describe_room → check_aggressive` even without a room change. Workarounds: `skip_description: True` / `force_full_description`. Revisit if a third workaround appears.
- **Minor polish**: define `DATA_DIR` constant in `main.py` (hardcoded cache path); rename `_io_var` → `_io_context_var`; add `-> dict` return type hints to handlers.

## Future AI Feature Ideas
- **NPC gossip network** — After conversations, push facts to other NPCs' Pinecone namespaces so they react to player reputation, not just direct interaction.
- **Dungeon master agent** — Background LangGraph node on a timer that injects narrative events autonomously (notes, monster migrations, rumors).
- **Vision-grounded room descriptions** — Feed room image to GPT-4o vision so descriptions reference what's actually visible.
- **Player persona inference** — Build behavioral profile from actions (bribes vs. fights, hoards vs. spends); inject into NPC prompts.

## Workflow
- Work directly on `main` branch — do NOT create worktrees unless absolutely necessary, and always ask first
- Keep this file under 200 lines
