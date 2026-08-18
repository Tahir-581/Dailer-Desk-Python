

import logging
import re
from typing import Any, Dict

from ..node_base import WorkflowNode

logger = logging.getLogger("workflow.node.logic_split")


class LogicSplitNode(WorkflowNode):



    OPERATORS = {
        "equals": lambda a, b: str(a).lower() == str(b).lower(),
        "not_equals": lambda a, b: str(a).lower() != str(b).lower(),
        "contains": lambda a, b: str(b).lower() in str(a).lower(),
        "not_contains": lambda a, b: str(b).lower() not in str(a).lower(),
        "starts_with": lambda a, b: str(a).lower().startswith(str(b).lower()),
        "ends_with": lambda a, b: str(a).lower().endswith(str(b).lower()),
        "greater_than": lambda a, b: _safe_float(a) > _safe_float(b),
        "less_than": lambda a, b: _safe_float(a) < _safe_float(b),
        "greater_than_or_equal": lambda a, b: _safe_float(a) >= _safe_float(b),
        "less_than_or_equal": lambda a, b: _safe_float(a) <= _safe_float(b),
        "is_empty": lambda a, _: not a or str(a).strip() == "",
        "is_not_empty": lambda a, _: a and str(a).strip() != "",
        "matches_regex": lambda a, b: bool(re.search(b, str(a), re.IGNORECASE)),
    }

    def __init__(self, node_id: str, config: Dict[str, Any]):
        super().__init__(node_id, "logic-split", config)

        self.conditions = config.get("conditions", [])
        self.default_transition = config.get("defaultTransition") or config.get("default_transition")
        self.title = config.get("title", "Logic Split")


        self.transitions = config.get("transitions", [])

        logger.info(
            f"Logic split node {node_id} initialized: "
            f"conditions={len(self.conditions)}, transitions={len(self.transitions)}"
        )

    async def execute(self, context: Dict[str, Any]) -> str:

        collected_data = context.get("collected_data", {})
        workflow_state = context.get("workflow_state")


        eval_context = {
            **collected_data,
            **(context.get("results", {})),
        }


        for key, value in context.items():
            if isinstance(value, (str, int, float, bool)) and key not in eval_context:
                eval_context[key] = value

        logger.debug(f"Logic split evaluating with context keys: {list(eval_context.keys())}")


        for condition in self.conditions:
            variable_name = condition.get("variable")
            operator = condition.get("operator", "equals")
            compare_value = condition.get("value")
            transition = condition.get("transition")

            if not variable_name or not transition:
                continue


            actual_value = eval_context.get(variable_name)


            if self._evaluate_condition(actual_value, operator, compare_value):
                logger.info(
                    f"Logic split node {self.node_id}: condition matched - "
                    f"{variable_name} {operator} {compare_value} -> {transition}"
                )
                return transition


        if self.transitions and not self.conditions:
            logger.info(
                f"Logic split node {self.node_id}: no conditions, using first transition"
            )
            return self.transitions[0] if self.transitions else self.default_transition


        if self.default_transition:
            logger.info(
                f"Logic split node {self.node_id}: no conditions matched, using default"
            )
            return self.default_transition

        logger.warning(
            f"Logic split node {self.node_id}: no conditions matched and no default set"
        )
        return None

    def _evaluate_condition(
        self,
        actual_value: Any,
        operator: str,
        compare_value: Any
    ) -> bool:
        if operator not in self.OPERATORS:
            logger.warning(f"Unknown operator: {operator}")
            return False
        return self.OPERATORS[operator](actual_value, compare_value)


def _safe_float(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0
