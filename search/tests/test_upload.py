import json

import pytest

import voltrieve_search.upload as upload
from voltrieve_search.client import SearchApiError


class FakeContainer:
    def __init__(self, name="c", existing=(), events=None):
        self.url = f"https://x/{name}"
        self.blobs = {blob: "existing" for blob in existing}
        self.events = events if events is not None else []

    def upload_blob(self, name, handle, overwrite, metadata, content_settings):
        self.events.append(("upload", name))
        self.blobs[name] = content_settings.content_type
        self.metadata = getattr(self, "metadata", {})
        self.metadata[name] = metadata

    def list_blobs(self):
        return [type("Blob", (), {"name": name})() for name in list(self.blobs)]

    def delete_blob(self, name):
        self.events.append(("delete", name))
        del self.blobs[name]


class FakeService:
    containers = {}

    def __init__(self, url, credential):
        pass

    def get_container_client(self, name):
        return self.containers.setdefault(name, FakeContainer())


def test_content_type_is_set_per_file(tmp_path, monkeypatch):
    names = ["a.md", "b.pdf", "c.docx"]
    for n in names:
        (tmp_path / n).write_bytes(b"x")
    manifest = [
        {"file": n, "doc_type": "spec", "model_slug": None, "collection": "docs"} for n in names
    ]
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    FakeService.containers = {}
    monkeypatch.setattr(upload, "BlobServiceClient", FakeService)
    assert upload.upload_documents("https://x", {"docs": "c"}, tmp_path, None).uploaded == 3
    assert FakeService.containers["c"].blobs == {
        "a.md": "text/markdown",
        "b.pdf": "application/pdf",
        "c.docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }


def test_each_collection_goes_to_its_own_container(tmp_path, monkeypatch):
    (tmp_path / "a.md").write_bytes(b"x")
    (tmp_path / "b.md").write_bytes(b"x")
    manifest = [
        {"file": "a.md", "doc_type": "guide", "model_slug": None, "collection": "docs"},
        {"file": "b.md", "doc_type": "parts", "model_slug": None, "collection": "parts"},
    ]
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    FakeService.containers = {}
    monkeypatch.setattr(upload, "BlobServiceClient", FakeService)
    upload.upload_documents("https://x", {"docs": "d", "parts": "p"}, tmp_path, None)
    assert set(FakeService.containers["d"].blobs) == {"a.md"}
    assert set(FakeService.containers["p"].blobs) == {"b.md"}


def manifest_dir(tmp_path, names):
    for n in names:
        (tmp_path / n).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / n).write_bytes(b"x")
    manifest = [{"file": n, "doc_type": "brochure", "model_slug": "fjord", "collection": "docs"} for n in names]
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    return tmp_path


def with_container(monkeypatch, container):
    FakeService.containers = {"c": container}
    monkeypatch.setattr(upload, "BlobServiceClient", FakeService)


def test_never_deletes_a_manifest_blob(tmp_path, monkeypatch):
    names = ["sales/fjord/voltrieve-fjord-brochure.pdf", "voltrieve-service-guide.md"]
    container = FakeContainer(existing=names + ["voltrieve-fjord-brochure.docx"])
    with_container(monkeypatch, container)
    result = upload.upload_documents("https://x", {"docs": "c"}, manifest_dir(tmp_path, names), None,
                                     prune=True, out=lambda line: None)
    assert result.stale == ["c/voltrieve-fjord-brochure.docx"]
    assert result.deleted == ["c/voltrieve-fjord-brochure.docx"]
    assert set(container.blobs) == set(names)


def test_dry_run_deletes_nothing(tmp_path, monkeypatch):
    names = ["voltrieve-service-guide.md"]
    container = FakeContainer(existing=names + ["old.docx"])
    with_container(monkeypatch, container)
    printed = []
    result = upload.upload_documents("https://x", {"docs": "c"}, manifest_dir(tmp_path, names), None,
                                     prune=True, dry_run=True, out=printed.append)
    assert result.stale == ["c/old.docx"] and result.deleted == [] and result.uploaded == 0
    assert container.events == []
    assert any("old.docx" in line for line in printed)


def test_without_prune_stale_blobs_are_only_reported(tmp_path, monkeypatch):
    names = ["voltrieve-service-guide.md"]
    container = FakeContainer(existing=names + ["old.docx"])
    with_container(monkeypatch, container)
    result = upload.upload_documents("https://x", {"docs": "c"}, manifest_dir(tmp_path, names), None,
                                     out=lambda line: None)
    assert result.stale == ["c/old.docx"] and result.deleted == []
    assert "old.docx" in container.blobs


