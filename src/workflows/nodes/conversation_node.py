import logging
from typing import Any, Dict, Optional

from livekit.agents import Agent, ChatContext

from tools import (
    end_call,
    generic_service,
    make_outbound_call,
    search_knowledge_base,
    transfer_call,
)
from tools.workflow_tools import get_workflow_state, transition_to_next_node

from ..node_base import WorkflowNode

logger = logging.getLogger("workflow.node.conversation")


class ConversationNode(WorkflowNode):

    def __init__(self, node_id: str, config: Dict[str, Any]):

        super().__init__(node_id, "conversation", config)

        self.base_instructions = (
            config.get("instructions") or
            config.get("systemPrompt") or
            ""
        )
        self.title = config.get("title", "Conversation")
        self.transitions = config.get("transitions", [])


        self.instructions = self._build_full_instructions()

        logger.info(f"Conversation node '{self.title}' ({node_id}) initialized with {len(self.transitions)} transitions")

    def _build_full_instructions(self, edge_conditions: list = None) -> str:

        instructions_parts = []

        if self.base_instructions:
            instructions_parts.append("## YOUR KNOWLEDGE & INSTRUCTIONS")
            instructions_parts.append(f"{self.base_instructions}")
            instructions_parts.append("\n## INSTRUCTION FOR ANSWERING")
            instructions_parts.append("1. If the user's input matches a navigation condition below, IGNORE these instructions and navigate immediately.")
            instructions_parts.append("3. If you need more information about company policies or details, use the `search_knowledge_base` tool. Before doing so, give a brief filler phrase like 'Let me look that up for you'.")
            instructions_parts.append("4. Do not hallucinate. If the answer is not in your instructions or the knowledge base, say you don't know.")

        conditions_to_use = edge_conditions if edge_conditions else []

        if conditions_to_use:
            instructions_parts.append("\n\n## NAVIGATION RULES (HIGHEST PRIORITY)")
            instructions_parts.append("You are controlling a workflow state machine. Your PRIMARY job is to detect user intent and route them to the correct next node.")
            instructions_parts.append("")
            instructions_parts.append("### HOW TO MATCH USER INPUT TO CONDITIONS:")
            instructions_parts.append("The conditions below describe user INTENTS, not exact phrases. You must semantically match what the user says to these intents.")
            instructions_parts.append("- If the user's message MEANS the same thing as a condition (even if worded differently), it's a match!")
            instructions_parts.append("- You do NOT need an exact word match. Focus on the user's underlying intent.")
            instructions_parts.append("")
            instructions_parts.append("### WHEN YOU DETECT A MATCH:")
            instructions_parts.append("1. IMMEDIATELY call `transition_to_next_node` with the condition text EXACTLY as written below (copy-paste it).")
            instructions_parts.append("2. DO NOT speak or respond with text. ONLY make the tool call.")
            instructions_parts.append("3. Do NOT ask for confirmation. Do NOT explain. Just call the tool.")
            instructions_parts.append("")
            instructions_parts.append("")
            instructions_parts.append("### AVAILABLE CONDITIONS (copy the exact text when calling the tool):")

            for i, condition in enumerate(conditions_to_use, 1):
                if isinstance(condition, str) and condition:
                    instructions_parts.append(f"  {i}. \"{condition}\"")
                elif isinstance(condition, dict):
                    cond_text = condition.get("condition", condition.get("label", ""))
                    if cond_text:
                        instructions_parts.append(f"  {i}. \"{cond_text}\"")










        return "\n".join(instructions_parts) if instructions_parts else "You are a helpful assistant."


    async def execute(self, context: Dict[str, Any]) -> Agent:
        session = context.get("session")
        chat_ctx = context.get("chat_ctx")
        org_id = context.get("org_id")
        workflow_engine = context.get("workflow_engine")

        if not session:
            raise ValueError("Session is required in context for conversation node")

        try:

            edge_conditions = []
            if workflow_engine:
                outgoing_edges = workflow_engine.get_outgoing_edges(self.node_id)
                edge_conditions = [e.condition for e in outgoing_edges if e.condition]
                logger.info(f"Node '{self.node_id}' has {len(edge_conditions)} outgoing edge conditions: {edge_conditions}")


            final_instructions = self._build_full_instructions(edge_conditions if edge_conditions else None)


            if len(final_instructions) > 300:
                logger.info(f"Node '{self.node_id}' instructions preview: {final_instructions[:300]}...")
            else:
                logger.info(f"Node '{self.node_id}' instructions: {final_instructions}")
            logger.debug(f"Full instructions for node '{self.node_id}':\n{final_instructions}")

            is_initial_node = False
            if workflow_engine:
                history = workflow_engine.state.execution_history
                if len(history) <= 1:
                    is_initial_node = True
                    logger.info(f"Node '{self.node_id}' is the INITIAL conversation node (history len: {len(history)})")
                else:
                    logger.info(f"Node '{self.node_id}' is a TRANSITIONAL node (history len: {len(history)})")

            agent = WorkflowAgent(
                instructions=final_instructions or "You are a helpful assistant.",
                base_instructions=self.base_instructions,
                node_id=self.node_id,
                chat_ctx=chat_ctx,
                org_id=org_id,
                is_initial_node=is_initial_node
            )

            logger.info(f"Created conversation agent for node {self.node_id} (agent type: {type(agent).__name__}, initial={is_initial_node})")
            return agent
        except Exception as e:
            logger.error(f"Failed to create WorkflowAgent for node {self.node_id}: {e}", exc_info=True)
            raise ValueError(f"Failed to create agent for conversation node {self.node_id}: {e!s}")


class WorkflowAgent(Agent):


    def __init__(
        self,
        instructions: str,
        base_instructions: str = "",
        node_id: Optional[str] = None,
        chat_ctx: Optional[ChatContext] = None,
        org_id: Optional[str] = None,
        greeting: Optional[str] = None,
        is_initial_node: bool = False
    ):
        super().__init__(
            instructions=instructions,
            tools=[
                transition_to_next_node,
                get_workflow_state,
                end_call,
                transfer_call,
                generic_service,
                make_outbound_call,
                search_knowledge_base
            ],
            chat_ctx=chat_ctx
        )
        self.node_id = node_id
        self.org_id = org_id
        self.greeting = greeting
        self._instructions = instructions
        self._base_instructions = base_instructions
        self._is_initial_node = is_initial_node
        self._rag_service = None  # Lazy initialization
        logger.debug(f"WorkflowAgent initialized for node {node_id} (initial={is_initial_node})")

    async def on_enter(self) -> None:

        if not self._is_initial_node:


            logger.info(f"Node {self.node_id}: Responding to user's question with new node instructions (transitional node)")





            await self.session.generate_reply()
            return


        logger.info(f"Node {self.node_id}: Generating greeting (initial node)")
        if self.greeting:
            await self.session.generate_reply(
                instructions=self.greeting
            )
        elif self._base_instructions:
            await self.session.generate_reply(
                instructions=f"Your role: {self._base_instructions}\n\nGreet the user briefly and naturally."
            )

