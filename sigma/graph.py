import hashlib
import json

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from sigma.state import SigmaState


def digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def action(state: SigmaState) -> dict:
    step = state["step"]
    return {
        "action_id": f'{state["job"]["job_id"]}:{step}:{state["attempts"].get(step, 1)}',
        "type": step,
        "input": state["job"]["input"] if step == "create_package" else state["artifacts"],
    }


def prepare(state: SigmaState) -> dict:
    return {"step": "create_package", "status": "waiting_external"}


def external(state: SigmaState) -> dict:
    result = interrupt(action(state))
    step = state["step"]
    receipts = {**state["receipts"], result["action_id"]: digest(result)}
    if result["outcome"] == "failed":
        attempt = state["attempts"].get(step, 1)
        retry = result["error"]["retryable"] and attempt < 3
        return {
            "receipts": receipts,
            "error": result["error"],
            "attempts": {**state["attempts"], step: attempt + 1 if retry else attempt},
            "status": "waiting_external" if retry else "failed",
        }
    next_step = "generate_assets" if step == "create_package" else "approval"
    artifact = "package" if step == "create_package" else "assets"
    return {
        "receipts": receipts,
        "artifacts": {**state["artifacts"], artifact: result["data"]},
        "step": next_step,
        "status": "waiting_approval" if next_step == "approval" else "waiting_external",
        "error": None,
    }


def approval(state: SigmaState) -> dict:
    result = interrupt(action(state))
    return {
        "receipts": {**state["receipts"], result["action_id"]: digest(result)},
        "approval": result["decision"],
        "step": "finalize",
        "status": "running",
    }


def finalize(state: SigmaState) -> dict:
    return {"step": "done", "status": "completed" if state["approval"] == "approve" else "rejected"}


def build_graph(checkpointer):
    graph = StateGraph(SigmaState)
    for name, node in (("prepare", prepare), ("create_package", external),
                       ("generate_assets", external), ("approval", approval), ("finalize", finalize)):
        graph.add_node(name, node)
    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "create_package")
    for step in ("create_package", "generate_assets"):
        graph.add_conditional_edges(step, lambda state: END if state["status"] == "failed" else state["step"],
                                    ["create_package", "generate_assets", "approval", END])
    graph.add_edge("approval", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile(checkpointer=checkpointer)
