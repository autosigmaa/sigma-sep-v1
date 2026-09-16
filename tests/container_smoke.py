"""HTTP stand-in for n8n. Only run on a disposable volume (run_container_smoke.sh)."""
import json
import os
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def request(path, data=None, authenticated=True, expected=200):
    headers = {"Content-Type": "application/json"}
    if authenticated:
        headers["X-Sigma-Key"] = os.environ["SIGMA_API_TOKEN"]
    req = urllib.request.Request("http://127.0.0.1:8000" + path,
                                 data=json.dumps(data).encode() if data is not None else None,
                                 headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            code, body = response.status, json.load(response)
    except urllib.error.HTTPError as error:
        code, body = error.code, json.load(error)
    assert code == expected, (path, code, expected, body)
    return body


def base(state):
    return "/jobs/" + state["job"]["job_id"]


def new_job(brand):
    job = {"job_id": "smoke-" + brand, "brand_id": brand, "task_type": "carousel",
           "input": {"brief": "Brief untuk " + brand, "slide_count": 2}}
    state = request("/jobs", job)
    assert state.pop("created"), "Use a fresh disposable volume"
    assert state["job"] == job
    assert state["next_action"]["input"] == job["input"]
    return state


def work(state):
    """Use only the returned job/next_action to produce deterministic fake results."""
    job, action = state["job"], state["next_action"]
    brand = job["brand_id"]
    if action["type"] == "create_package":
        data = {"caption": brand, "slides": [
            {"number": n, "title": brand, "body": action["input"]["brief"], "image_prompt": brand}
            for n in range(1, action["input"]["slide_count"] + 1)]}
    else:
        assert action["type"] == "generate_assets"
        package = action["input"]["package"]
        assert package["caption"] == brand
        data = {"slides": [{"number": s["number"], "url": f'https://example.com/{brand}/{s["number"]}.png'}
                           for s in package["slides"]]}
    payload = {"action_id": action["action_id"], "outcome": "success", "data": data}
    request(base(state) + "/results", payload)  # Deliberately discard the response, as if lost.
    saved = request(base(state))
    assert request(base(state) + "/results", payload) == saved
    assert saved["job"] == job
    return {"state": saved, "callback": payload}


def recover(records):
    states = []
    for record in records:
        expected = record["state"]
        state = request(base(expected))
        assert state == expected, "Restart changed the checkpoint"
        assert request(base(state) + "/results", record["callback"]) == state
        assert request(base(state) + "/resume", {}) == state
        states.append(state)
    return states


def finish(state):
    decision = "approve" if state["job"]["brand_id"] == "alpha" else "reject"
    assert state["next_action"]["type"] == "approval"
    assert all("/" + state["job"]["brand_id"] + "/" in slide["url"]
               for slide in state["artifacts"]["assets"]["slides"])
    payload = {"action_id": state["next_action"]["action_id"], "decision": decision}
    terminal = request(base(state) + "/approval", payload)
    assert terminal["status"] == ("completed" if decision == "approve" else "rejected")
    assert terminal["next_action"] is None
    assert terminal["job"] == state["job"]
    assert request(base(state) + "/approval", payload) == terminal
    assert request(base(state) + "/resume", {}) == terminal
    return terminal


def main(phase):
    # Driver-only evidence simulates saved n8n callbacks; it is not kernel state.
    evidence = Path("/data/smoke-evidence.json")
    assert request("/health") == {"status": "ok"}
    if phase == "prepare":
        assert "/jobs" in request("/openapi.json")["paths"]
        with ThreadPoolExecutor(2) as pool:
            states = list(pool.map(new_job, ["alpha", "beta"]))
        first, second = states
        request(base(first), authenticated=False, expected=401)
        request("/jobs", first["job"], authenticated=False, expected=401)
        action = first["next_action"]["action_id"]
        request(base(first) + "/approval", {"action_id": action, "decision": "approve"}, expected=409)
        failure = {"action_id": action, "outcome": "failed", "error": {"message": "wrong job"}}
        request(base(second) + "/results", failure, expected=409)
        request(base(first) + "/results", {"action_id": action, "outcome": "success", "data": {}}, expected=422)
        assert request(base(first)) == first
        assert request(base(second)) == second
        with ThreadPoolExecutor(2) as pool:
            records = list(pool.map(work, states))
        assert all(r["state"]["next_action"]["type"] == "generate_assets" for r in records)
        evidence.write_text(json.dumps(records))
        print("PASS: concurrent brands, validation, auth, lost/duplicate callbacks; checkpoint before assets")
    elif phase == "assets":
        records = json.loads(evidence.read_text())
        states = recover(records)
        request(base(states[1]) + "/results", records[0]["callback"], expected=409)
        with ThreadPoolExecutor(2) as pool:
            records = list(pool.map(work, states))
        assert all(r["state"]["status"] == "waiting_approval" for r in records)
        evidence.write_text(json.dumps(records))
        print("PASS: recovery from first forced kill, isolated assets; checkpoint before approval")
    elif phase == "finish":
        states = recover(json.loads(evidence.read_text()))
        with ThreadPoolExecutor(2) as pool:
            list(pool.map(finish, states))
        failed = new_job("retry")
        job = failed["job"]
        for attempt in range(1, 4):
            assert failed["next_action"]["action_id"].endswith(f":{attempt}")
            payload = {"action_id": failed["next_action"]["action_id"], "outcome": "failed",
                       "error": {"message": "Safe simulated failure", "retryable": True}}
            failed = request(base(failed) + "/results", payload)
            assert request(base(failed) + "/results", payload) == failed
            assert failed["job"] == job
        assert failed["status"] == "failed" and failed["next_action"] is None
        assert request(base(failed) + "/resume", {}) == failed
        print("PASS: recovery from second forced kill, approve/reject, three-attempt limit, terminal resume")
    else:
        raise SystemExit("Usage: python - prepare|assets|finish < tests/container_smoke.py")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) == 2 else "")
