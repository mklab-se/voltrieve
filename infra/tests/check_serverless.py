"""Reads the compiled template on stdin (test_bicep.sh): what hangs on serverlessSearch, and what
`active` moves."""

import json
import sys

template = json.load(sys.stdin)
resources = template["resources"]
modules = {
    name: r for name, r in (resources.items() if isinstance(resources, dict) else
                            ((r["name"], r) for r in resources))
    if r["type"].endswith("/deployments")
}
parameters = template["parameters"]
assert parameters["serverlessSearch"]["allowedValues"] == ["off", "standby", "active"]
assert parameters["planningModelName"]["allowedValues"] == ["gpt-5.4-mini", "gpt-6-luna"]
assert parameters["planningModelName"]["defaultValue"] == "gpt-5.4-mini"

conditional = {n for n, m in modules.items() if "deployServerless" in str(m.get("condition"))}
assert conditional == {"searchServerless", "searchServerlessRoles"}, conditional
assert "condition" not in modules["search"]
assert template["variables"]["deployServerless"] == "[not(equals(parameters('serverlessSearch'), 'off'))]"
assert template["variables"]["serverlessActive"] == "[equals(parameters('serverlessSearch'), 'active')]"


def arguments(module):
    return modules[module]["properties"]["parameters"]


# active moves the API and both MCP connections, and the output the agents are deployed with
moved = {m: arguments(m)["searchEndpoint"] for m in ("apiApp", "kbConnection", "partsKbConnection")}
moved["output"] = template["outputs"]["SEARCH_ENDPOINT"]["value"]
for where, value in moved.items():
    assert value.startswith("[if(variables('serverlessActive'), "), (where, value)
    when_active, otherwise = value.split("reference(", 2)[1:]
    assert "searchServerless" in when_active and "searchServerless" not in otherwise, (where, value)
    assert "'search'" in otherwise, (where, value)

# the same callers can read the Serverless service, and its identity has two roles
roles = arguments("searchServerlessRoles")
readers = roles["readerPrincipalIds"]["value"]
for reader in ("'apiApp'", "projectPrincipalId", "hostedAgentPrincipalId"):
    assert reader in readers, readers
assert "searchServerless" in roles["searchName"]["value"] and "searchServerless" in roles["searchPrincipalId"]["value"]
assert roles["deployerPrincipalId"]["value"] == "[parameters('principalId')]"

inner = modules["searchServerlessRoles"]["properties"]["template"]
assigned = inner["resources"]
assigned = assigned if isinstance(assigned, dict) else {r["copy"]["name"] if "copy" in r else r["name"]: r for r in assigned}


def role(name):
    (found,) = [r for key, r in assigned.items() if name in key]
    args = found["properties"]["parameters"]
    return args["principalId"]["value"], args["roleDefinitionId"]["value"], found


ids = inner["variables"]
assert ids == {
    "storageBlobDataReader": "2a2b9908-6ea1-4ae2-8e65-a410df84e7d1",
    "cognitiveServicesOpenAIUser": "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd",
    "searchIndexDataReader": "1407120a-92aa-4202-b7e9-c0e197c71c8f",
    "searchServiceContributor": "7ca78c08-252a-4471-8644-bb5ff32d4ba0",
    "searchIndexDataContributor": "8ebe5a00-799e-43f5-93ac-243d3dce84a7",
}
assert len(assigned) == 4, list(assigned)
identity = "[parameters('searchPrincipalId')]"
assert role("readsStorage")[:2] == (identity, "[variables('storageBlobDataReader')]")
assert role("usesFoundry")[:2] == (identity, "[variables('cognitiveServicesOpenAIUser')]")
assert role("readers")[0] == "[parameters('readerPrincipalIds')[copyIndex()]]"
assert role("readers")[1] == "[variables('searchIndexDataReader')]"
deployer = role("deployer")
assert deployer[0] == "[parameters('deployerPrincipalId')]"
assert "searchServiceContributor" in deployer[1] and "searchIndexDataContributor" in deployer[1]
print("serverless ok")
