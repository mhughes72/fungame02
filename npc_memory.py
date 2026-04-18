import os
import json
import uuid
from pinecone import Pinecone, ServerlessSpec
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage
from utils import debug
from prompts import NPC_MEMORY_HYDE_PROMPT, NPC_MEMORY_EXTRACT_PROMPT, NPC_MOOD_PROMPT, NPC_FEAR_PROMPT


INDEX_NAME = "fungame-npc-memory"
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMS = 1536
SIMILARITY_THRESHOLD = 0.25

_index = None
_mini_llm = None

def _get_mini_llm():
    global _mini_llm
    if _mini_llm is None:
        _mini_llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)
    return _mini_llm

def _get_index():
    global _index
    if _index is not None:
        return _index
    pc = Pinecone(api_key=os.getenv("PINECONE_API_KEY"))
    existing = [i.name for i in pc.list_indexes()]
    if INDEX_NAME not in existing:
        pc.create_index(
            name=INDEX_NAME,
            dimension=EMBEDDING_DIMS,
            metric="cosine",
            spec=ServerlessSpec(cloud="aws", region="us-east-1")
        )
    _index = pc.Index(INDEX_NAME)
    return _index

def _namespace(npc_name: str) -> str:
    return npc_name.lower().replace(" ", "_")

def _embeddings():
    return OpenAIEmbeddings(model=EMBEDDING_MODEL)

def clear_all_memories() -> None:
    """Wipe all NPC memories from Pinecone. Called at game start for a fresh playthrough."""
    index = _get_index()
    stats = index.describe_index_stats()
    namespaces = list(stats.namespaces.keys())

    if not namespaces:
        debug("npc_memory: no memories to clear")
        return

    for ns in namespaces:
        index.delete(delete_all=True, namespace=ns)
        debug(f"npc_memory: cleared namespace '{ns}'")

    debug(f"npc_memory: wiped {len(namespaces)} NPC namespace(s)")


def _upsert_facts(npc_name: str, facts: list[str]) -> None:
    embeddings = _embeddings()
    index = _get_index()
    namespace = _namespace(npc_name)
    vectors = [
        {"id": str(uuid.uuid4()), "values": embeddings.embed_query(f), "metadata": {"npc_name": npc_name, "text": f}}
        for f in facts
    ]
    index.upsert(vectors=vectors, namespace=namespace)
    debug(f"npc_memory: upserted {len(vectors)} vectors to namespace '{namespace}'")


def store_exchange(npc_name: str, player_msg: str, npc_reply: str, llm=None) -> None:
    """Extract and store facts from a single player/NPC exchange immediately after it happens."""
    exchange = f"Player: {player_msg}\n{npc_name}: {npc_reply}"

    response = _get_mini_llm().invoke([
        SystemMessage(content=NPC_MEMORY_EXTRACT_PROMPT),
        HumanMessage(content=exchange)
    ])

    try:
        text = response.content.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        facts = json.loads(text.strip())
        if not isinstance(facts, list) or not facts:
            return
    except Exception as e:
        debug(f"npc_memory: fact extraction failed — {e}")
        return

    debug(f"npc_memory: storing {len(facts)} facts for '{npc_name}': {facts}")
    _upsert_facts(npc_name, facts)


def _hyde_rewrite(query: str, npc_name: str = "") -> str:
    """Rewrite player input as a factual statement for better embedding similarity against stored memories."""
    response = _get_mini_llm().invoke([
        SystemMessage(content=NPC_MEMORY_HYDE_PROMPT.format(npc_name=npc_name or "the NPC")),
        HumanMessage(content=query)
    ])
    rewritten = response.content.strip()
    debug(f"npc_memory: HyDE rewrite: '{query}' → '{rewritten}'")
    return rewritten


def evaluate_fear_delta(player_msg: str) -> int:
    """Ask gpt-4o-mini to rate how threatening the player's message is as an unconstrained integer."""
    response = _get_mini_llm().invoke([
        HumanMessage(content=NPC_FEAR_PROMPT.format(player_msg=player_msg))
    ])
    try:
        return int(response.content.strip())
    except (ValueError, AttributeError):
        debug("npc_memory: fear delta parse failed, defaulting to 0")
        return 0


def evaluate_mood_delta(player_msg: str) -> int:
    """Ask gpt-4o-mini to rate the player's attitude as an unconstrained integer."""
    response = _get_mini_llm().invoke([
        HumanMessage(content=NPC_MOOD_PROMPT.format(player_msg=player_msg))
    ])
    try:
        return int(response.content.strip())
    except (ValueError, AttributeError):
        debug("npc_memory: mood delta parse failed, defaulting to 0")
        return 0


def retrieve_memories(npc_name: str, query: str, k: int = 3, llm=None) -> list[str]:
    embeddings = _embeddings()
    index = _get_index()
    namespace = _namespace(npc_name)

    search_query = _hyde_rewrite(query, npc_name)
    query_vector = embeddings.embed_query(search_query)
    results = index.query(
        vector=query_vector,
        top_k=k,
        namespace=namespace,
        include_metadata=True
    )

    memories = [
        match["metadata"]["text"]
        for match in results["matches"]
        if match["score"] > SIMILARITY_THRESHOLD
    ]

    debug(f"npc_memory: retrieved {len(memories)}/{k} memories for '{npc_name}' (query: '{query[:60]}')")
    return memories
