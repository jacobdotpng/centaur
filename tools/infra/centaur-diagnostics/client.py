"""Read-only client for the operator-configured Centaur diagnostics API."""
import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class DiagnosticsClient:
    def __init__(self):
        endpoint = os.environ.get("CENTAUR_DIAGNOSTICS_URL", "").strip()
        url = urlsplit(endpoint)
        if (url.scheme not in {"http", "https"} or not url.hostname or url.username
                or url.password or url.path not in {"", "/"} or url.query or url.fragment):
            raise ValueError("Configure CENTAUR_DIAGNOSTICS_URL with the diagnostics origin")
        self.endpoint = endpoint.rstrip("/")
        self.opener = build_opener(NoRedirect())

    def _get(self, route, **params):
        url = f"{self.endpoint}/v1/{route}"
        if params:
            url += "?" + urlencode(params)
        for attempt in range(3):
            try:
                with self.opener.open(url, timeout=30) as response:
                    return json.load(response)
            except HTTPError as error:
                with error:
                    code = error.code
                    if code != 429 or attempt == 2:
                        detail = {
                            401: "the credential proxy did not supply an accepted diagnostics credential",
                            403: "the current principal or proxy rule denied the read",
                            429: "the diagnostics service stayed busy after bounded retries",
                            503: "a diagnostics backend is unavailable",
                        }.get(code, "the diagnostics request failed")
                        raise RuntimeError(f"{route}: HTTP {code}, {detail}") from None
                time.sleep(attempt + 1)
            except URLError as error:
                raise RuntimeError(f"{route}: diagnostics connection failed ({type(error.reason).__name__})") from None

    def deployment(self):
        """Return the verified release and current branch state."""
        return self._get("deployment")

    def pipelines(self, repo="source", run_id=None):
        """Read CI for one of the approved repository aliases."""
        if repo not in {"source", "config", "product"}:
            raise ValueError("Use source, config, or product")
        return self._get("pipelines", repo=repo, **({"run_id": run_id} if run_id else {}))

    def release(self, source):
        """Trace a source revision through promotion and verification."""
        return self._get("release", source=source)

    def health(self):
        """Read workload readiness and restart reasons."""
        return self._get("health")

    def preflight(self, extended=False):
        """Read the deployment and both pipelines sequentially."""
        deployment = self.deployment()
        source = self.pipelines("source")
        config = self.pipelines("config")
        result = {"deployment": deployment, "source_pipeline": source, "config_pipeline": config}
        if extended:
            self.health()
            self._get("logs", service="api", lines=1)
            result.update(health=True, logs=True)
        return result


def _client():
    return DiagnosticsClient()
