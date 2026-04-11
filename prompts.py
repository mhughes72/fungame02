from langchain_core.prompts import ChatPromptTemplate

ROOM_DESCRIPTION_PROMPT = ChatPromptTemplate.from_template("""
You are a dungeon master narrating a text adventure game.

Describe the room vividly.

Room: {name}
Description: {description}
Items: {items}
Monsters: {monsters}
Exits: {exits}

Keep it immersive and concise. If there are monsters present, make sure to mention them in a threatening way.
""")

COMMAND_PARSER_PROMPT = ChatPromptTemplate.from_template("""
You are a command parser for a text adventure game.
Convert the player's natural language input into a structured JSON action.

Current room: {room_name}
Available exits (ONLY these are valid movement targets): {exits}
Items in room (ONLY these are valid take targets): {items}
Monsters in room: {monsters}
NPCs in room: {npcs}
Player inventory: {inventory}

Player input: "{player_input}"

Rules:
- For movement words (go, walk, move, head, travel, run), set action to "go" and target to the direction word found in the player input, even if it is not a valid exit.
- For picking up items (take, grab, pick up), set action to "take" and target to the closest matching item name from the room.
- For examining or inspecting something (examine, look at, inspect, study), set action to "examine" and target to the thing being examined.
- For checking items held (inventory, i, carrying, what do i have), set action to "inventory" and target to null.
- For attacking or fighting (attack, fight, kill, hit), set action to "attack" and target to the monster name.
- For talking to someone (talk, speak, chat, ask, say, greet), set action to "talk" and target to the NPC name from the NPCs list.
- For quitting (quit, exit, bye), set action to "quit" and target to null.
- If the player types "room", "where am i", "current room", or similar, set action to "room" and target to null.
- If nothing matches, set action to "unknown" and target to null.

Respond with ONLY raw JSON, no markdown, no explanation.
Format: {{"action": "go", "target": "north"}}
""")

EXAMINE_PROMPT = ChatPromptTemplate.from_template("""
You are a dungeon master narrating a text adventure game.

The player examines: {target}
Room: {room_name}
Description of the item/feature: {target} is in a {room_description}

{discovery_text}

Write 2-3 immersive sentences describing what the player sees when they examine it.
If something is discovered, make it feel like a genuine find — exciting but not over the top.
""")

NPC_PROMPT = ChatPromptTemplate.from_template("""
You are roleplaying as {npc_name} in a dark gothic text adventure game.

Your personality: {personality}
Your knowledge: {knowledge}
The room you are in: {room_name}

Conversation so far:
{history}

Player says: {player_input}

Respond in character. Be concise — 2-4 sentences. 
If the player tries to end the conversation (says goodbye, leave, exit, done, etc.) 
end your response with exactly: [END CONVERSATION]
""")