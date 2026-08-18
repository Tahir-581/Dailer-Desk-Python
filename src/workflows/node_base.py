import logging
from abc import ABC, abstractmethod
from typing import Any, Dict

logger = logging.getLogger("workflow.node")


class WorkflowNode(ABC):

    def __init__(self, node_id: str, node_type: str, config: Dict[str, Any]):

        self.node_id = node_id
        self.node_type = node_type
        self.config = config
        logger.debug(f"Initialized {node_type} node: {node_id}")

    @abstractmethod
    async def execute(self, context: Dict[str, Any]) -> Any:
        pass

    def get_config_value(self, key: str, default: Any = None) -> Any:

        return self.config.get(key, default)
