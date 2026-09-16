import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from sigma.api import create_app


TOKEN = "test-token-0123456789-abcdefgh"
JOB = {"job_id": "marlov-001", "brand_id": "marlov", "input": {"brief": "Contoh carousel", "slide_count": 1}}
PACKAGE = {"caption": "Caption", "slides": [{"number": 1, "title": "Judul", "body": "Isi", "image_prompt": "Slide dengan tulisan Judul dan Isi"}]}
ASSETS = {"slides": [{"number": 1, "url": "https://example.com/slide-1.png"}]}
BASE = "/jobs/marlov-001"


class KernelTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "checkpoints.sqlite"

    def client(self):
        return TestClient(create_app(self.db, TOKEN), headers={"X-Sigma-Key": TOKEN})

    def post(self, client, path, payload):
        response = client.post(path, json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["job"]["job_id"], response.json()["job_id"])
        return response.json()

    def result(self, state, data):
        return {"action_id": state["next_action"]["action_id"], "outcome": "success", "data": data}

    def test_restart_each_stage_and_duplicate_results(self):
        with self.client() as client:
            state = self.post(client, "/jobs", JOB)
            self.assertTrue(state["created"])
            self.assertEqual(state["job"], {**JOB, "task_type": "carousel"})
            self.assertFalse(self.post(client, "/jobs", JOB)["created"])
            first_action = state["next_action"]
        with self.client() as client:
            self.assertEqual(client.get(BASE).json()["next_action"], first_action)
            package_result = self.result(state, PACKAGE)
            state = self.post(client, BASE + "/results", package_result)
            self.assertEqual(self.post(client, BASE + "/results", package_result), state)
            self.assertEqual(state["current_step"], "generate_assets")
        with self.client() as client:
            self.assertEqual(client.get(BASE).json(), state)

            state = self.post(client, BASE + "/results", self.result(state, ASSETS))
            self.assertEqual(state["status"], "waiting_approval")
        with self.client() as client:
            self.assertEqual(client.get(BASE).json(), state)
            self.assertEqual(self.post(client, BASE + "/resume", None), state)
            decision = {"action_id": state["next_action"]["action_id"], "decision": "approve"}
            state = self.post(client, BASE + "/approval", decision)
            self.assertEqual(state["status"], "completed")
            self.assertIsNone(state["next_action"])
            self.assertEqual(self.post(client, BASE + "/approval", decision), state)
            self.assertEqual(self.post(client, BASE + "/results", package_result), state)
            self.assertEqual(self.post(client, BASE + "/resume", None), state)
        with self.client() as client:
            self.assertEqual(client.get(BASE).json(), state)

    def test_existing_v1_checkpoint_exposes_job_without_migration(self):
        with self.client() as client:
            original = {**JOB, "task_type": "carousel"}
            config = {"configurable": {"thread_id": JOB["job_id"]}}
            graph = client.app.state.graph
            graph.invoke({"schema_version": 1, "job": original, "step": "prepare", "status": "running",
                          "artifacts": {}, "approval": None, "attempts": {}, "error": None, "receipts": {}}, config)
            before = graph.get_state(config)
            response = client.get(BASE).json()
            self.assertEqual(response["job"], original)
            self.assertEqual(response["next_action"]["input"], original["input"])
            self.assertEqual(graph.get_state(config).config, before.config)

    def test_validation_auth_and_conflicts(self):
        with self.client() as client:
            self.assertEqual(client.get(BASE, headers={"X-Sigma-Key": "wrong"}).status_code, 401)
            self.assertEqual(client.get(BASE).status_code, 404)
            self.assertEqual(client.post("/jobs", json={**JOB, "input": {"brief": " "}}).status_code, 422)
            state = self.post(client, "/jobs", JOB)
            self.assertEqual(client.post("/jobs", json={**JOB, "brand_id": "other"}).status_code, 409)
            self.assertEqual(client.post(BASE + "/approval", json={"action_id": state["next_action"]["action_id"], "decision": "approve"}).status_code, 409)
            wrong = self.result(state, ASSETS)
            self.assertEqual(client.post(BASE + "/results", json=wrong).status_code, 409)
            invalid = {**PACKAGE, "slides": [{**PACKAGE["slides"][0], "number": 2}]}
            self.assertEqual(client.post(BASE + "/results", json=self.result(state, invalid)).status_code, 422)
            self.assertEqual(client.post(BASE + "/results", json={**self.result(state, PACKAGE), "action_id": "unknown"}).status_code, 409)
            payload = self.result(state, PACKAGE)
            next_state = self.post(client, BASE + "/results", payload)
            payload["data"] = {**PACKAGE, "caption": "Changed"}
            self.assertEqual(client.post(BASE + "/results", json=payload).status_code, 409)
            self.assertEqual(client.get(BASE).json(), next_state)

    def test_retries_bounded_and_fatal_failure(self):
        with self.client() as client:
            state = self.post(client, "/jobs", JOB)
            for attempt in range(1, 4):
                self.assertTrue(state["next_action"]["action_id"].endswith(f":{attempt}"))
                payload = {"action_id": state["next_action"]["action_id"], "outcome": "failed", "error": {"message": "Temporary failure", "retryable": True}}
                state = self.post(client, BASE + "/results", payload)
                self.assertEqual(self.post(client, BASE + "/results", payload), state)
            self.assertEqual(state["status"], "failed")
            self.assertEqual(self.post(client, BASE + "/resume", None), state)
            other = self.post(client, "/jobs", {**JOB, "job_id": "fatal"})
            fatal = self.post(client, "/jobs/fatal/results", {"action_id": other["next_action"]["action_id"], "outcome": "failed", "error": {"message": "Invalid API key"}})
            self.assertEqual(fatal["status"], "failed")

    def test_retry_success_and_reject(self):
        with self.client() as client:
            state = self.post(client, "/jobs", JOB)
            state = self.post(client, BASE + "/results", {"action_id": state["next_action"]["action_id"], "outcome": "failed", "error": {"message": "Try again", "retryable": True}})
            state = self.post(client, BASE + "/results", self.result(state, PACKAGE))
            self.assertIsNone(state["error"])
            state = self.post(client, BASE + "/results", self.result(state, ASSETS))
            state = self.post(client, BASE + "/approval", {"action_id": state["next_action"]["action_id"], "decision": "reject"})
            self.assertEqual(state["status"], "rejected")

    def test_concurrent_delivery_and_second_server_blocked(self):
        with self.client() as client:
            state = self.post(client, "/jobs", JOB)
            payload = self.result(state, PACKAGE)
            with ThreadPoolExecutor(2) as pool:
                responses = list(pool.map(lambda _: client.post(BASE + "/results", json=payload), range(2)))
            self.assertEqual([r.status_code for r in responses], [200, 200])
            self.assertEqual(responses[0].json(), responses[1].json())
            with self.assertRaises(BlockingIOError):
                with self.client():
                    pass

    def test_abrupt_process_exit_preserves_checkpoint(self):
        script = """
import os, sys
from fastapi.testclient import TestClient
from sigma.api import create_app
with TestClient(create_app(sys.argv[1], sys.argv[2]), headers={'X-Sigma-Key': sys.argv[2]}) as c:
    assert c.post('/jobs', json={'job_id':'marlov-001','brand_id':'marlov','input':{'brief':'Contoh carousel','slide_count':1}}).status_code == 200
    os._exit(0)
"""
        subprocess.run([sys.executable, "-c", script, str(self.db), TOKEN], check=True)
        with self.client() as client:
            self.assertEqual(client.get(BASE).json()["current_step"], "create_package")
            self.assertEqual(client.get(BASE).json()["status"], "waiting_external")

    def test_internal_node_failure_can_resume(self):
        from unittest.mock import patch
        with patch("sigma.graph.prepare", side_effect=RuntimeError("injected failure")):
            with self.client() as client:
                with self.assertRaises(RuntimeError):
                    client.post("/jobs", json=JOB)
                self.assertEqual(client.get(BASE).json()["status"], "recoverable")
        with self.client() as client:
            state = self.post(client, BASE + "/resume", None)
            self.assertEqual(state["status"], "waiting_external")
            self.assertEqual(state["current_step"], "create_package")


if __name__ == "__main__":
    unittest.main()
