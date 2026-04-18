import os
import json
import uuid
from pinecone import Pinecone, ServerlessSpec
from langchain_openai import OpenAIEmbeddings
from langchain_core.messages import HumanMessage, SystemMessage
from utils import debug

INDEX_NAME = "fungame-npc-memory"
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIMS = 1536
SIMILARITY_THRESHOLD = 0.3

_index = None

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

def store_memory(npc_name: str, conversation: list[str], llm) -> None:
    if len(conversation) < 2:
        debug(f"npc_memory: skipping store for '{npc_name}' — too short")
        return

    conv_text = "\n".join(conversation)

    response = llm.invoke([
        SystemMessage(content=(
            "Extract 2-4 key facts from this conversation about what the player revealed, asked, or did. "
            "Return ONLY a JSON array of short factual sentences. "
            'Example: ["Player found the secret letter", "Player is searching for the basement"]'
        )),
        HumanMessage(content=f"Conversation:\n{conv_text}")
    ])

    try:
        text = response.content.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        facts = json.loads(text.strip())
        if not isinstance(facts, list):
            raise ValueError("Expected a list")
    except Exception as e:
        debug(f"npc_memory: fact extraction failed for '{npc_name}' — {e}")
        return

    if not facts:
        debug(f"npc_memory: no facts extracted for '{npc_name}'")
        return

    debug(f"npc_memory: storing {len(facts)} facts for '{npc_name}': {facts}")

    embeddings = _embeddings()
    index = _get_index()
    namespace = _namespace(npc_name)

    vectors = []
    for fact in facts:
        vector = embeddings.embed_query(fact)
        vectors.append({
            "id": str(uuid.uuid4()),
            "values": vector,
            "metadata": {"npc_name": npc_name, "text": fact}
        })

    index.upsert(vectors=vectors, namespace=namespace)
    debug(f"npc_memory: upserted {len(vectors)} vectors to namespace '{namespace}'")


def retrieve_memories(npc_name: str, query: str, k: int = 3) -> list[str]:
    embeddings = _embeddings()
    index = _get_index()
    namespace = _namespace(npc_name)

    query_vector = embeddings.embed_query(query)
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
