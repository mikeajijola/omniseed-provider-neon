#!/usr/bin/env python3
"""Neon implementation of OmniSeed's durable runtime-state memory contract."""

import datetime
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

PROTOCOL = "omniseed.provider.protocol/1.0"
PROVIDER_ID = "neon"
VERSION = "0.1.0-alpha.1"
FAMILIES = ["memory"]
METHODS = [
    "provider.initialize", "provider.status", "provider.validate", "provider.plan",
    "provider.apply", "provider.observe", "provider.invoke", "provider.shutdown"
]
OPERATIONS = ["runtime.state.status", "runtime.state.evidence"]


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


class ProviderError(RuntimeError):
    def __init__(self, message, code="provider_error", details=None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


class NeonClient:
    def __init__(self, token=None, base_url="https://console.neon.tech/api/v2"):
        self.token = token
        self.base_url = base_url.rstrip("/")

    def request(self, path, method="GET", body=None):
        if not self.token:
            raise ProviderError("Neon credentials are unavailable", "not_configured")
        headers = {
            "Accept": "application/json",
            "Authorization": "Bearer " + self.token,
            "User-Agent": "omniseed-provider-neon/0.1"
        }
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(self.base_url + path, headers=headers, data=data, method=method)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                content = response.read().decode("utf-8")
                return json.loads(content) if content else {}
        except urllib.error.HTTPError as error:
            raise ProviderError("Neon API returned an error", "remote_http_error", {"status": error.code, "path": path}) from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise ProviderError("Neon API is unreachable", "remote_unreachable", {"path": path}) from error
        except json.JSONDecodeError as error:
            raise ProviderError("Neon API returned invalid JSON", "invalid_remote_response", {"path": path}) from error


class NeonProvider:
    def __init__(self, configuration=None, client=None):
        self.configuration = configuration or {}
        token_name = self.configuration.get("apiKeyEnvironment", "NEON_API_KEY")
        self.client = client or NeonClient(os.environ.get(token_name), self.configuration.get("apiBaseUrl", "https://console.neon.tech/api/v2"))
        self.company_id = None

    def initialize(self, params):
        if params.get("protocolVersion") != PROTOCOL:
            raise ProviderError("Unsupported protocol version", "protocol_mismatch", {"supported": PROTOCOL})
        self.configuration = params.get("configuration") or {}
        self.company_id = (params.get("context") or {}).get("companyId")
        token_name = self.configuration.get("apiKeyEnvironment", "NEON_API_KEY")
        if isinstance(self.client, NeonClient):
            self.client.token = os.environ.get(token_name)
            self.client.base_url = self.configuration.get("apiBaseUrl", "https://console.neon.tech/api/v2").rstrip("/")
        return {
            "protocolVersion": PROTOCOL,
            "provider": {"id": PROVIDER_ID, "name": "Neon", "organisation": "Neon", "version": VERSION},
            "primitiveFamilies": FAMILIES,
            "configurationSchema": "./provider-configuration.schema.json",
            "offerings": [{"family": "memory", "id": "runtime_state_continuity", "products": ["postgresql", "projects", "branches", "compute"]}],
            "operations": OPERATIONS,
            "methods": METHODS
        }

    def _configured(self):
        return bool(self.configuration.get("projectName") and getattr(self.client, "token", None))

    def _projects(self):
        return self.client.request("/projects").get("projects", [])

    def _resolve_project(self, allow_missing=False):
        project_id = self.configuration.get("projectId")
        if project_id:
            return self.client.request("/projects/" + urllib.parse.quote(project_id, safe=""))
        name = self.configuration.get("projectName")
        matches = [project for project in self._projects() if project.get("name") == name]
        if len(matches) > 1:
            raise ProviderError("Neon project name is ambiguous", "ambiguous_project", {"projectName": name, "matches": len(matches)})
        if matches:
            return {"project": matches[0]}
        if allow_missing:
            return None
        raise ProviderError("Configured Neon project does not exist", "project_missing", {"projectName": name})

    def status(self):
        configured = self._configured()
        connected = healthy = False
        if configured:
            try:
                connected = self._resolve_project(allow_missing=True) is not None or isinstance(self._projects(), list)
                healthy = connected
            except ProviderError:
                pass
        return {"implementation_available": True, "configured": configured, "connected": connected, "healthy": healthy}

    def validate(self, action):
        issues = []
        desired = (action.get("desired") or {})
        spec = desired.get("spec") or {}
        if action.get("family") != "memory":
            issues.append({"code": "unsupported_family", "message": "Neon supports the memory primitive family only"})
        if action.get("resourceId") != "engine_runtime_state" and "runtime_state_continuity" not in (desired.get("offers") or []):
            issues.append({"code": "unsupported_resource", "message": "Neon supports durable runtime-state resources only"})
        if spec.get("canonicalDesiredState") is not False:
            issues.append({"code": "desired_state_boundary", "message": "Neon runtime state must not claim canonical desired-state authority"})
        if spec.get("product") not in (None, "PostgreSQL", "postgresql"):
            issues.append({"code": "unsupported_product", "message": "The current Neon implementation uses PostgreSQL"})
        return {"valid": not issues, "issues": issues}

    def plan(self, action):
        validation = self.validate(action)
        project = self._resolve_project(allow_missing=True) if validation["valid"] and self._configured() else None
        return {
            "deterministic": True,
            "actionId": action.get("id"),
            "valid": validation["valid"],
            "issues": validation["issues"],
            "project": {"name": self.configuration.get("projectName"), "id": self.configuration.get("projectId"), "change": "reuse" if project else "create"},
            "expectedEvidence": ["neon_project_observation"]
        }

    def _create_project(self):
        project = {"name": self.configuration["projectName"]}
        for source, target in (("regionId", "region_id"), ("postgresVersion", "pg_version")):
            if self.configuration.get(source) is not None:
                project[target] = self.configuration[source]
        query = ""
        if self.configuration.get("organisationId"):
            query = "?org_id=" + urllib.parse.quote(self.configuration["organisationId"], safe="")
        return self.client.request("/projects" + query, "POST", {"project": project})

    def apply(self, action):
        validation = self.validate(action)
        if not validation["valid"]:
            raise ProviderError("Action is invalid", "invalid_action", {"issues": validation["issues"]})
        resolved = self._resolve_project(allow_missing=True)
        created = resolved is None
        result = self._create_project() if created else resolved
        project = result.get("project") or result
        project_id = project.get("id")
        if not project_id:
            raise ProviderError("Neon did not return a project identity", "invalid_remote_response")
        attributes = {
            "companyId": self.company_id,
            "projectId": project_id,
            "projectName": project.get("name") or self.configuration.get("projectName"),
            "regionId": project.get("region_id"),
            "postgresVersion": project.get("pg_version"),
            "created": created
        }
        return {"providerResourceId": "neon://projects/" + project_id, "status": "provisioned", "attributes": attributes}

    def observe(self, resource):
        attributes = resource.get("attributes") or {}
        project_id = attributes.get("projectId") or self.configuration.get("projectId")
        if not project_id:
            raise ProviderError("Persisted Neon binding has no project ID", "binding_invalid")
        result = self.client.request("/projects/" + urllib.parse.quote(project_id, safe=""))
        project = result.get("project") or result
        checked = now()
        active = project.get("id") == project_id and project.get("name") == (attributes.get("projectName") or self.configuration.get("projectName"))
        evidence = {
            "type": "neon_project_observation",
            "source": PROVIDER_ID,
            "projectId": project.get("id"),
            "projectName": project.get("name"),
            "regionId": project.get("region_id"),
            "postgresVersion": project.get("pg_version"),
            "matchesDesired": active,
            "observedAt": checked
        }
        return {"status": "healthy" if active else "degraded", "checkedAt": checked, "providerResourceId": resource.get("providerResourceId"), "evidence": [evidence], "snapshot": evidence}

    def invoke(self, operation, input_value, actor):
        if operation not in OPERATIONS:
            raise ProviderError("Unsupported operation", "unsupported_operation", {"operation": operation})
        project_id = (input_value or {}).get("projectId") or self.configuration.get("projectId")
        if not project_id:
            resolved = self._resolve_project()
            project_id = (resolved.get("project") or resolved).get("id")
        resource = {"providerResourceId": "neon://projects/" + project_id, "attributes": {"projectId": project_id, "projectName": self.configuration.get("projectName")}}
        observed = self.observe(resource)
        return observed if operation == "runtime.state.evidence" else observed["snapshot"]


def respond(request_id, result=None, error=None):
    message = {"jsonrpc": "2.0", "id": request_id}
    message["error" if error is not None else "result"] = error if error is not None else result
    sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def main():
    provider = NeonProvider()
    for line in sys.stdin:
        try:
            request = json.loads(line)
            request_id = request.get("id")
            method = request.get("method")
            params = request.get("params") or {}
            try:
                if method == "provider.initialize": result = provider.initialize(params)
                elif method == "provider.status": result = provider.status()
                elif method == "provider.validate": result = provider.validate(params.get("action") or {})
                elif method == "provider.plan": result = provider.plan(params.get("action") or {})
                elif method == "provider.apply": result = provider.apply(params.get("action") or {})
                elif method == "provider.observe": result = provider.observe(params.get("resource") or {})
                elif method == "provider.invoke": result = provider.invoke(params.get("operation"), params.get("input"), params.get("actor"))
                elif method == "provider.shutdown": result = {"shutdown": True}
                else:
                    respond(request_id, error={"code": -32601, "message": "Method not found"})
                    continue
                respond(request_id, result=result)
            except ProviderError as error:
                respond(request_id, error={"code": -32000, "message": str(error), "data": {"code": error.code, **error.details}})
        except Exception as error:
            respond(None, error={"code": -32603, "message": "Internal error", "data": {"type": type(error).__name__}})


if __name__ == "__main__":
    main()
