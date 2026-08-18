import logging
from typing import Any, Dict, Optional

from livekit.agents.beta.workflows.dtmf_inputs import GetDtmfTask

from ..node_base import WorkflowNode

logger = logging.getLogger("workflow.node.press_digit")


class PressDigitNode(WorkflowNode):

    def __init__(self, node_id: str, config: Dict[str, Any]):
        super().__init__(node_id, "press-digit", config)


        self.num_digits = int(config.get("numDigits", 1))
        self.ask_for_confirmation = config.get("askForConfirmation", False)
        self.dtmf_input_timeout = float(config.get("timeout", 10))
        self.dtmf_stop_event = config.get("stopEvent") or None
        self.variable_name = config.get("variableName", "collected_digits")

        instructions = config.get("instructions", "")
        prompt = config.get("prompt", "")
        if prompt and instructions:
            self.extra_instructions = f"{prompt}. {instructions}"
        else:
            self.extra_instructions = prompt or instructions

        logger.info(
            f"Press-digit node {node_id} initialized: "
            f"num_digits={self.num_digits}, "
            f"ask_for_confirmation={self.ask_for_confirmation}, "
            f"timeout={self.dtmf_input_timeout}s, "
            f"variable={self.variable_name}"
        )

    async def execute(self, context: Dict[str, Any]) -> Optional[str]:
        session = context.get("session")

        if not session:
            raise ValueError("Session is required in context for press-digit node")

        chat_ctx = context.get("chat_ctx")
        if not chat_ctx:
            if hasattr(session, "chat_ctx") and session.chat_ctx:
                chat_ctx = session.chat_ctx
            else:

                logger.warning("chat_ctx not found in context, some features might not work")

        try:

            result = await GetDtmfTask(
                num_digits=self.num_digits,
                ask_for_confirmation=self.ask_for_confirmation,
                dtmf_input_timeout=self.dtmf_input_timeout,
                dtmf_stop_event=self.dtmf_stop_event,
                extra_instructions=self.extra_instructions if self.extra_instructions else None,
                chat_ctx=chat_ctx
            )

            if result and hasattr(result, "user_input"):
                collected_digits = result.user_input
                logger.info(f"Collected digits from node {self.node_id}: {collected_digits}")


                if "results" not in context:
                    context["results"] = {}
                context["results"][self.variable_name] = collected_digits


                context[self.variable_name] = collected_digits

                return collected_digits
            else:
                logger.warning(f"GetDtmfTask returned None for node {self.node_id}")
                return None

        except Exception as e:
            logger.error(f"Error executing GetDtmfTask for node {self.node_id}: {e}", exc_info=True)
            raise ValueError(f"Failed to collect digits: {e!s}")
