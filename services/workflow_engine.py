"""
Workflow Engine Framework for Capulse
Base classes for defining and executing multi-step AI pipelines
with node execution, state management, and audit trails.
"""

import logging
import uuid
import time
from enum import Enum
from typing import Any, Callable, Dict, List, Optional
from datetime import datetime, timezone
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


class NodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class PipelineStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class AuditEntry:
    timestamp: str
    node_name: str
    event: str
    details: Optional[Dict[str, Any]] = None
    duration_ms: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "timestamp": self.timestamp,
            "node_name": self.node_name,
            "event": self.event,
        }
        if self.details is not None:
            result["details"] = self.details
        if self.duration_ms is not None:
            result["duration_ms"] = self.duration_ms
        return result


class WorkflowState:
    def __init__(self, initial_data: Optional[Dict[str, Any]] = None):
        self._data: Dict[str, Any] = initial_data.copy() if initial_data else {}
        self._audit_trail: List[AuditEntry] = []
        self._node_results: Dict[str, Dict[str, Any]] = {}
        self._node_statuses: Dict[str, NodeStatus] = {}
        self._errors: Dict[str, str] = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def update(self, data: Dict[str, Any]) -> None:
        self._data.update(data)

    @property
    def data(self) -> Dict[str, Any]:
        return self._data.copy()

    @property
    def audit_trail(self) -> List[Dict[str, Any]]:
        return [entry.to_dict() for entry in self._audit_trail]

    def add_audit(self, node_name: str, event: str, details: Optional[Dict[str, Any]] = None, duration_ms: Optional[float] = None) -> None:
        entry = AuditEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            node_name=node_name,
            event=event,
            details=details,
            duration_ms=duration_ms,
        )
        self._audit_trail.append(entry)

    def set_node_status(self, node_name: str, status: NodeStatus) -> None:
        self._node_statuses[node_name] = status

    def get_node_status(self, node_name: str) -> NodeStatus:
        return self._node_statuses.get(node_name, NodeStatus.PENDING)

    def set_node_result(self, node_name: str, result: Dict[str, Any]) -> None:
        self._node_results[node_name] = result

    def get_node_result(self, node_name: str) -> Optional[Dict[str, Any]]:
        return self._node_results.get(node_name)

    def set_error(self, node_name: str, error: str) -> None:
        self._errors[node_name] = error

    @property
    def errors(self) -> Dict[str, str]:
        return self._errors.copy()

    @property
    def node_statuses(self) -> Dict[str, str]:
        return {name: status.value for name, status in self._node_statuses.items()}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "data": self._data,
            "node_statuses": self.node_statuses,
            "node_results": self._node_results,
            "errors": self._errors,
            "audit_trail": self.audit_trail,
        }


class WorkflowNode:
    def __init__(
        self,
        name: str,
        execute_fn: Callable[[WorkflowState], Dict[str, Any]],
        description: str = "",
        retry_count: int = 0,
        timeout_seconds: Optional[float] = None,
        condition_fn: Optional[Callable[[WorkflowState], bool]] = None,
    ):
        self.name = name
        self.execute_fn = execute_fn
        self.description = description
        self.retry_count = retry_count
        self.timeout_seconds = timeout_seconds
        self.condition_fn = condition_fn

    def should_execute(self, state: WorkflowState) -> bool:
        if self.condition_fn is not None:
            return self.condition_fn(state)
        return True

    def execute(self, state: WorkflowState) -> Dict[str, Any]:
        if not self.should_execute(state):
            logger.info(f"Node '{self.name}' skipped (condition not met)")
            state.set_node_status(self.name, NodeStatus.SKIPPED)
            state.add_audit(self.name, "skipped", {"reason": "condition not met"})
            return {}

        state.set_node_status(self.name, NodeStatus.RUNNING)
        state.add_audit(self.name, "started")

        last_error = None
        attempts = self.retry_count + 1

        for attempt in range(1, attempts + 1):
            start_time = time.time()
            try:
                result = self.execute_fn(state)
                duration_ms = (time.time() - start_time) * 1000

                state.set_node_status(self.name, NodeStatus.COMPLETED)
                state.set_node_result(self.name, result)
                if result:
                    state.update(result)
                state.add_audit(self.name, "completed", {"attempt": attempt}, duration_ms=duration_ms)

                logger.info(f"Node '{self.name}' completed in {duration_ms:.1f}ms")
                return result

            except Exception as e:
                duration_ms = (time.time() - start_time) * 1000
                last_error = str(e)
                logger.warning(f"Node '{self.name}' attempt {attempt}/{attempts} failed: {e}")
                state.add_audit(
                    self.name,
                    "retry" if attempt < attempts else "failed",
                    {"attempt": attempt, "error": last_error},
                    duration_ms=duration_ms,
                )

        state.set_node_status(self.name, NodeStatus.FAILED)
        state.set_error(self.name, last_error or "Unknown error")
        state.add_audit(self.name, "failed", {"error": last_error})
        raise RuntimeError(f"Node '{self.name}' failed after {attempts} attempts: {last_error}")


