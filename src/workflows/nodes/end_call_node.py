import logging
from typing import Any, Dict

from ..node_base import WorkflowNode

logger = logging.getLogger("workflow.node.end_call")


class EndCallNode(WorkflowNode):

    def __init__(self, node_id: str, config: Dict[str, Any]):
        super().__init__(node_id, "end-call", config)

        self.farewell_message = config.get("farewell_message", "Thank you for calling!")
        logger.info(f"End call node {node_id} initialized")

    async def execute(self, context: Dict[str, Any]) -> None:
        session = context.get("session")

        if not session:
            raise ValueError("Session is required in context for end call node")


        if self.farewell_message:
            try:
                farewell_handle = await session.say(self.farewell_message, allow_interruptions=False)
                await farewell_handle
                logger.debug("Farewell message completed")
            except Exception as e:
                logger.warning(f"Could not say farewell message: {e}")

        try:
            logger.info(f"Shutting down session for end-call node {self.node_id}")
            session.shutdown(drain=True)
            logger.info(f"Call ended successfully for node {self.node_id}")
        except Exception as e:
            logger.error(f"Error ending call for node {self.node_id}: {e}", exc_info=True)
            raise

