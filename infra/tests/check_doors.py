"""Reads the compiled template on stdin (test_bicep.sh): the MCP doors are off by default, what the two services get
when they are on, and that a host name of our own is bound on every provision."""

import json
import sys

template = json.load(sys.stdin)
parameters = template["parameters"]
modules = {name: r for name, r in template["resources"].items() if r["type"].endswith("/deployments")}


def arguments(module):
    return {name: p["value"] for name, p in modules[module]["properties"]["parameters"].items()}


def listed(module):
    found = modules[module]["properties"]["template"]["resources"]
    return list(found.values()) if isinstance(found, dict) else found


def environment(module):
    (app,) = [r for r in listed(module) if r["type"] == "Microsoft.App/containerApps"]
    (container,) = app["properties"]["template"]["containers"]
    return app, {e["name"]: e["value"] for e in container["env"]}


# off by default, and then every service gets nothing, whatever is left in the azd environment
assert parameters["mcpDoors"]["defaultValue"] is False
assert parameters["mcpAllowCli"]["defaultValue"] is False
assert parameters["mcpApiIdentifierIsUrl"]["defaultValue"] is False
assert parameters["mcpDealerIdentifierIsUrl"]["defaultValue"] is False
for name in ("apiHost", "dealerHost", "dealerDoorCities", "mcpApps", "mcpIssuer"):
    assert parameters[name]["defaultValue"] == "", name
DOORS = {
    "apiApp": ["mcpUrl", "mcpOperationsClientId", "mcpCarClientId", "mcpCustomerClientId", "mcpRunaClientId",
               "mcpApps", "mcpIssuer"],
    "dealerApp": ["mcpUrl", "mcpClientId", "mcpApps", "mcpIssuer", "doorCities"],
}


# An expression for one value, or one with an object per outcome
ONLY = (
    ("[if(parameters('mcpDoors'), createObject('value', ", ", createObject('value', ''))]"),
    ("[if(parameters('mcpDoors'), ", ", '')]"),
)
for module, names in DOORS.items():
    raw = modules[module]["properties"]["parameters"]
    for name in names:
        value = raw[name] if isinstance(raw[name], str) else raw[name]["value"]
        assert any(value.startswith(a) and value.endswith(b) for a, b in ONLY), (module, name, value)
    assert raw["mcpAllowCli"]["value"] == "[and(parameters('mcpDoors'), parameters('mcpAllowCli'))]"
    flag = {"apiApp": "mcpApiIdentifierIsUrl", "dealerApp": "mcpDealerIdentifierIsUrl"}[module]
    assert raw["mcpIdentifierIsUrl"]["value"] == f"[and(parameters('mcpDoors'), parameters('{flag}'))]"

# the settings of the services, and the two that must be true or false
SHARED = {"MCP_APPS", "MCP_ALLOW_CLI", "MCP_IDENTIFIER_IS_URL", "MCP_ISSUER"}
OWN = {
    "apiApp": {"MCP_API_URL", "MCP_OPERATIONS_CLIENT_ID", "MCP_CAR_CLIENT_ID", "MCP_CUSTOMER_CLIENT_ID",
               "MCP_RUNA_CLIENT_ID"},
    "dealerApp": {"MCP_DEALER_URL", "MCP_DEALER_CLIENT_ID", "DEALER_DOOR_CITIES"},
}
for module, host in (("apiApp", "apiHost"), ("dealerApp", "dealerHost")):
    app, env = environment(module)
    assert {n for n in env if n.startswith("MCP_") or "DOOR" in n} == SHARED | OWN[module]
    for name in ("MCP_ALLOW_CLI", "MCP_IDENTIFIER_IS_URL"):
        assert env[name].endswith("'true', 'false')]"), env[name]
    # a host name: bound by itself, with no state to keep, so a later provision cannot drop it
    domains = app["properties"]["configuration"]["ingress"]["customDomains"]
    assert domains == (
        "[if(empty(parameters('host')), null(), "
        "createArray(createObject('name', parameters('host'), 'bindingType', 'Auto')))]"
    ), domains
    assert modules[module]["properties"]["parameters"]["host"]["value"] == f"[parameters('{host}')]"

for module, host, app in (
    ("apiCertificate", "apiHost", "apiApp"), ("dealerCertificate", "dealerHost", "dealerApp"),
):
    assert modules[module]["condition"] == f"[not(empty(parameters('{host}')))]"
    assert app in modules[module]["dependsOn"], "the name must be on the app before its certificate"
    (certificate,) = [r for r in listed(module) if not r.get("existing")]
    assert certificate["type"] == "Microsoft.App/managedEnvironments/managedCertificates"
    assert certificate["properties"] == {"subjectName": "[parameters('host')]", "domainControlValidation": "CNAME"}
for name in ("MCP_API_URL", "MCP_DEALER_URL", "HOST_VERIFICATION_ID"):
    assert name in template["outputs"], name
print("doors ok")