class WorkflowPipeline:
    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self.pipeline_id = str(uuid.uuid4())
        self._nodes: List[WorkflowNode] = []
        self._conditional_edges: Dict[str, Callable[[WorkflowState], Optional[str]]] = {}
        self._status = PipelineStatus.PENDING
        self._start_time: Optional[float] = None
        self._end_time: Optional[float] = None

    def add_node(self, node: WorkflowNode) -> "WorkflowPipeline":
        self._nodes.append(node)
        return self

    def add_conditional_edge(self, after_node: str, router_fn: Callable[[WorkflowState], Optional[str]]) -> "WorkflowPipeline":
        self._conditional_edges[after_node] = router_fn
        return self

    @property
    def nodes(self) -> List[WorkflowNode]:
        return list(self._nodes)

    @property
    def status(self) -> PipelineStatus:
        return self._status

    @property
    def duration_ms(self) -> Optional[float]:
        if self._start_time is not None and self._end_time is not None:
            return (self._end_time - self._start_time) * 1000
        return None

    def _find_node(self, name: str) -> Optional[WorkflowNode]:
        for node in self._nodes:
            if node.name == name:
                return node
        return None

    def execute(self, initial_data: Optional[Dict[str, Any]] = None, stop_on_failure: bool = True) -> WorkflowState:
        state = WorkflowState(initial_data)
        self._status = PipelineStatus.RUNNING
        self._start_time = time.time()

        state.add_audit("pipeline", "started", {"pipeline_name": self.name, "pipeline_id": self.pipeline_id, "total_nodes": len(self._nodes)})
        logger.info(f"Pipeline '{self.name}' ({self.pipeline_id}) starting with {len(self._nodes)} nodes")

        idx = 0
        while idx < len(self._nodes):
            node = self._nodes[idx]
            try:
                node.execute(state)
            except RuntimeError:
                if stop_on_failure:
                    self._status = PipelineStatus.FAILED
                    self._end_time = time.time()
                    state.add_audit("pipeline", "failed", {"failed_node": node.name, "duration_ms": self.duration_ms})
                    logger.error(f"Pipeline '{self.name}' failed at node '{node.name}'")
                    return state

            if node.name in self._conditional_edges:
                router_fn = self._conditional_edges[node.name]
                next_node_name = router_fn(state)
                if next_node_name is None:
                    logger.info(f"Pipeline '{self.name}' terminated by conditional edge after '{node.name}'")
                    break
                target_node = self._find_node(next_node_name)
                if target_node is not None:
                    target_idx = self._nodes.index(target_node)
                    idx = target_idx
                    continue

            idx += 1

        self._status = PipelineStatus.COMPLETED
        self._end_time = time.time()
        state.add_audit("pipeline", "completed", {"pipeline_name": self.name, "duration_ms": self.duration_ms})
        logger.info(f"Pipeline '{self.name}' completed in {self.duration_ms:.1f}ms")
        return state

    def get_execution_summary(self, state: WorkflowState) -> Dict[str, Any]:
        return {
            "pipeline_id": self.pipeline_id,
            "pipeline_name": self.name,
            "description": self.description,
            "status": self._status.value,
            "duration_ms": self.duration_ms,
            "total_nodes": len(self._nodes),
            "node_statuses": state.node_statuses,
            "errors": state.errors,
            "audit_trail": state.audit_trail,
        }
