import json

from voltrieve_search.retrieve import RetrieveResult, retrieve


def body(partial=False):
    return {
        "response": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            [
                                {
                                    "ref_id": "0",
                                    "title": "voltrieve-fjord-specifications.pdf",
                                    "content": "Towing capacity: 2 100 kg",
                                }
                            ]
                        ),
                    }
                ],
            }
        ],
        "activity": [{"type": "searchIndex", "id": 0, "count": 3}]
        + ([{"type": "searchIndex", "id": 1, "error": {"message": "timeout"}}] if partial else []),
        "references": [
            {
                "type": "searchIndex",
                "id": "0",
                "docKey": "k1",
                "rerankerScore": 3.1,
                "sourceData": {"title": "voltrieve-fjord-specifications.pdf", "doc_type": "spec"},
            }
        ],
    }


class Client:
    def __init__(self, status, payload):
        self.status, self.payload = status, payload
        self.sent = None

    def post(self, path, b=None):
        self.sent = (path, b)
        return self.status, self.payload


def test_parses_chunks_and_references():
    c = Client(200, body())
    r = retrieve(c, "voltrieve-kb", "towing of the Fjord")
    assert r.chunks[0].content.startswith("Towing") and r.references[0].doc_key == "k1"
    assert r.partial is False
    assert c.sent[0] == "/knowledgebases/voltrieve-kb/retrieve"
    assert c.sent[1]["messages"] == [
        {"role": "user", "content": [{"type": "text", "text": "towing of the Fjord"}]}
    ]


def test_206_marks_partial_and_keeps_activity_errors():
    r = retrieve(Client(206, body(partial=True)), "voltrieve-kb", "q")
    assert r.partial is True
    assert any("error" in a for a in r.activity)


# Every property the REST reference of Knowledge Retrieval - Retrieve lists for 2026-11-01-preview.
# The service answers 400 to a property it does not know.
NOVEMBER_REQUEST = {
    "includeActivity", "intents", "knowledgeSourceParams", "maxOutputDocuments", "maxOutputSize",
    "maxOutputSizeInTokens", "maxRuntimeInSeconds", "messages", "outputMode",
    "retrievalReasoningEffort", "toolName",
}
NOVEMBER_SEARCH_INDEX_PARAMS = {
    "alwaysQuerySource", "enableImageServing", "failOnError", "filterAddOn",
    "includeReferenceSourceData", "includeReferences", "kind", "knowledgeSourceName",
    "maxOutputDocuments", "neverQuerySource", "queryHintOverrides", "rerankerThreshold",
    "resultsProcessing", "topicGraph",
}


def test_the_request_has_only_properties_the_november_reference_lists():
    c = Client(200, body())
    retrieve(c, "voltrieve-kb", "q")
    sent = c.sent[1]
    assert set(sent) <= NOVEMBER_REQUEST
    (params,) = sent["knowledgeSourceParams"]
    assert set(params) <= NOVEMBER_SEARCH_INDEX_PARAMS
    # What the reference and the how-to say about the combination: messages or intents, not both,
    # and source data needs references.
    assert ("messages" in sent) != ("intents" in sent)
    assert params["includeReferences"] or not params["includeReferenceSourceData"]


def test_new_fields_of_the_response_are_ignored():
    payload = body()
    payload["references"][0] |= {"activitySource": 1, "citationUrl": "https://s.search.windows.net/x"}
    payload["activity"][0] |= {"warning": "documents dropped", "startedAt": "2026-11-24T00:00:00Z"}
    payload["responseSensitivityLabelInfo"] = None
    r = retrieve(Client(200, payload), "voltrieve-kb", "q")
    assert r.references[0].doc_key == "k1" and r.chunks[0].ref_id == "0" and r.partial is False


def test_the_planning_model_is_read_from_the_nested_model_object():
    planned = {"type": "modelQueryPlanning", "model": {"modelName": "gpt-6-luna", "deploymentId": "d"}}
    assert RetrieveResult(activity=[{"type": "searchIndex"}, planned]).planning_model == "gpt-6-luna"


def test_the_planning_model_is_empty_when_the_activity_does_not_name_one():
    # The nested object and its properties are optional, and can be null.
    for activity in ([], [{"type": "searchIndex"}], [{"type": "modelQueryPlanning"}],
                     [{"type": "modelQueryPlanning", "model": None}],
                     [{"type": "modelQueryPlanning", "model": {"modelName": None}}],
                     [{"type": "modelQueryPlanning", "model": "gpt-6-luna"}],
                     [{"type": "modelAnswerSynthesis", "model": {"modelName": "x"}}]):
        assert RetrieveResult(activity=activity).planning_model is None
