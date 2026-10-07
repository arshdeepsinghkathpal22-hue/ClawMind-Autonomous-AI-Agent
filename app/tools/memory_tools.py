from app import memory
from app.tools.registry import Permission, ToolError, tool


def _describe_forget(args):
    row = memory.get_memory(args.get("memory_id", 0))
    if row:
        return f"delete the memory “{row['content']}”"
    return f"delete memory #{args.get('memory_id')}"


@tool(
    name="remember",
    description="Save a fact to long-term memory. ONLY use this when the user explicitly asks you to "
                "remember something. Never store passwords, API keys, tokens or card numbers.",
    parameters={
        "type": "object",
        "properties": {"fact": {"type": "string", "description": "The fact, e.g. 'My main project is ClawMind.'"}},
        "required": ["fact"],
    },
    permission=Permission.WRITE,
    activity="Saving to memory...",
)
def remember(fact):
    try:
        saved = memory.add_memory(fact)
    except memory.MemoryRejected as exc:
        raise ToolError(str(exc)) from None
    return {"success": True, "saved": saved}


@tool(
    name="search_memories",
    description="Search the user's saved memories for facts related to a query.",
    parameters={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
    permission=Permission.READ,
    activity="Checking memory...",
)
def search_memories(query):
    return {"success": True, "memories": memory.search_memories(query, limit=10)}


@tool(
    name="list_memories",
    description="List everything saved in long-term memory, with ids.",
    parameters={"type": "object", "properties": {}},
    permission=Permission.READ,
    activity="Checking memory...",
)
def list_memories():
    return {"success": True, "memories": memory.list_memories(limit=100)}


@tool(
    name="forget",
    description="Delete one memory by id (find the id with search_memories or list_memories). "
                "The user is asked to confirm.",
    parameters={
        "type": "object",
        "properties": {"memory_id": {"type": "integer"}},
        "required": ["memory_id"],
    },
    permission=Permission.DANGEROUS,
    activity="Deleting memory...",
    describe=_describe_forget,
)
def forget(memory_id):
    if not memory.delete_memory(memory_id):
        raise ToolError(f"There is no memory with id {memory_id}.")
    return {"success": True, "deleted": memory_id}
