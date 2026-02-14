"""ReMe retriever that builds messages with meta memories."""

from typing import List

from ..base_memory_agent import BaseMemoryAgent
from ...core.context import C
from ...core.enumeration import Role
from ...core.schema import Message
from ...core.utils import get_now_time, format_messages


@C.register_op()
class ReMeRetriever(BaseMemoryAgent):
    """Memory agent that retrieves and builds messages with meta memory context."""

    def __init__(self, enable_tool_memory: bool = True, **kwargs):
        """Initialize retriever with tool memory option."""
        super().__init__(**kwargs)
        self.enable_tool_memory = enable_tool_memory

    async def _read_meta_memories(self) -> str:
        """Read and return meta memories as string."""
        from ...mem_tool import ReadMetaMemory

        op = ReadMetaMemory(enable_tool_memory=self.enable_tool_memory, enable_identity_memory=False)
        await op.call()
        return str(op.output)

    async def build_messages(self) -> List[Message]:
        """Build messages with system prompt and user message."""
        system_prompt = self.prompt_format(
            prompt_name="system_prompt",
            now_time=get_now_time(),
            meta_memory_info=await self._read_meta_memories(),
            context=format_messages(self.get_messages()),
        )

        messages = [
            Message(role=Role.SYSTEM, content=system_prompt),
            Message(role=Role.USER, content=self.get_prompt("user_message")),
        ]
        return messages
