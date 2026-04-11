import os
from typing import Annotated, Dict, List, NotRequired, Optional, Sequence, TypedDict
from urllib import response
from dotenv import load_dotenv  
from langchain_core.messages import BaseMessage, HumanMessage # The foundational class for all message types in LangGraph
from langchain_core.messages import ToolMessage # Passes data back to LLM after it calls a tool such as the content and the tool_call_id
from langchain_core.messages import SystemMessage # Message for providing instructions to the LLM
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool
from langgraph.graph.message import add_messages
from langgraph.graph import START, StateGraph, END
from langgraph.prebuilt import ToolNode
from langchain_core.prompts import ChatPromptTemplate
from sqlalchemy import text
from audio_utils import speak
import json
#from openai import OpenAItalk to aldric
from tavily import TavilyClient


from rooms import ROOMS
from prompts import ROOM_DESCRIPTION_PROMPT, COMMAND_PARSER_PROMPT, EXAMINE_PROMPT, NPC_PROMPT, WEB_SEARCH_ROLEPLAY_PROMPT

load_dotenv()

from typing import Dict, List, Optional
from typing_extensions import TypedDict, NotRequired

#testing branches

class ItemData(TypedDict):
    name: str
    hidden: bool
    revealed_by: Optional[str]
    openable: bool
    is_open: bool
    gold: int
    
class NPCData(TypedDict):
    name: str
    description: str
    personality: str
    knowledge: str
    can_search_web: bool
    
#These are things that change in the rooms, such as items and monsters. We will store these in the state and use them to override the base room data when we load it.
class RoomState(TypedDict, total=False):
    items: List[ItemData]
    monsters: List[str]
    visited: bool

#This is the data for the rooms. We will load this from the ROOMS constant and then override it with any changes from the state.
class RoomData(TypedDict):
    name: str
    description: str
    exits: Dict[str, str]
    items: List[ItemData]
    monsters: List[str]
    npcs: List[NPCData]

class PlayerState(TypedDict, total=False):
    inventory: List[str]
    health: int
    max_health: int
    status_effects: List[str]
    gold: int

#This is the state for the agent. It includes the current room ID, the current room data (which is loaded from the ROOMS constant and then overridden with any changes from the state), and the room states (which include any changes to the items and monsters in each room).

class AgentState(TypedDict):
    current_room_id: str
    current_room_data: NotRequired[RoomData]
    room_states: NotRequired[Dict[str, RoomState]]
    player_input: NotRequired[str]
    player: NotRequired[PlayerState]
    force_full_description: NotRequired[bool]
    game_over: NotRequired[bool]
    route_to: NotRequired[str]
    
llm = ChatOpenAI(
    model="gpt-4o", temperature = 0.5)

    
#This function loads the room data from the ROOMS constant and then overrides it with any changes from the state. It takes the current room ID from the state, looks up the base room data from the ROOMS constant, and then applies any overrides from the state to create the final room data that will be used in the game.
def load_room_data(state: AgentState):
    room_id = state["current_room_id"]
    base_room = ROOMS[room_id]
    room_override = state.get("room_states", {}).get(room_id, {})

    room: RoomData = {
        "name": base_room["name"],
        "description": base_room["description"],
        "exits": dict(base_room["exits"]),
        "monsters": list(room_override.get("monsters", base_room["monsters"])),
        "items": list(room_override.get("items", base_room["items"])),  # full ItemData list
        "npcs": list(base_room.get("npcs", [])),
    }

    return {
        "current_room_data": room,
        "route_to": None
    }


def visible_items(room: RoomData) -> List[ItemData]:
    """Items the player can currently see."""
    return [i for i in room["items"] if not i["hidden"]]

def find_item(room: RoomData, name: str, include_hidden=False) -> Optional[ItemData]:
    """Find an item by name, optionally including hidden ones."""
    items = room["items"] if include_hidden else visible_items(room)
    return next((i for i in items if i["name"] == name), None)

