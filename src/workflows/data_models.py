from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class WorkflowData:
    id: str
    name: str
    assistant_id: str
    enabled: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class NodeData:
    node_id: str
    workflow_id: str
    type: str
    config: Dict[str, Any]
    created_at: Optional[str] = None


@dataclass
class EdgeData:
    source_node_id: str
    target_node_id: str
    workflow_id: str
    condition: Optional[str] = None
    priority: Optional[int] = None
    created_at: Optional[str] = None


@dataclass
class WorkflowState:
    workflow_id: str
    current_node_id: Optional[str] = None
    execution_history: List[Dict[str, Any]] = None
    collected_data: Dict[str, Any] = None

    def __post_init__(self):

        if self.execution_history is None:
            self.execution_history = []
        if self.collected_data is None:
            self.collected_data = {}