def test_lists_stale_before_deleting(tmp_path, monkeypatch):
    names = ["voltrieve-service-guide.md"]
    events = []
    container = FakeContainer(existing=names + ["old-a.docx", "old-b.pdf"], events=events)
    with_container(monkeypatch, container)
    upload.upload_documents("https://x", {"docs": "c"}, manifest_dir(tmp_path, names), None,
                            prune=True, out=lambda line: events.append(("print", line)))
    first_delete = next(i for i, e in enumerate(events) if e[0] == "delete")
    printed_before = [e[1] for e in events[:first_delete] if e[0] == "print"]
    assert any("old-a.docx" in line for line in printed_before)
    assert any("old-b.pdf" in line for line in printed_before)


class FakeSearch:
    def __init__(self, hits):
        self.hits = hits
        self.calls = []

    def post(self, path, body=None):
        self.calls.append((path, body))
        if path.endswith("/docs/search"):
            return 200, {"value": [{"chunk_id": k} for k in self.hits]}
        return 200, {}


def test_stale_index_entries_are_deleted_by_source_path():
    search = FakeSearch(["k1", "k2"])
    count = upload.delete_index_entries(search, "voltrieve-docs-index",
                                        ["https://x/c/it's.docx", "it's.docx"])
    assert count == 2
    path, body = search.calls[0]
    assert path == "/indexes/voltrieve-docs-index/docs/search"
    assert body["filter"] == "source_path eq 'https://x/c/it''s.docx' or source_path eq 'it''s.docx'"
    path, body = search.calls[1]
    assert path == "/indexes/voltrieve-docs-index/docs/index"
    assert body["value"] == [{"@search.action": "delete", "chunk_id": "k1"},
                             {"@search.action": "delete", "chunk_id": "k2"}]


def test_no_index_call_when_nothing_matches():
    search = FakeSearch([])
    assert upload.delete_index_entries(search, "voltrieve-docs-index", ["a.docx"]) == 0
    assert len(search.calls) == 1


def test_only_linkable_doc_types_are_marked_linkable(tmp_path, monkeypatch):
    for n in ("brochure.pdf", "internal.pdf", "catalog.md"):
        (tmp_path / n).write_bytes(b"x")
    manifest = [
        {"file": "brochure.pdf", "doc_type": "brochure", "model_slug": "fjord", "collection": "docs"},
        {"file": "internal.pdf", "doc_type": "pricing_notes", "model_slug": None, "collection": "docs"},
        {"file": "catalog.md", "doc_type": "parts", "model_slug": None, "collection": "docs"},
    ]
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    FakeService.containers = {}
    monkeypatch.setattr(upload, "BlobServiceClient", FakeService)
    upload.upload_documents("https://x", {"docs": "c"}, tmp_path, None, linkable_doc_types={"brochure"})
    metadata = FakeService.containers["c"].metadata
    assert metadata["brochure.pdf"]["linkable"] == "true"
    assert metadata["internal.pdf"]["linkable"] == "false"
    assert metadata["catalog.md"]["linkable"] == "false"


def test_nothing_is_linkable_unless_configured(tmp_path, monkeypatch):
    (tmp_path / "brochure.pdf").write_bytes(b"x")
    manifest = [{"file": "brochure.pdf", "doc_type": "brochure", "model_slug": "fjord", "collection": "docs"}]
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    FakeService.containers = {}
    monkeypatch.setattr(upload, "BlobServiceClient", FakeService)
    upload.upload_documents("https://x", {"docs": "c"}, tmp_path, None)
    assert FakeService.containers["c"].metadata["brochure.pdf"]["linkable"] == "false"


def test_the_config_links_customer_documents_and_never_the_parts_catalogs():
    from pathlib import Path

    linkable = upload.linkable_doc_types(Path(__file__).parents[2] / "data" / "voltrieve.yaml")
    assert {"brochure", "specifications", "buyers_guide", "service"} <= linkable
    assert "parts" not in linkable


BASIC, SERVERLESS = "https://basic.search.windows.net", "https://sl.search.windows.net"


class PruneSearch:
    """Stands in for the search services of one run of the command, by endpoint."""

    def __init__(self, missing=(), refused=()):
        self.missing, self.refused, self.deleted, self.events = missing, refused, [], []

    def client(self, endpoint):
        outer = self

        class Client:
            def get(self, path):
                outer.events.append(("get", endpoint))
                if endpoint in outer.refused:
                    raise SearchApiError(403, "forbidden")
                if endpoint in outer.missing:
                    raise SearchApiError(404, "no such index")
                return {}

            def post(self, path, body=None):
                if path.endswith("/docs/search"):
                    return 200, {"value": [{"chunk_id": "k"}]}
                outer.deleted.append((endpoint, path.split("/")[2]))
                return 200, {}

        return Client()


def prune(monkeypatch, search, deleted=("d/old.pdf",), args=("--prune",), **env):
    import voltrieve_search.cli as cli

    for name in ("SEARCH_ENDPOINT", "SEARCH_BASIC", "SEARCH_BASIC_ENDPOINT", "SEARCH_SERVERLESS_ENDPOINT"):
        monkeypatch.delenv(name, raising=False)
    for name, value in {"STORAGE_ACCOUNT_NAME": "s", "DOCS_CONTAINER": "d", "PARTS_CONTAINER": "p", **env}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(cli, "make_client", lambda endpoint=None: search.client(endpoint))

    def fake_upload(*a, **k):
        search.events.append(("upload", k["prune"]))
        return upload.UploadResult(uploaded=1, stale=list(deleted), deleted=list(deleted))

    monkeypatch.setattr(cli, "upload_documents", fake_upload)
    return cli.main(["upload", *args])