def describe_room(state: AgentState) -> dict:
    room = state["current_room_data"]  # type: ignore
    room_id = state["current_room_id"]

    room_states = dict(state.get("room_states", {}))
    room_override = dict(room_states.get(room_id, {}))

    already_visited = room_override.get("visited", False)
    force_full_description = state.get("force_full_description", False)

    if not already_visited or force_full_description:
        visible = [i["name"] for i in room["items"] if not i["hidden"]]

# Add hints for unopened containers
        container_hints = []
        for i in room["items"]:
            if not i["hidden"] and i.get("openable") and not i.get("is_open"):
                container_hints.append(f"The {i['name']} looks like it might contain something")

        prompt = ROOM_DESCRIPTION_PROMPT.invoke({
            "name": room["name"],
            "description": room["description"],
            "items": ", ".join(visible) if visible else "none",
            "containers": ". ".join(container_hints) if container_hints else "none",
            "monsters": ", ".join(room["monsters"]) if room["monsters"] else "none",
            "npcs": ", ".join(n["name"] for n in room["npcs"]) if room["npcs"] else "none",
            "exits": ", ".join(room["exits"].keys()) if room["exits"] else "none",
        })

        response = llm.invoke(prompt)
        print(response.content)
        #text = str(response.content)
        #speak(text)

        room_override["visited"] = True
        room_states[room_id] = room_override

        return {
            "room_states": room_states,
            "force_full_description": False
        }

    short_desc = (
        f"You are back in the {room['name']}. "
        f"Items: {', '.join(i["name"] for i in room['items'] if not i['hidden']) or 'none'}. "
        f"Exits: {', '.join(room['exits'].keys()) if room['exits'] else 'none'}."
    )
    print(short_desc)

    return {
        "force_full_description": False
    }

def get_player_action(state: AgentState) -> dict:
    player_input = input("\nWhat do you do? ").strip().lower()
    return {
        "player_input": player_input
    }

def parse_command(player_input: str, state: AgentState) -> dict:
    room = state["current_room_data"]
    player = state.get("player", {})
    inventory = player.get("inventory", [])

    prompt = COMMAND_PARSER_PROMPT.invoke({
        "room_name": room["name"],
        "exits": ", ".join(room["exits"].keys()) or "none",
        "items": ", ".join(i["name"] for i in room["items"] if not i["hidden"]) or "none",        
        "monsters": ", ".join(room["monsters"]) or "none",
        "npcs": ", ".join(n["name"] for n in room["npcs"]) or "none",
        "inventory": ", ".join(inventory) or "nothing",
        "player_input": player_input,
    })

    response = llm.invoke(prompt)
    
    try:
        text = response.content.strip()
        # Strip any markdown fences
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        text = text.strip()
        parsed = json.loads(text)
        print(f"[DEBUG] Parsed command: {parsed}")  # Remove later
        return parsed
    except Exception as e:
        print(f"[DEBUG] Parse error: {e}, raw response: {response.content}")
        return {"action": "unknown", "target": None}



def npc_dialogue(state: AgentState) -> dict:
    room = state["current_room_data"]
    player_input = state.get("player_input", "").strip()

    command = parse_command(player_input, state)
    target = command.get("target", "").lower() if command.get("target") else ""

    npc = next(
        (n for n in room["npcs"] if n["name"].lower() in target or target in n["name"].lower()),
        room["npcs"][0] if room["npcs"] else None
    )

    if not npc:
        print("There's no one here to talk to.")
        return {"force_full_description": False}

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

        if use_web_search:
            search_result = tavily_client.search(player_msg)
            raw_facts = "\n".join([r["content"] for r in search_result["results"]])

            roleplay_prompt = WEB_SEARCH_ROLEPLAY_PROMPT.format(
                npc_name=npc["name"],
                personality=npc["personality"],
                knowledge=npc["knowledge"],
                player_msg=player_msg,
                raw_facts=raw_facts,
                history=chr(10).join(history),
            )
            reply = llm.invoke([
                SystemMessage(content=roleplay_prompt),
                HumanMessage(content="Respond in character now.")
            ]).content

        else:
            prompt = NPC_PROMPT.invoke({
                "npc_name": npc["name"],
                "personality": npc["personality"],
                "knowledge": npc["knowledge"],
                "room_name": room["name"],
                "history": "\n".join(history),
                "player_input": player_msg,
            })
            reply = str(llm.invoke(prompt).content)

        end_conversation = "[END CONVERSATION]" in reply
        clean_reply = reply.replace("[END CONVERSATION]", "").strip()

        print(f"\n{npc['name']}: {clean_reply}\n")
        history.append(f"{npc['name']}: {clean_reply}")

        if end_conversation:
            print(f"({npc['name']} turns away.)")
            break

    return {"force_full_description": False}


