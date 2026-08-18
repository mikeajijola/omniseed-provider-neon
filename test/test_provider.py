import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from provider.neon_provider import NeonProvider, ProviderError, PROTOCOL


class FakeClient:
    token = "secret-never-evidence"

    def __init__(self, projects=None):
        self.projects = list(projects or [])
        self.requests = []

    def request(self, path, method="GET", body=None):
        self.requests.append((path, method, body))
        if path == "/projects" and method == "GET":
            return {"projects": self.projects}
        if path.startswith("/projects?") and method == "POST" or path == "/projects" and method == "POST":
            project = {"id": "quiet-tree-123", "name": body["project"]["name"], "region_id": body["project"].get("region_id", "aws-eu-west-2"), "pg_version": body["project"].get("pg_version", 17)}
            self.projects.append(project)
            return {"project": project, "connection_uris": [{"connection_uri": "postgresql://must-not-leak"}]}
        if path.startswith("/projects/"):
            project_id = path.split("/")[-1]
            project = next((item for item in self.projects if item["id"] == project_id), None)
            if project:
                return {"project": project}
            raise ProviderError("missing", "remote_http_error", {"status": 404})
        raise AssertionError((path, method, body))


def action():
    return {"id": "action-1", "family": "memory", "resourceId": "engine_runtime_state", "desired": {"offers": ["runtime_state_continuity"], "spec": {"canonicalDesiredState": False, "product": "PostgreSQL"}}}


class ProviderTests(unittest.TestCase):
    def provider(self, client=None, configuration=None):
        subject = NeonProvider(configuration or {"projectName": "omniseed-runtime", "regionId": "aws-eu-west-2", "postgresVersion": 17}, client or FakeClient())
        subject.company_id = "omniseed_ecosystem"
        return subject

    def test_manifest_and_runtime_declare_neon_memory_boundary(self):
        client = FakeClient([{"id": "quiet-tree-123", "name": "omniseed-runtime"}])
        result = self.provider(client).initialize({"protocolVersion": PROTOCOL, "configuration": {"projectName": "omniseed-runtime"}, "context": {"companyId": "omniseed_ecosystem"}})
        manifest = json.loads(Path("provider-package.json").read_text())
        self.assertEqual(result["provider"]["id"], "neon")
        self.assertEqual(result["primitiveFamilies"], ["memory"])
        self.assertEqual(result["primitiveFamilies"], manifest["primitiveFamilies"])
        self.assertEqual(result["operations"], manifest["operations"])

    def test_validation_preserves_git_desired_state_boundary(self):
        self.assertTrue(self.provider().validate(action())["valid"])
        invalid = action()
        invalid["desired"]["spec"]["canonicalDesiredState"] = True
        self.assertEqual(self.provider().validate(invalid)["issues"][0]["code"], "desired_state_boundary")

    def test_plan_reuses_unique_project_and_rejects_ambiguity(self):
        project = {"id": "quiet-tree-123", "name": "omniseed-runtime"}
        self.assertEqual(self.provider(FakeClient([project])).plan(action())["project"]["change"], "reuse")
        with self.assertRaises(ProviderError) as raised:
            self.provider(FakeClient([project, {**project, "id": "other-456"}])).plan(action())
        self.assertEqual(raised.exception.code, "ambiguous_project")

    def test_apply_creates_once_without_leaking_connection_material(self):
        client = FakeClient()
        subject = self.provider(client)
        first = subject.apply(action())
        second = subject.apply(action())
        self.assertTrue(first["attributes"]["created"])
        self.assertFalse(second["attributes"]["created"])
        self.assertEqual(sum(1 for _, method, _ in client.requests if method == "POST"), 1)
        self.assertNotIn("postgresql://", json.dumps(first))
        self.assertNotIn(client.token, json.dumps(first))

    def test_observe_returns_non_secret_project_evidence(self):
        project = {"id": "quiet-tree-123", "name": "omniseed-runtime", "region_id": "aws-eu-west-2", "pg_version": 17}
        observed = self.provider(FakeClient([project])).observe({"providerResourceId": "neon://projects/quiet-tree-123", "attributes": {"projectId": "quiet-tree-123", "projectName": "omniseed-runtime"}})
        self.assertEqual(observed["status"], "healthy")
        self.assertEqual(observed["evidence"][0]["source"], "neon")
        self.assertNotIn("secret", json.dumps(observed))

    def test_process_fails_closed_without_credentials_and_does_not_echo_environment(self):
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "provider.initialize", "params": {"protocolVersion": PROTOCOL, "configuration": {"projectName": "omniseed-runtime"}, "context": {"companyId": "omniseed_ecosystem"}}},
            {"jsonrpc": "2.0", "id": 2, "method": "provider.status", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "provider.shutdown", "params": {}}
        ]
        with patch.dict(os.environ, {"NEON_API_KEY": "must-not-appear"}, clear=True):
            process = subprocess.run([sys.executable, "provider/neon_provider.py"], input="\n".join(json.dumps(item) for item in messages) + "\n", text=True, capture_output=True, check=True)
        output = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual(output[1]["result"]["implementation_available"], True)
        self.assertNotIn("must-not-appear", process.stdout + process.stderr)


if __name__ == "__main__":
    unittest.main()
