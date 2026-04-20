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

## WebSocket message protocol
Server → client prefixes (stripped before JSON parse):
- `__statejson__` → `{type: "state", ...player/room fields}`
- `__roomfeatures__` → `{type: "room_features", room_id, features}`
- `__encounter_start__` → `{type: "encounter_start", ...}`
- `__encounter_end__` → `{type: "encounter_end"}`
- `__encounter_state__` → `{type: "encounter_state", ...}`
- `__prompt__` → `{type: "prompt", text}`

## Workflow
- Work directly on `main` branch — do NOT create worktrees
- Keep this file under 200 lines