def resolve_action(state: AgentState) -> dict:
    player_input = state.get("player_input", "").strip()
    room = state["current_room_data"]
    room_id = state["current_room_id"]

    room_states = dict(state.get("room_states", {}))
    room_override = dict(room_states.get(room_id, {}))
    player = dict(state.get("player", {}))
    inventory = list(player.get("inventory", []))

    # Parse the command using LLM
    command = parse_command(player_input, state)
    action = command.get("action", "unknown")
    target = command.get("target")
    print(f"\n[Input: '{player_input}' → action: '{action}', target: '{target}']")

    if action == "talk":
        return {"route_to": "npc_dialogue"}
    
    if action == "quit":
        print("Goodbye.")
        return {"game_over": True}

    if action == "go":
        if target in room["exits"]:
            print(f"You head {target}.")
            return {
                "current_room_id": room["exits"][target],
                "force_full_description": False
            }
        print(f"You can't go {target} from here.")
        return {"force_full_description": False}

    if action == "take":
        item = find_item(room, target)
        if item:
            new_items = [i for i in room["items"] if i["name"] != target]
            room_override["items"] = new_items
            room_states[room_id] = room_override
            inventory.append(target)
            player["inventory"] = inventory
            print(f"You take the {target}.")
            return {
                "room_states": room_states,
                "player": player,
                "force_full_description": False
            }
        print(f"There's no {target} here.")
        return {"force_full_description": False}

    if action == "inventory":
        if inventory:
            print("You are carrying: " + ", ".join(inventory))
        else:
            print("Your inventory is empty.")
        print(f"Gold: {player.get('gold', 0)} coins")
        return {"force_full_description": False}

    if action == "examine":
        new_items = []
        newly_revealed = []
        for item in room["items"]:
            if item["hidden"] and item["revealed_by"] == target:
                new_items.append({**item, "hidden": False})
                newly_revealed.append(item["name"])
            else:
                new_items.append(item)

        if newly_revealed:
            room_override["items"] = new_items
            room_states[room_id] = room_override

        discovery_text = f"The player discovers: {', '.join(newly_revealed)}" if newly_revealed else ""

        prompt = EXAMINE_PROMPT.invoke({
            "target": target,
            "room_name": room["name"],
            "room_description": room["description"],
            "discovery_text": discovery_text,
        })
        response = llm.invoke(prompt)
        print(response.content)

        if newly_revealed:
            print(f"\n[You find: {', '.join(newly_revealed)}]")
            return {
                "room_states": room_states,
                "force_full_description": False
            }

        return {"force_full_description": False}

    if action == "attack":
        if target in room["monsters"]:
            new_monsters = [m for m in room["monsters"] if m != target]
            room_override["monsters"] = new_monsters
            room_states[room_id] = room_override
            print(f"You defeated the {target}!")
            return {
                "room_states": room_states,
                "force_full_description": False
            }
        print(f"There is no {target} here.")
        return {"force_full_description": False}

    if action == "open":
        item = find_item(room, target)
        
        if not item:
            print(f"There's no {target} here.")
            return {"force_full_description": False}
        
        if not item.get("openable"):
            print(f"You can't open the {target}.")
            return {"force_full_description": False}
        
        if item.get("is_open"):
            print(f"The {target} is already open.")
            return {"force_full_description": False}

        gold_found = item.get("gold", 0)

        # Mark item as open AND zero out the gold
        new_items = []
        for i in room["items"]:
            if i["name"] == target:
                new_items.append({**i, "is_open": True, "gold": 0})
            else:
                new_items.append(i)

        room_override["items"] = new_items
        room_states[room_id] = room_override

        if gold_found > 0:
            current_gold = player.get("gold", 0)
            player["gold"] = current_gold + gold_found
            print(f"You open the {target} and find {gold_found} gold coins inside!")
            print(f"You now have {player['gold']} gold.")
            return {
                "room_states": room_states,
                "player": player,
                "force_full_description": False
            }

        print(f"You open the {target} but find nothing of value inside.")
        return {
            "room_states": room_states,
            "force_full_description": False
        }


    if action == "room":
        visible = [i for i in room["items"] if not i["hidden"]]
        hidden = [i for i in room["items"] if i["hidden"]]
        
        print(f"\n--- {room['name']} ---")
        print(f"Visible items: {', '.join(i['name'] for i in visible) if visible else 'none'}")
        
        if hidden:
            print("Hidden items:")
            for i in hidden:
                print(f"  - {i['name']} (hidden behind: {i['revealed_by']})")
        else:
            print("Hidden items: none")

        # Show containers and their gold status
        containers = [i for i in visible if i.get("openable")]
        if containers:
            print("Containers:")
            for i in containers:
                if i.get("is_open"):
                    print(f"  - {i['name']} (open, empty)")
                else:
                    print(f"  - {i['name']} (unopened, contains {i.get('gold', 0)} gold)")
        
        print(f"Monsters: {', '.join(room['monsters']) if room['monsters'] else 'none'}")
        print(f"NPCs:     {', '.join(n['name'] for n in room['npcs']) if room['npcs'] else 'none'}")
        print(f"Exits:    {', '.join(room['exits'].keys())}")
        return {"force_full_description": False}


