

import logging
from typing import Optional

from livekit.agents import Agent, ChatContext

logger = logging.getLogger("workflow.node.agent_transfer")


class TransferredAgent(Agent):


    def __init__(
        self,
        instructions: str,
        name: str = "Transferred Agent",
        greeting: Optional[str] = None,
        chat_ctx: Optional[ChatContext] = None,
        org_id: Optional[str] = None,
        tools: Optional[list] = None
    ):
        super().__init__(
            instructions=instructions,
            tools=tools or [],
            chat_ctx=chat_ctx
        )
        self.agent_name = name
        self.greeting = greeting
        self.org_id = org_id

        logger.debug(f"TransferredAgent '{name}' initialized")

    async def on_enter(self) -> None:
        pass
