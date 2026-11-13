"""Scoped durable workflow API client; iron-proxy supplies authorization."""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request


class WorkflowClient:
    def __init__(self, url=None):
        self.url = (url or os.environ.get("CENTAUR_API_URL", "http://api:8080")).rstrip("/")

    def request(self, method, path, body=None):
        req = urllib.request.Request(self.url + path, method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            with error:
                code = error.code
            detail = {
                401: "principal authentication failed",
                403: "the current principal lacks the endpoint capability or is not an allowed starter",
                404: "the resource is missing or outside the current principal's scope",
                503: "the workflow runtime is unavailable",
            }.get(code, "workflow request failed")
            raise RuntimeError(f"Workflow API HTTP {code}: {detail}") from None

    def catalog(self):
        return self.request("GET", "/api/workflows")

    def runs(self, name=None):
        query = urllib.parse.urlencode({"workflow_name": name} if name else {})
        return self.request("GET", "/api/workflows/runs?limit=20" + ("&" + query if query else ""))

    def run(self, run_id):
        return self.request("GET", "/api/workflows/runs/" + urllib.parse.quote(run_id, safe=""))["run"]

    def progress(self, run_id):
        return self.request("GET", "/api/workflows/runs/" + urllib.parse.quote(run_id, safe="") + "/progress")

    def access(self, workflow, action):
        query = urllib.parse.urlencode({"workflow": workflow, "action": action})
        return self.request("GET", "/api/workflows/access?" + query)

    def start(self, name, inputs, idempotency_key):
        return self.request("POST", "/api/workflows/runs", {"workflow_name": name, "input": inputs, "idempotency_key": idempotency_key})

    def wait(self, run_id, timeout=300, sleep=time.sleep, clock=time.monotonic):
        deadline = clock() + timeout
        while True:
            run = self.run(run_id)
            if run["status"] in {"completed", "failed", "cancelled"}:
                return run
            remaining = deadline - clock()
            if remaining <= 0:
                raise TimeoutError(f"Workflow {run_id} is still {run['status']}; inspect it with centaur-workflows run {run_id}")
            sleep(min(2, remaining))


def _client():
    return WorkflowClient()