def next_step(state: AgentState) -> str:
    if state.get("game_over"):
        return END
    if state.get("route_to") == "npc_dialogue":
        return "npc_dialogue"
    return "load_room_data"

graph = StateGraph(AgentState)
graph.add_node("load_room_data", load_room_data)
graph.add_node("describe_room", describe_room)
graph.add_node("get_player_action", get_player_action)
graph.add_node("resolve_action", resolve_action)
graph.add_node("npc_dialogue", npc_dialogue)


graph.add_edge(START, "load_room_data")
graph.add_edge("load_room_data", "describe_room")
graph.add_edge("describe_room", "get_player_action")
graph.add_edge("get_player_action", "resolve_action")
#graph.add_edge("resolve_action", "load_room_data")
graph.add_edge("npc_dialogue", "load_room_data")

graph.add_conditional_edges(
    "resolve_action",
    next_step,
    {
        "load_room_data": "load_room_data",
        "npc_dialogue": "npc_dialogue",
        END: END,
    }
)

app = graph.compile()


initial_state_1 = AgentState(
    current_room_id="room_1",
    player={
        "inventory": [],
        "health": 100,
        "max_health": 100,
        "status_effects": [],
        "gold": 0
    }
)
app.invoke(initial_state_1)





#Draw the graph
img = app.get_graph().draw_mermaid_png()
with open("graph.png", "wb") as f:
    f.write(img)
print("Saved graph.png")
'''
initial_state_1 = AgentState(
    current_room_id="room_1"

)


initial_state_1 = AgentState(
    current_room_id="room_1",
    room_states={
        "room_1": {
            "items": ["silver key", "mysterious note"],
            "monsters": ["giant spider"],
            "visited": True
        }
    }
)

'''




'''
img = app.get_graph().draw_mermaid_png()
with open("graph.png", "wb") as f:
    f.write(img)
print("Saved graph.png")
'''





