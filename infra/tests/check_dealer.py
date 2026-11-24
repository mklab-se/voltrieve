"""Reads the compiled template on stdin (test_bicep.sh): the dealer's identity gets one role, Table Data
Contributor on one table, and nothing else in the account."""

import json
import sys

template = json.load(sys.stdin)
resources = template["resources"]
modules = {
    name: r for name, r in (resources.items() if isinstance(resources, dict) else
                            ((r["name"], r) for r in resources))
    if r["type"].endswith("/deployments")
}
TABLE_CONTRIBUTOR = "0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3"
assert template["variables"]["storageTableDataContributor"] == TABLE_CONTRIBUTOR

# the only module that hands the dealer's identity a role
named = {n for n, m in modules.items()
         if "roleDefinitionId" in m["properties"]["parameters"]
         and "dealerApp" in json.dumps(m["properties"]["parameters"])}
assert named == {"dealerReadsTable"}, named
args = modules["dealerReadsTable"]["properties"]["parameters"]
assert "storageTableDataContributor" in args["roleDefinitionId"]["value"], args
assert args["tableName"]["value"] == "dealer" or "dealerTable" in args["tableName"]["value"], args
assert "ServicePrincipal" in args["principalType"]["value"]

# the module assigns one role, on the table
inner = modules["dealerReadsTable"]["properties"]["template"]["resources"]
assignments = [r for r in (inner.values() if isinstance(inner, dict) else inner)
               if r["type"] == "Microsoft.Authorization/roleAssignments"]
assert len(assignments) == 1
assert "tableServices/tables" in json.dumps(assignments[0]["scope"]), assignments[0]["scope"]