def test_prune_removes_the_chunks_from_every_search_service(monkeypatch):
    for active in (BASIC, SERVERLESS + "/"):
        search = PruneSearch()
        assert prune(monkeypatch, search, SEARCH_ENDPOINT=active, SEARCH_BASIC_ENDPOINT=BASIC,
                     SEARCH_SERVERLESS_ENDPOINT=SERVERLESS) == 0
        assert sorted(search.deleted) == [(BASIC, "voltrieve-docs-index"), (SERVERLESS, "voltrieve-docs-index")]


def test_prune_on_one_service_is_what_it_was(monkeypatch):
    search = PruneSearch()
    assert prune(monkeypatch, search, SEARCH_ENDPOINT=BASIC, SEARCH_BASIC_ENDPOINT=BASIC,
                 SEARCH_SERVERLESS_ENDPOINT="") == 0
    assert search.deleted == [(BASIC, "voltrieve-docs-index")]


def test_prune_skips_a_service_that_is_not_filled_yet(monkeypatch):
    search = PruneSearch(missing=[SERVERLESS])
    assert prune(monkeypatch, search, SEARCH_ENDPOINT=BASIC, SEARCH_BASIC_ENDPOINT=BASIC,
                 SEARCH_SERVERLESS_ENDPOINT=SERVERLESS) == 0
    assert search.deleted == [(BASIC, "voltrieve-docs-index")]


def test_prune_deletes_no_blob_when_a_service_cannot_be_reached(monkeypatch, capsys):
    """Once the blob is gone nothing can find its chunks again, so the check comes first."""
    search = PruneSearch(refused=[SERVERLESS])
    assert prune(monkeypatch, search, SEARCH_ENDPOINT=BASIC, SEARCH_BASIC_ENDPOINT=BASIC,
                 SEARCH_SERVERLESS_ENDPOINT=SERVERLESS) == 1
    assert ("upload", True) not in search.events and search.deleted == []
    assert "403" in capsys.readouterr().err


def test_prune_needs_the_whole_environment(monkeypatch, capsys):
    """With SEARCH_ENDPOINT alone the command cannot know about a second service."""
    search = PruneSearch()
    assert prune(monkeypatch, search, SEARCH_ENDPOINT=BASIC) == 1
    assert search.events == [] and "SEARCH_BASIC_ENDPOINT" in capsys.readouterr().err


def test_prune_works_without_the_basic_service(monkeypatch):
    search = PruneSearch()
    assert prune(monkeypatch, search, SEARCH_BASIC="off", SEARCH_BASIC_ENDPOINT="", SEARCH_ENDPOINT=SERVERLESS,
                 SEARCH_SERVERLESS_ENDPOINT=SERVERLESS) == 0
    assert search.deleted == [(SERVERLESS, "voltrieve-docs-index")]


def test_prune_still_reaches_a_basic_service_the_environment_names(monkeypatch):
    """off is set, but not provisioned yet."""
    search = PruneSearch()
    assert prune(monkeypatch, search, SEARCH_BASIC="off", SEARCH_BASIC_ENDPOINT=BASIC, SEARCH_ENDPOINT=SERVERLESS,
                 SEARCH_SERVERLESS_ENDPOINT=SERVERLESS) == 0
    assert sorted(search.deleted) == [(BASIC, "voltrieve-docs-index"), (SERVERLESS, "voltrieve-docs-index")]


@pytest.mark.parametrize(("env", "missing"), [
    ({"SEARCH_BASIC": "off"}, "SEARCH_SERVERLESS_ENDPOINT"),
    ({"SEARCH_BASIC": "on", "SEARCH_SERVERLESS_ENDPOINT": "set"}, "SEARCH_BASIC_ENDPOINT"),
    ({"SEARCH_SERVERLESS_ENDPOINT": "set"}, "SEARCH_BASIC_ENDPOINT"),
])
def test_an_empty_basic_endpoint_needs_basic_off_and_a_serverless_service(monkeypatch, capsys, env, missing):
    search = PruneSearch()
    assert prune(monkeypatch, search, SEARCH_ENDPOINT=SERVERLESS, SEARCH_BASIC_ENDPOINT="", **env) == 1
    assert search.events == [] and missing in capsys.readouterr().err


def test_an_upload_or_a_dry_run_needs_no_search_service(monkeypatch):
    for args in ((), ("--prune", "--dry-run")):
        search = PruneSearch()
        assert prune(monkeypatch, search, deleted=(), args=args) == 0
        assert [e for e in search.events if e[0] == "get"] == []
