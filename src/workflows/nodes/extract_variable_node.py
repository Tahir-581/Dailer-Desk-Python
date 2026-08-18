

import logging
from typing import Any, Dict, List, Optional

from livekit.agents import AgentTask, ChatContext

logger = logging.getLogger("workflow.node.extract_variable")


class ExtractVariableTask(AgentTask[Dict[str, Any]]):


    def __init__(
        self,
        variables: List[Dict[str, str]],
        extra_instructions: str = "",
        chat_ctx: Optional[ChatContext] = None,
        last_user_content: Optional[str] = None
    ):
        self.variables_data = variables
        self.last_user_content = last_user_content


        var_desc_list = []
        self.variable_names = []

        for v in variables:
            name = v["name"]
            self.variable_names.append(name)
            desc = v.get("description", "")
            if desc:
                var_desc_list.append(f"- {name}: {desc}")
            else:
                var_desc_list.append(f"- {name}")

        formatted_vars = "\n".join(var_desc_list)

        super().__init__(
            chat_ctx=chat_ctx,
        )

        self.extracted_data: Dict[str, Any] = {}

    async def on_enter(self) -> None:
        pass

    @staticmethod
    def _normalize_variables(raw_vars: List) -> List[Dict[str, str]]:
        normalized = []
        for v in raw_vars:
            if isinstance(v, str):
                normalized.append({"name": v, "description": ""})
            elif isinstance(v, dict):
                normalized.append({
                    "name": v.get("name", "unknown"),
                    "description": v.get("description", "")
                })
        return normalized

    async def execute(self, context: Dict[str, Any]) -> Dict[str, Any]:

        session = context.get("session")
        chat_ctx = context.get("chat_ctx")

        if not session:
            raise ValueError("Session is required for extract variable node")

        try:

            last_user_content = None
            if chat_ctx and chat_ctx.messages:
                for msg in reversed(chat_ctx.messages):
                    if msg.role == "user":
                        content = msg.content
                        if isinstance(content, list):
                            content = " ".join(str(c) for c in content)
                        last_user_content = content
                        logger.info(f"ExtractVariableNode found last user intent: {last_user_content}")
                        break

            if not last_user_content:
                logger.warning("ExtractVariableNode could not find a previous user message to analyze")


            task = ExtractVariableTask(
                variables=self.variables,
                extra_instructions=self.instructions,
                chat_ctx=chat_ctx.copy(
                    exclude_instructions=True,
                    exclude_function_call=True
                ) if chat_ctx else None,
                last_user_content=last_user_content
            )


            result = await task

            if result:

                collected_data = context.get("collected_data", {})
                collected_data.update(result)
                context["collected_data"] = collected_data


                if "results" not in context:
                    context["results"] = {}
                context["results"].update(result)

                logger.info(
                    f"Extract variable node {self.node_id}: "
                    f"extracted {len(result)} variables: {list(result.keys())}"
                )

                return result
            else:
                logger.warning(
                    f"Extract variable node {self.node_id}: no data extracted"
                )
                return {}

        except Exception as e:
            logger.error(
                f"Error in extract variable node {self.node_id}: {e}",
                exc_info=True
            )
            raise ValueError(f"Failed to extract variables: {e!s}")
