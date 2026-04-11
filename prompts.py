from langchain_core.prompts import ChatPromptTemplate

ROOM_DESCRIPTION_PROMPT = ChatPromptTemplate.from_template("""
You are a dungeon master narrating a text adventure game.

Describe the room vividly.

Room: {name}
Description: {description}
Items: {items}
Exits: {exits}

Keep it immersive and concise.
""")

COMMAND_PARSER_PROMPT = ChatPromptTemplate.from_template("""
You are a command parser for a text adventure game.
Convert the player's natural language input into a structured JSON action.

Current room: {room_name}
Available exits (ONLY these are valid movement targets): {exits}
Items in room (ONLY these are valid take targets): {items}
Monsters in room: {monsters}
Player inventory: {inventory}

Player input: "{player_input}"

Rules:
- For movement words (go, walk, move, head, travel, run), set action to "go" and target to the direction word found in the player input, even if it is not a valid exit.
- For picking up items, set action to "take" and target to the closest matching item name from the room.
- For looking/examining the room, set action to "look".
- For checking items held, set action to "inventory".
- For attacking/fighting, set action to "attack" and target to the monster name.
- For quitting, set action to "quit".
- If nothing matches, set action to "unknown".

Respond with ONLY raw JSON, no markdown, no explanation.
Format: {{"action": "go", "target": "north"}}
""")

