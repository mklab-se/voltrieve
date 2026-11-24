"""Reads the compiled template on stdin (test_bicep.sh): what the insights job may do, when it runs,
and where its totals go."""

import json
import sys

template = json.load(sys.stdin)
modules = {
    name: r for name, r in template["resources"].items() if r["type"].endswith("/deployments")
}
assert "insights" in modules, "no insights module"


def arguments(module):
    return modules[module]["properties"].get("parameters", {})


# off and unset by default, so a plain provision starts nothing and reads no real chat
assert template["parameters"]["insightsSchedule"]["defaultValue"] == "off"
assert template["parameters"]["insightsNoticeDate"]["defaultValue"] == ""
assert template["parameters"]["insightsImage"]["defaultValue"] == ""
given = arguments("insights")
assert given["schedule"]["value"] == "[parameters('insightsSchedule')]"
assert given["noticeDate"]["value"] == "[parameters('insightsNoticeDate')]"
assert given["image"]["value"] == "[parameters('insightsImage')]"


# the identity of the job is used by its own two modules and nowhere else
def uses(wanted):
    return {name for name in modules if wanted(json.dumps(arguments(name)))}


identity = "'insightsIdentity'"
assert uses(lambda text: identity in text) == {"insights", "insightsRole"}
assert uses(lambda text: identity in text and "outputs.principalId" in text) >= {"insights", "insightsRole"}

def listed(module):
    found = modules[module]["properties"]["template"]["resources"]
    return list(found.values()) if isinstance(found, dict) else found


resources = listed("insights")
variables = modules["insights"]["properties"]["template"]["variables"]


def of(kind):
    return [r for r in resources if r["type"] == kind]


# no other role by another way: none in main.bicep itself, AcrPull alone where the identity is made,
# and one custom role. No module but those two takes the principal, as checked above.
assignment = "Microsoft.Authorization/roleAssignments"
top = [r for r in template["resources"].values() if r["type"] == assignment]
assert top == [], "main.bicep assigns a role itself"
(pulls,) = [r for r in listed("insightsIdentity") if r["type"] == assignment]
assert "acrPullRoleId" in pulls["properties"]["roleDefinitionId"]
assert pulls["scope"].startswith("[resourceId('Microsoft.ContainerRegistry/registries'"), pulls["scope"]
assert (
    modules["insightsIdentity"]["properties"]["template"]["variables"]["acrPullRoleId"]
    == "7f951dda-4ed3-4680-a7ca-43fe172d538d"
)
assert len([r for r in listed("insightsRole") if r["type"] == assignment]) == 1
for name in modules:  # nor by the name of the identity, or by its output
    if name not in ("insightsIdentity", "insightsRole", "insights"):
        text = json.dumps(modules[name])
        assert "id-insights" not in text and "insightsIdentity" not in text, name

# two roles on the job: save to this rule, read this table. The API gets the second one only.
assert variables["monitoringMetricsPublisher"] == "3913510d-42f4-4e42-8a64-420c390055eb"
assert variables["logAnalyticsDataReader"] == "3b03c2da-16b3-4a49-8834-0f8130efdd3b"
roles = of("Microsoft.Authorization/roleAssignments")
assert len(roles) == 2, len(roles)
(saves,) = [r for r in roles if "copy" not in r]
(reads,) = [r for r in roles if "copy" in r]
assert "monitoringMetricsPublisher" in saves["properties"]["roleDefinitionId"]
assert saves["properties"]["principalId"] == "[parameters('identityPrincipalId')]"
assert saves["scope"].startswith("[resourceId('Microsoft.Insights/dataCollectionRules'"), saves["scope"]
assert "logAnalyticsDataReader" in reads["properties"]["roleDefinitionId"]
assert reads["properties"]["principalId"] == (
    "[createArray(parameters('identityPrincipalId'), parameters('apiPrincipalId'))[copyIndex()]]"
)
assert "'apiApp'" in given["apiPrincipalId"]["value"]
assert reads["properties"]["conditionVersion"] == "2.0"
assert reads["properties"]["condition"] == "[variables('onlyThisTable')]"
condition = variables["onlyThisTable"]
assert condition == (  # as compiled: the table name goes in at {0}
    "[format('((!(ActionMatches{{''Microsoft.OperationalInsights/workspaces/tables/data/read''}})) OR "
    "(@Resource[Microsoft.OperationalInsights/workspaces/tables:name] StringEquals ''{0}''))', variables('tableName'))]"
), condition
assert variables["tableName"] == "VoltrieveInsights_CL"

