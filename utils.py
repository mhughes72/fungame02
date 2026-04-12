from langchain_core.messages import SystemMessage
from prompts import GAME_SYSTEM_PROMPT

def visible_items(room):
    return [i for i in room["items"] if not i["hidden"]]

def find_item(room, name, include_hidden=False):
    items = room["items"] if include_hidden else visible_items(room)
    return next((i for i in items if i["name"] == name), None)

def invoke_with_system(llm, prompt):
    if hasattr(prompt, 'to_messages'):
        messages = prompt.to_messages()
    elif isinstance(prompt, list):
        messages = prompt
    else:
        messages = [prompt]

    # Don't prepend if already has a system message
    if messages and isinstance(messages[0], SystemMessage):
        return llm.invoke(messages)

    return llm.invoke([SystemMessage(content=GAME_SYSTEM_PROMPT)] + messages)