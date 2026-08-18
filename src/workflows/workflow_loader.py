import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .data_models import EdgeData, NodeData, WorkflowData

try:
    from .workflow_engine import WorkflowEngine
    _ENGINE_AVAILABLE = True
except ImportError:
    WorkflowEngine = None
    _ENGINE_AVAILABLE = False

logger = logging.getLogger("workflow.loader")


def load_workflow_from_json(file_path: Union[str, Path]) -> Dict[str, Any]:
    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(f"Workflow file not found: {file_path}")

    with open(file_path, encoding='utf-8') as f:
        data = json.load(f)

    required_keys = ["workflow", "nodes", "edges"]
    for key in required_keys:
        if key not in data:
            raise ValueError(f"Missing required key in workflow JSON: {key}")

    logger.info(f"Loaded workflow from {file_path}: {data['workflow'].get('name', 'Unknown')}")
    return data


def save_workflow_to_json(
    workflow: WorkflowData,
    nodes: List[NodeData],
    edges: List[EdgeData],
    file_path: Union[str, Path]
) -> None:
    from dataclasses import asdict

    file_path = Path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "workflow": asdict(workflow),
        "nodes": [asdict(n) for n in nodes],
        "edges": [asdict(e) for e in edges]
    }

    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)

    logger.info(f"Saved workflow to {file_path}")


def create_engine_from_json(file_path: Union[str, Path]) -> Optional["WorkflowEngine"]:
    if not _ENGINE_AVAILABLE:
        logger.error("WorkflowEngine not available - LiveKit dependencies may be missing")
        return None

    data = load_workflow_from_json(file_path)

    workflow = WorkflowData(**data["workflow"])
    nodes = [NodeData(**n) for n in data["nodes"]]
    edges = [EdgeData(**e) for e in data["edges"]]

    return WorkflowEngine(workflow, nodes, edges)


def create_engine_from_api(workflow_id: str) -> Optional["WorkflowEngine"]:
    if not _ENGINE_AVAILABLE:
        logger.error("WorkflowEngine not available - LiveKit dependencies may be missing")
        return None

    import os

    import httpx

    api_base_url = (
        os.getenv("NEXTJS_API_URL")
        or os.getenv("NEXT_PUBLIC_API_URL")
        or "http://localhost:3000"
    ).rstrip("/")
    api_url = f"{api_base_url}/api/workflows/{workflow_id}"

    try:
        logger.info(f"Fetching workflow from API: {api_url}")

        with httpx.Client(timeout=10.0) as client:
            response = client.get(api_url)
            response.raise_for_status()
            data = response.json()

        logger.info(
            "Successfully fetched workflow data for: %s",
            data.get("workflow", {}).get("name", "Unknown"),
        )

        workflow_data = data.get("workflow")
        nodes_data = data.get("nodes", [])
        edges_data = data.get("edges", [])

        if not workflow_data:
            logger.error("No workflow data in API response")
            return None

        workflow = WorkflowData(**workflow_data)
        nodes = [NodeData(**n) for n in nodes_data]
        edges = [EdgeData(**e) for e in edges_data]

        logger.info(
            "Loaded workflow '%s' with %s nodes and %s edges",
            workflow.name,
            len(nodes),
            len(edges),
        )

        return WorkflowEngine(workflow, nodes, edges)

    except httpx.HTTPError as e:
        logger.error(f"HTTP error while fetching workflow {workflow_id}: {e}")
        return None
    except Exception as e:
        logger.error(f"Error loading workflow {workflow_id} from API: {e}", exc_info=True)
        return None


def load_workflow_from_api_response(response_data: Dict[str, Any]) -> Dict[str, Any]:
    if "workflow" in response_data:
        return response_data

    workflow_data = {
        "id": response_data.get("id") or response_data.get("workflow_id"),
        "name": response_data.get("name"),
        "assistant_id": response_data.get("assistant_id"),
        "enabled": response_data.get("enabled", True),
        "created_at": response_data.get("created_at"),
        "updated_at": response_data.get("updated_at")
    }

    return {
        "workflow": workflow_data,
        "nodes": response_data.get("nodes", []),
        "edges": response_data.get("edges", [])
    }
