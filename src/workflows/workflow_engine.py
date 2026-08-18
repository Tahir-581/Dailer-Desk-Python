

import logging
from dataclasses import asdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Type

from .data_models import EdgeData, NodeData, WorkflowData, WorkflowState
from .node_base import WorkflowNode
from .nodes import (
    AgentTransferNode,
    CallTransferNode,
    ConversationNode,
    EndCallNode,
    ExtractVariableNode,
    LogicSplitNode,
    PressDigitNode,
    SMSNode,
)

logger = logging.getLogger("workflow.engine")


class WorkflowEngine:



    NODE_TYPE_REGISTRY: Dict[str, Type[WorkflowNode]] = {
        "conversation": ConversationNode,
        "welcome": ConversationNode,
        "custom": ConversationNode,
        "call-transfer": CallTransferNode,
        "press-digit": PressDigitNode,
        "end-call": EndCallNode,
        "endCall": EndCallNode,
        "sms": SMSNode,
        "logic-split": LogicSplitNode,
        "extract-variable": ExtractVariableNode,
        "agent-transfer": AgentTransferNode,
    }

    def __init__(
        self,
        workflow: WorkflowData,
        nodes: List[NodeData],
        edges: List[EdgeData]
    ):

        self.workflow = workflow
        self.node_definitions = {n.node_id: n for n in nodes}
        self.edges = edges
        self.node_instances: Dict[str, WorkflowNode] = {}


        self.adjacency: Dict[str, List[EdgeData]] = {}
        for node in nodes:
            self.adjacency[node.node_id] = []
        for edge in edges:
            if edge.source_node_id in self.adjacency:
                self.adjacency[edge.source_node_id].append(edge)


        for node_id in self.adjacency:
            self.adjacency[node_id].sort(
                key=lambda e: e.priority if e.priority is not None else 999
            )


        self.state = WorkflowState(
            workflow_id=workflow.id,
            current_node_id=self._find_start_node(),
            execution_history=[],
            collected_data={}
        )

        logger.info(f"WorkflowEngine initialized for workflow: {workflow.name}")
        logger.debug(f"Loaded {len(nodes)} nodes and {len(edges)} edges")

    def _find_start_node(self) -> Optional[str]:
        """
        Find the ID of the starting node for this workflow.
        Prefers a node explicitly marked as start, otherwise falls back
        to the first node in the workflow definition if available.
        """
        # Try to find a node explicitly marked as start
        for node in self.node_definitions.values():
            try:
                if getattr(node.config, "is_start", False):
                    return node.node_id
            except AttributeError:
                # Node has no config or is_start flag
                continue

        # If there is a node named "start" use it
        for node in self.node_definitions.values():
            if node.type == "start" or node.node_id == "start":
                return node.node_id

        # Fallback to the first node if nothing else is marked
        if self.node_definitions:
            # Deterministic order for reproducibility
            first_id = sorted(self.node_definitions.keys())[0]
            return first_id

        logger.warning("No nodes available to determine start node")
        return None

    def get_outgoing_edges(self, node_id: str) -> List[EdgeData]:
        """
        Get all outgoing edges for the given node, already sorted by priority.
        """
        return self.adjacency.get(node_id, [])

    def find_next_node(self, condition: Optional[str] = None) -> Optional[str]:
        """
        Find the ID of the next node based on the current node and an optional condition.
        If multiple edges exist, chooses the first matching the condition; if no
        condition is provided, the first edge (by priority) is used.
        """
        current_id = self.state.current_node_id
        if not current_id:
            logger.warning("No current node set in workflow state")
            return None

        outgoing = self.get_outgoing_edges(current_id)
        if not outgoing:
            logger.info(f"No outgoing edges from node '{current_id}'")
            return None

        if condition is None:
            # Default to first edge
            return outgoing[0].target_node_id

        # Try to find edge whose condition matches the provided condition
        for edge in outgoing:
            if edge.condition is None:
                continue
            if str(edge.condition) == str(condition):
                return edge.target_node_id

        # Fallback: if nothing matches, use first edge
        logger.info(
            f"No edge condition matched '{condition}' from node '{current_id}', "
            "falling back to first edge."
        )
        return outgoing[0].target_node_id

    def transition_to(self, node_id: str, reason: Optional[str] = None) -> bool:
        """
        Transition the workflow to a specific node and record the transition
        in the execution history.
        """
        if node_id not in self.node_definitions:
            logger.error(f"Cannot transition to unknown node_id: {node_id}")
            return False

        previous_id = self.state.current_node_id
        self.state.current_node_id = node_id
        self.state.execution_history.append(
            {
                "from": previous_id,
                "to": node_id,
                "reason": reason,
                "timestamp": datetime.utcnow().isoformat(),
            }
        )

        logger.info(f"Workflow transition: {previous_id} -> {node_id} (reason={reason})")
        return True

    def _instantiate_node(self, node_id: str) -> Optional[WorkflowNode]:
        """
        Instantiate a node class from its definition.
        """
        node_def = self.node_definitions.get(node_id)
        if not node_def:
            logger.error(f"Node definition not found for id: {node_id}")
            return None

        node_cls = self.NODE_TYPE_REGISTRY.get(node_def.type)
        if not node_cls:
            logger.error(f"Unsupported node type '{node_def.type}' for node '{node_id}'")
            return None

        try:
            instance = node_cls(node_def)
            self.node_instances[node_id] = instance
            return instance
        except Exception as e:
            logger.error(f"Failed to instantiate node '{node_id}' of type '{node_def.type}': {e}", exc_info=True)
            return None

    def get_current_node(self) -> Optional[NodeData]:
        """
        Get the NodeData definition for the current node.
        """
        if not self.state.current_node_id:
            return None
        return self.node_definitions.get(self.state.current_node_id)

    async def execute_current_node(self, context: Dict[str, Any]) -> Any:
        """
        Execute the current node and return its result.
        """
        if not self.state.current_node_id:
            logger.warning("Cannot execute node: no current_node_id set")
            return None

        node_id = self.state.current_node_id
        node = self.node_instances.get(node_id)
        if not node:
            node = self._instantiate_node(node_id)
        if not node:
            logger.error(f"Unable to execute node '{node_id}': instantiation failed")
            return None

        logger.info(f"Executing workflow node '{node_id}' of type '{node.node_def.type}'")
        result = await node.execute(context)
        return result

    def get_state(self) -> Dict[str, Any]:
        """
        Return a serializable view of the current workflow state.
        """
        return asdict(self.state)