# twelve months for the table, and the workspace stays at 30 days
(table,) = of("Microsoft.OperationalInsights/workspaces/tables")
assert table["properties"]["retentionInDays"] == table["properties"]["totalRetentionInDays"] == 365
workspaces = [r for r in listed("monitoring") if r["type"] == "Microsoft.OperationalInsights/workspaces"]
assert [r["properties"]["retentionInDays"] for r in workspaces] == [30]

# the stream and the table are made from one list
(rule,) = of("Microsoft.Insights/dataCollectionRules")
assert rule["kind"] == "Direct"
(stream,) = rule["properties"]["streamDeclarations"].values()
assert stream["columns"] == "[variables('columns')]"
(columns,) = table["properties"]["schema"]["copy"]
assert columns["count"] == "[length(variables('columns'))]"
types = {c["name"]: c["type"] for c in variables["columns"]}
for name in ("TimeGenerated", "Run", "WindowStart", "WindowEnd"):
    assert types[name] == "datetime", name
assert types["Conversations"] == types["People"] == "int" and types["SampleShare"] == "real"
(flow,) = rule["properties"]["dataFlows"]
assert flow["transformKql"] == "source" and "streamName" in flow["outputStream"]

# the job: by hand or once a night, small, one try, and one identity
(job,) = of("Microsoft.App/jobs")
assert job["identity"]["type"] == "UserAssigned" and len(job["identity"]["userAssignedIdentities"]) == 1
settings = job["properties"]["configuration"]
# the public sample never runs on a schedule: that identity reads everyone's chats
assert variables["byHand"] == "[or(equals(parameters('schedule'), 'off'), empty(parameters('image')))]"
assert settings["triggerType"] == "[if(variables('byHand'), 'Manual', 'Schedule')]", settings["triggerType"]
when = "if(variables('byHand'), "
assert settings["manualTriggerConfig"].startswith(f"[{when}createObject("), settings["manualTriggerConfig"]
assert settings["scheduleTriggerConfig"].startswith(f"[{when}null(), createObject("), settings["scheduleTriggerConfig"]
assert "'30 2 * * *'" in settings["scheduleTriggerConfig"]
assert settings["replicaTimeout"] == 1800 and settings["replicaRetryLimit"] == 0
assert "secrets" not in settings
(container,) = job["properties"]["template"]["containers"]
assert "0.5" in str(container["resources"]["cpu"]) and container["resources"]["memory"] == "1Gi"
assert [e["name"] for e in container["env"]] == [
    "FOUNDRY_PROJECT_ENDPOINT",
    "FOUNDRY_OPENAI_ENDPOINT",
    "LANGUAGE_ENDPOINT",
    "INSIGHTS_INGESTION_ENDPOINT",
    "INSIGHTS_RULE_ID",
    "LOG_ANALYTICS_WORKSPACE_ID",
    "AZURE_CLIENT_ID",
    "INSIGHTS_NOTICE_DATE",
    "INSIGHTS_LIMIT",
    "INSIGHTS_CAP",
    "MODEL_DEPLOYMENT",
]
assert "logAnalyticsWorkspaceId" in arguments("apiApp") and "workspaceId" in arguments("dashboardWorkbook")
for name in ("INSIGHTS_JOB_NAME", "LOG_ANALYTICS_WORKSPACE_ID"):
    assert name in template["outputs"], name
print("insights ok")
