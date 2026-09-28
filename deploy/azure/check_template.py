"""Compile deploy/azure/main.bicep and check it against the library's configuration (B13).

    python deploy/azure/check_template.py [BICEP_EXECUTABLE]

- the template compiles and lints with no warnings;
- every environment variable it sets is accepted by bidoc_library.config.from_env, with
  the values it would receive, so a deployment cannot fail on configuration;
- the pilot limits hold (0-1 replicas, 0.25 vCPU / 0.5 GiB, keyless storage, https only,
  sign-in everywhere except health and the token-only publishing API).
"""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    bicep = sys.argv[1] if len(sys.argv) > 1 else "bicep"
    lint = subprocess.run([bicep, "lint", str(HERE / "main.bicep")], capture_output=True, text=True)
    if lint.returncode or "Warning" in lint.stdout + lint.stderr:
        print(lint.stdout + lint.stderr)
        return 1
    built = subprocess.run([bicep, "build", str(HERE / "main.bicep"), "--stdout"], capture_output=True, text=True,
                           check=True)
    arm = json.loads(built.stdout)
    resources = arm["resources"]
    resources = resources.values() if isinstance(resources, dict) else resources
    by_type = {}
    for r in resources:
        by_type.setdefault(r["type"], []).append(r)
    app = by_type["Microsoft.App/containerApps"][0]["properties"]
    container = app["template"]["containers"][0]
    params = {p: v.get("defaultValue") for p, v in arm["parameters"].items()}
    assert params["minReplicas"] == 0 and params["maxReplicas"] == 1, "pilot scale is 0-1 replicas"
    assert (params["cpu"], params["memory"]) == ("0.25", "0.5Gi"), "pilot size is 0.25 vCPU / 0.5 GiB"
    assert app["configuration"]["ingress"]["allowInsecure"] is False
    storage = by_type["Microsoft.Storage/storageAccounts"][0]["properties"]
    assert storage["allowSharedKeyAccess"] is False and storage["allowBlobPublicAccess"] is False
    auth = by_type["Microsoft.App/containerApps/authConfigs"][0]["properties"]
    assert auth["globalValidation"]["unauthenticatedClientAction"] == "RedirectToLoginPage"
    assert sorted(auth["globalValidation"]["excludedPaths"]) == [
        "/api/v1/health/live", "/api/v1/health/ready", "/api/v1/publishing/*", "/api/v1/worker/*"], \
        "only health and the token-only publishing and worker APIs skip sign-in"
    job = by_type["Microsoft.App/jobs"][0]["properties"]
    assert job["template"]["containers"][0]["command"] == ["python", "-m", "bidoc_library", "cleanup"]
    assert job["configuration"]["triggerType"] == "Schedule", "cleanup must not depend on traffic"
    for forbidden in ("Microsoft.ContainerService/managedClusters", "Microsoft.Sql/servers",
                      "Microsoft.Search/searchServices", "Microsoft.Compute/virtualMachines"):
        assert forbidden not in by_type, f"{forbidden} is not part of the pilot"

    sample = {"trustedProxies": "127.0.0.1/32,::1/128", "entraRoleMap": params["entraRoleMap"],
              "entraDefaultRoles": params["entraDefaultRoles"], "tableName": params["tableName"],
              "containerName": params["containerName"]}
    env = {}
    items = container["env"]
    if isinstance(items, str):                  # "[variables('appEnv')]"
        items = arm["variables"][items.split("variables('")[1].split("'")[0]]
    for item in items:
        value = item["value"]
        if value.startswith("["):              # an ARM expression: substitute a realistic value
            if "parameters(" in value:
                value = sample[value.split("parameters('")[1].split("'")[0]]
            elif "tenant()" in value:
                value = "11111111-2222-4333-8444-555555555555"
            elif ".table" in value:
                value = "https://acct.table.core.windows.net/"
            elif ".blob" in value:
                value = "https://acct.blob.core.windows.net/"
            elif "clientId" in value:
                value = "22222222-2222-4222-8222-222222222222"
        env[item["name"]] = value
    sys.path.insert(0, str(HERE.parents[1] / "apps" / "library"))
    from bidoc_library.config import from_env  # noqa: PLC0415
    settings = from_env(env)
    settings.validate()
    assert (settings.data_backend, settings.auth_mode, settings.port) == ("azure", "entra", 8765)
    probes = {p["type"]: p["httpGet"]["path"] for p in container["probes"]}
    assert probes["Readiness"] == "/api/v1/health/ready"
    print("template ok:", ", ".join(sorted(env)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
