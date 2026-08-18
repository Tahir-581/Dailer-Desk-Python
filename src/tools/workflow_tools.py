import logging
from typing import Any, Dict, Optional, Tuple, Union

from livekit.agents import Agent, ToolError
from livekit.agents.llm import function_tool
from livekit.agents.voice import RunContext

logger = logging.getLogger("workflow.tools")


@function_tool()
async def transition_to_next_node(
    context: RunContext,
    condition: Optional[str] = None
) -> Union[Agent, Tuple[Agent, str], str]:

    try:
        workflow_engine = context.session.userdata.get("workflow_engine")

        if not workflow_engine:
            raise ToolError("No workflow engine found. This tool only works in workflow mode.")

        current_node_id = workflow_engine.state.current_node_id
        if not current_node_id:
            raise ToolError("No current node in workflow. Cannot transition.")

        outgoing_edges = workflow_engine.get_outgoing_edges(current_node_id)
        if len(outgoing_edges) > 1 and not condition:
            available_conditions = [e.condition for e in outgoing_edges if e.condition]
            logger.warning(f"Multiple paths available from '{current_node_id}' but no condition provided. Available conditions: {available_conditions}. Using default (first) path.")


        next_node_id = workflow_engine.find_next_node(condition)

        if not next_node_id:
            return "No next node available. Workflow may be complete or at a terminal node."


        success = workflow_engine.transition_to(next_node_id, reason=f"tool_transition_{condition or 'default'}")

        if not success:
            raise ToolError(f"Failed to transition to node: {next_node_id}")




        chat_ctx = None
        try:
            chat_ctx = context.session.history
            logger.debug("Got chat context from session.history")

        except (AttributeError, RuntimeError) as e:
            logger.warning(f"Could not access session.history: {e}")

            try:
                if hasattr(context.session, 'chat_ctx'):
                    chat_ctx = context.session.chat_ctx
                    logger.debug("Got chat context from session.chat_ctx (fallback)")
            except AttributeError:
                pass

        if chat_ctx is None:
            try:
                current_agent = context.session.current_agent
                if hasattr(current_agent, 'chat_ctx') and current_agent.chat_ctx is not None:
                    chat_ctx = current_agent.chat_ctx
                    logger.debug("Got chat context from session.current_agent.chat_ctx")
            except (AttributeError, RuntimeError, TypeError) as e:
                logger.debug(f"Could not access current_agent.chat_ctx: {e}")


        if chat_ctx is None:
            error_msg = "CRITICAL: Could not access conversation history from session! Cannot preserve context during workflow transition."
            logger.error(error_msg)
            raise ToolError(error_msg)


        room = None
        if hasattr(context.session, 'room'):
            room = context.session.room

        workflow_context = {
            "session": context.session,
            "chat_ctx": chat_ctx,
            "room": room,
            "org_id": context.session.userdata.get("org_id"),
            "collected_data": workflow_engine.state.collected_data
        }

        while True:
            node_result = await workflow_engine.execute_current_node(workflow_context)

            next_node_def = workflow_engine.get_current_node()
            node_type = next_node_def.type if next_node_def else "unknown"

            if node_type in ["end-call", "call-transfer"]:
                logger.info(f"Reached terminal node: {node_type}. Workflow execution complete.")
                return f"Workflow completed at terminal node: {node_type}"

            if isinstance(node_result, Agent):
                logger.info(f"Workflow handoff: {current_node_id} -> {next_node_id} (type: {node_type})")




                return node_result



            condition = str(node_result) if node_result is not None else None
            next_node_id = workflow_engine.find_next_node(condition=condition)

            if not next_node_id:
                return f"No further nodes available after intermediate node {node_type}."

            workflow_engine.transition_to(next_node_id, reason=f"auto_advance_with_result_{condition}")


    except ToolError:
        raise
    except Exception as e:
        logger.error(f"Error in transition_to_next_node: {e}", exc_info=True)
        raise ToolError(f"Failed to transition workflow: {e!s}")


@function_tool()
async def get_workflow_state(context: RunContext) -> Dict[str, Any]:

    try:
        workflow_engine = context.session.userdata.get("workflow_engine")

        if not workflow_engine:
            raise ToolError("No workflow engine found. This tool only works in workflow mode.")

        state = workflow_engine.get_state()
        current_node = workflow_engine.get_current_node()

        return {
            "current_node_id": state.get("current_node_id"),
            "current_node_type": current_node.type if current_node else None,
            "workflow_id": state.get("workflow_id"),
            "execution_history_count": len(state.get("execution_history", [])),
            "collected_data_keys": list(state.get("collected_data", {}).keys()),
            "has_next_node": len(workflow_engine.get_outgoing_edges(state.get("current_node_id"))) > 0
        }

    except ToolError:
        raise
    except Exception as e:
        logger.error(f"Error in get_workflow_state: {e}", exc_info=True)
        raise ToolError(f"Failed to get workflow state: {e!s}")


