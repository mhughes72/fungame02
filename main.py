from typing import Annotated, Dict, List, NotRequired, Optional, Sequence, TypedDict
from urllib import response
from dotenv import load_dotenv  
from langchain_core.messages import BaseMessage # The foundational class for all message types in LangGraph
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


from rooms import ROOMS
from prompts import ROOM_DESCRIPTION_PROMPT, COMMAND_PARSER_PROMPT


load_dotenv()

from typing import Dict, List, Optional
from typing_extensions import TypedDict, NotRequired

#These are things that change in the rooms, such as items and monsters. We will store these in the state and use them to override the base room data when we load it.
class RoomState(TypedDict, total=False):
    items: List[str]
    monsters: List[str]
    visited: bool

#This is the data for the rooms. We will load this from the ROOMS constant and then override it with any changes from the state.
class RoomData(TypedDict):
    name: str
    description: str
    exits: Dict[str, str]
    items: List[str]
    monsters: List[str]

class PlayerState(TypedDict, total=False):
    inventory: List[str]
    health: int
    max_health: int
    status_effects: List[str]

#This is the state for the agent. It includes the current room ID, the current room data (which is loaded from the ROOMS constant and then overridden with any changes from the state), and the room states (which include any changes to the items and monsters in each room).

class AgentState(TypedDict):
    current_room_id: str
    current_room_data: NotRequired[RoomData]
    room_states: NotRequired[Dict[str, RoomState]]
    player_input: NotRequired[str]
    player: NotRequired[PlayerState]
    force_full_description: NotRequired[bool]
    game_over: NotRequired[bool]
    
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
        "items": list(room_override.get("items", base_room["items"])),
        "monsters": list(room_override.get("monsters", base_room["monsters"])),
    }

    print(f"BASE ITEMS: {base_room['items']}")
    print(f"OVERRIDE ITEMS: {room_override.get('items')}")
    print(f"FINAL ROOM ITEMS: {room['items']}")

    return {
        "current_room_data": room
    }



def describe_room(state: AgentState) -> dict:
    room = state["current_room_data"]  # type: ignore
    room_id = state["current_room_id"]

    room_states = dict(state.get("room_states", {}))
    room_override = dict(room_states.get(room_id, {}))

    already_visited = room_override.get("visited", False)
    force_full_description = state.get("force_full_description", False)

    if not already_visited or force_full_description:
        prompt = ROOM_DESCRIPTION_PROMPT.invoke({
            "name": room["name"],
            "description": room["description"],
            "items": ", ".join(room["items"]) if room["items"] else "none",
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
        f"You see {', '.join(room['items']) if room['items'] else 'nothing of note'}. "
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
        "items": ", ".join(room["items"]) or "none",
        "monsters": ", ".join(room["monsters"]) or "none",
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
        current_items = list(room["items"])
        if target in current_items:
            current_items.remove(target)
            inventory.append(target)
            room_override["items"] = current_items
            room_states[room_id] = room_override
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
        return {"force_full_description": False}

    if action == "look":
        return {"force_full_description": True}

    if action == "examine":
        # Placeholder — perfect hook for the examine feature later!
        print(f"You examine the {target}. Nothing special stands out.")
        return {"force_full_description": False}

    if action == "attack":
        print(f"You ready yourself to fight the {target}!")
        # Hook for combat system later
        return {"force_full_description": False}

    print("You're not sure how to do that.")
    return {"force_full_description": False}


def next_step(state: AgentState) -> str:
    if state.get("game_over"):
        return END
    return "load_room_data"

graph = StateGraph(AgentState)
graph.add_node("load_room_data", load_room_data)
graph.add_node("describe_room", describe_room)
graph.add_node("get_player_action", get_player_action)
graph.add_node("resolve_action", resolve_action)

graph.add_edge(START, "load_room_data")
graph.add_edge("load_room_data", "describe_room")
graph.add_edge("describe_room", "get_player_action")
graph.add_edge("get_player_action", "resolve_action")
#graph.add_edge("resolve_action", "load_room_data")
graph.add_conditional_edges(
    "resolve_action",
    next_step,
    {
        "load_room_data": "load_room_data",
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
        "status_effects": []
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





