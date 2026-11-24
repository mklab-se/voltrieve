"""Reads the compiled template on stdin (test_bicep.sh): what hangs on serverlessSearch and
basicSearch, what `active` moves, and that without the Basic service nothing refers to it."""

import ast
import json
import re
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
assert parameters["basicSearch"]["allowedValues"] == ["on", "off"]
assert parameters["basicSearch"]["defaultValue"] == "on"
# everything that is about Basic: its roles, and who may use it. Nothing else hangs on it
BASIC = {"search", "apiReadsSearch", "hostedReadsSearch", "searchReadsStorage", "searchUsesFoundry",
         "searchPlansWithFoundry", "projectReadsSearch", "deployerSearchService", "deployerSearchData"}
assert {n for n, m in modules.items() if "deployBasic" in str(m.get("condition"))} == BASIC
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


# every combination of the two values, worked out as a deployment would: if() takes one branch only
class Refused(Exception):
    pass


class Deployment:
    def __init__(self, **values):
        self.values = {n: p.get("defaultValue", n) for n, p in parameters.items()} | values

    def text(self, value, reached):
        """The value of one template string. `reached` collects the modules it refers to."""
        if not (isinstance(value, str) and value.startswith("[") and not value.startswith("[[")):
            return value
        # if, and, or and not are words of Python, so they get another name
        expression = re.sub(r"\b(if|and|or|not|true|false|null)\(", r"arm_\1(", value[1:-1])
        return self.walk(ast.parse(expression, mode="eval").body, reached)

    def walk(self, node, reached):
        if isinstance(node, ast.Constant):
            return node.value
        if not isinstance(node, ast.Call):
            for child in ast.iter_child_nodes(node):
                self.walk(child, reached)
            return "unknown"
        name = node.func.id if isinstance(node.func, ast.Name) else ""
        if name == "arm_if":
            test = self.walk(node.args[0], reached)
            assert isinstance(test, bool), ast.unparse(node)
            return self.walk(node.args[1 if test else 2], reached)
        args = [self.walk(a, reached) for a in node.args]
        if name == "fail":
            raise Refused(args[0])
        if name == "reference":
            reached.add(args[0])
        if name == "variables":
            return self.text(template["variables"][args[0]], reached)
        known = {
            "parameters": lambda: self.values[args[0]], "equals": lambda: args[0] == args[1],
            "empty": lambda: args[0] == "", "arm_not": lambda: not args[0], "arm_and": lambda: all(args),
            "arm_or": lambda: any(args), "arm_true": lambda: True, "arm_false": lambda: False,
        }
        return known[name]() if name in known else "unknown"

    def everything(self, value, reached):
        if isinstance(value, dict):
            value = list(value.values())
        if isinstance(value, list):
            for item in value:
                self.everything(item, reached)
        else:
            self.text(value, reached)

    def run(self):
        """The deployed modules, and for each of them and each output the modules it refers to."""
        deployed = {n: m for n, m in modules.items() if self.text(m.get("condition", True), set())}
        uses = {}
        for name, value in [(n, m["properties"].get("parameters", {})) for n, m in deployed.items()] + [
            (n, o["value"]) for n, o in template["outputs"].items()
        ]:
            self.everything(value, uses.setdefault(name, set()))
        return set(deployed), uses


everyone = {"hostedAgentPrincipalId": "agent", "principalId": "deployer"}
for serverless in ("off", "standby", "active"):
    deployed, uses = Deployment(serverlessSearch=serverless, **everyone).run()  # Basic is on by default
    assert BASIC <= deployed and uses["SEARCH_BASIC_ENDPOINT"] == {"search"}
    assert all(reached <= deployed for reached in uses.values()), (serverless, uses)
    if serverless != "active":
        try:
            Deployment(basicSearch="off", serverlessSearch=serverless, **everyone).run()
        except Refused as refused:
            assert "SEARCH_SERVERLESS active" in str(refused)
        else:
            raise AssertionError(f"Basic can be off with Serverless {serverless}")

deployed, uses = Deployment(basicSearch="off", serverlessSearch="active", **everyone).run()
assert deployed & BASIC == set() and {"searchServerless", "searchServerlessRoles"} <= deployed
assert all(reached <= deployed for reached in uses.values()), uses  # nothing refers to what is not there
for user in ("apiApp", "kbConnection", "partsKbConnection", "SEARCH_ENDPOINT", "SEARCH_SERVICE_NAME"):
    assert "searchServerless" in uses[user], user
assert uses["SEARCH_BASIC_ENDPOINT"] == set()
print("serverless ok")
