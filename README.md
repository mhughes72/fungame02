# The Haunted Mansion — Gothic Text Adventure

A dark, atmospheric text adventure game built with LangGraph and OpenAI.
Explore a haunted mansion, battle monsters, collect weapons and armour,
find hidden items, trade with merchants, and converse with mysterious NPCs
— all through natural language commands powered by an LLM.

## Tech Stack

- **LangGraph** — game loop and state management
- **LangChain + OpenAI GPT-4o** — natural language command parsing, room descriptions, NPC dialogue, combat narration
- **Tavily** — real-time web search for the Oracle NPC
- **Python 3.10+**

## Project Structure

fungame/
data/
rooms.json          # All room definitions, items, monsters, NPCs
shop.json           # Merchant stock and pricing
handlers/
init.py         # Exports all action handlers
movement.py         # Movement between rooms
items.py            # Take, examine, open, equip, unequip
player.py           # Inventory and room status display
shop.py             # Merchant shop system with LangChain tools
main.py               # Game state, LangGraph nodes and graph
prompts.py            # All LLM prompts
utils.py              # Shared utility functions
audio_utils.py        # Text-to-speech (currently disabled)
.env                  # API keys (not committed)
requirements.txt      # Python dependencies


## Requirements

- Python 3.10+
- OpenAI API key
- Tavily API key (free tier — 1,000 searches/month)

## Setup

### 1. Clone the repo
```bash
git clone https://github.com/YOUR_USERNAME/fungame.git
cd fungame
```

### 2. Create and activate a virtual environment
```bash
# Windows
python -m venv .venv
.venv\Scripts\activate

# Mac/Linux
python -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Create a `.env` file in the project root

OPENAI_API_KEY=your-openai-api-key-here
TAVILY_API_KEY=your-tavily-api-key-here

**Getting API keys:**
- OpenAI: https://platform.openai.com → API Keys
- Tavily: https://tavily.com → Sign up free

### 5. Run the game
```bash
python main.py
```

## Game Commands

The game understands natural language — just type what you want to do.

### Movement
| Input | Result |
|-------|--------|
| `north` / `go north` / `walk north` | Move in that direction |
| `south` / `east` / `west` / `up` / `down` | Move in that direction |

### Items
| Input | Result |
|-------|--------|
| `take rusty key` / `grab the key` | Pick up an item |
| `examine old book` / `look at fireplace` | Examine something |
| `open chest` / `pry open the box` | Open a container |
| `equip iron sword` / `wield the dagger` | Equip a weapon or armour |
| `unequip helmet` / `remove the cloak` | Unequip an item |

### Combat
| Input | Result |
|-------|--------|
| `attack ghost` / `fight the rat` | Enter combat with a monster |
| `attack` / `hit` | Attack during combat |
| `flee` / `run` / `escape` | Attempt to flee combat (60% success) |

### NPCs
| Input | Result |
|-------|--------|
| `talk to aldric` / `speak with oracle` | Start a conversation |
| `talk to aldous` / `visit the merchant` | Open the shop |
| `goodbye` / `bye` / `farewell` | End a conversation |

### Player Status
| Input | Result |
|-------|--------|
| `inventory` / `what am I carrying` | Show inventory, equipped items and gold |
| `room` / `where am i` | Show full room state including hidden items |
| `look` / `look around` | Re-describe the current room |

### Debug Commands
| Input | Result |
|-------|--------|
| `goto room_6` | Teleport to a specific room |
| `quit` | Exit the game |

## Architecture

The game is built as a LangGraph state graph. Each turn flows through these nodes:

START
→ load_room_data       # Load room from JSON, apply state overrides
→ describe_room        # LLM generates room description (first visit)
→ get_player_action    # Wait for player input
→ resolve_action       # Parse command, dispatch to handler
→ [combat]             # Optional: turn-based combat loop
→ [npc_dialogue]       # Optional: NPC conversation loop
→ load_room_data       # Loop


State is stored in `AgentState` which tracks:
- Current room and room overrides (items taken, monsters defeated, containers opened)
- Player stats (health, gold, inventory, equipped weapon and armour)
- Routing flags for combat and NPC dialogue nodes

## Key Design Decisions

- **Natural language parsing** — player input is parsed by GPT-4o into structured actions rather than keyword matching
- **Inventory stores full item dicts** — not just strings, so weapon/armour stats are always available without searching room data
- **Room state overrides** — base room data lives in JSON, changes (items taken, monsters killed) are stored as overrides in game state
- **LangChain tools for the shop** — the merchant uses an agentic tool-calling loop to process real transactions in character
- **Two-step web search** — the Oracle NPC uses Tavily for raw facts then GPT-4o to deliver them in character

## Adding Content

### Add a new room
Edit `data/rooms.json` — add a new room entry and connect it via `exits` in an existing room.

### Add a new NPC
Add an NPC dict to a room's `npcs` list in `rooms.json`. Set `can_search_web: true` to give them Tavily access. Set `shop_id` to connect them to a shop.

### Add a new merchant
Add a new entry to `data/shop.json` and set `shop_id` on the NPC to match.

### Add a new action
1. Add the action rule to `COMMAND_PARSER_PROMPT` in `prompts.py`
2. Write a handler function in the appropriate `handlers/` file
3. Add it to the `handlers` dict in `resolve_action` in `main.py`

## Branches

| Branch | Description |
|--------|-------------|
| `main` | Stable terminal version |
| `web-app` | Web browser version (in progress) |

## Branch Commands
```bash
# Switch to terminal version
git checkout main

# Switch to web app version
git checkout web-app

# Create a new branch
git checkout -b branch-name

# Save and push changes
git add .
git commit -m "describe your change"
git push origin main
```

## Notes

- Game state is not persisted between sessions — each run starts fresh
- All LLM calls use the global `GAME_SYSTEM_PROMPT` for consistent gothic tone
- The Oracle NPC uses real web search — each question costs a Tavily API credit
- OpenAI costs accrue per session — monitor usage at platform.openai.com/usage

