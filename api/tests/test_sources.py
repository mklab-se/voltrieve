"""Source links: which documents an answer used, and signed links to the linkable ones."""

import base64
import datetime as dt
import logging
from urllib.parse import parse_qs, urlparse

import pytest
from azure.storage.blob import UserDelegationKey

from app.sources import (
    Document,
    LinkRefused,
    LinkSigner,
    SourceService,
    build_sources,
    cited_titles,
    named_models,
)
from tests.conftest import parse_sse

ACCOUNT = "https://stvoltrieve.blob.core.windows.net"


def doc(title, doc_type="brochure", model=None, linkable=True, container="voltrieve-docs"):
    return Document(
        title=title,
        path=f"{ACCOUNT}/{container}/sales/{model or 'all'}/{title}",
        linkable=linkable,
        doc_type=doc_type,
        model_slug=model or "all",
    )


FJORD_BROCHURE = doc("voltrieve-fjord-brochure.pdf", "brochure", "fjord")
FJORD_SPECS = doc("voltrieve-fjord-specifications.pdf", "specifications", "fjord")
ATLAS_BROCHURE = doc("voltrieve-atlas-brochure.pdf", "brochure", "atlas")
ATLAS_SPECS = doc("voltrieve-atlas-specifications.pdf", "specifications", "atlas")
BRAKES = doc("voltrieve-parts-brakes.md", "parts", linkable=False, container="voltrieve-parts")
ALL_DOCS = [FJORD_BROCHURE, FJORD_SPECS, ATLAS_BROCHURE, ATLAS_SPECS, BRAKES]


class FakeIndex:
    def __init__(self, docs=ALL_DOCS):
        self.docs = {d.title: d for d in docs}
        self.calls = []

    def find(self, titles, models):
        self.calls.append((list(titles), list(models)))
        found = [self.docs[t] for t in titles if t in self.docs]
        found += [d for d in self.docs.values() if d.model_slug in models and d not in found]
        return found

    def get(self, title):
        return self.docs.get(title)


class FakeSigner:
    def __init__(self):
        self.signed = []

    def sign(self, path):
        self.signed.append(path)
        return f"{path}?sv=2025&sp=r&sig=SECRET"


# What the model writes ------------------------------------------------------------------------

def test_cited_titles_are_the_file_names_in_order_once():
    text = (
        "Fjord AWD has air suspension. (voltrieve-fjord-brochure.pdf)\n"
        "WLTP is measured. (*voltrieve-fjord-specifications.pdf*, voltrieve-fjord-brochure.pdf)\n"
        "Brake pads: VP-10007 (voltrieve-parts-brakes.md)."
    )
    assert cited_titles(text) == [
        "voltrieve-fjord-brochure.pdf",
        "voltrieve-fjord-specifications.pdf",
        "voltrieve-parts-brakes.md",
    ]


def test_cited_titles_ignore_urls_and_paths():
    text = "See https://example.com/files/evil.pdf and ../secret/plan.pdf, or report.exe."
    assert cited_titles(text) == []


def test_named_models_in_order_of_first_mention():
    text = "The Atlas seats seven. The Voltrieve Fjord and the Atlas both tow. Sparkling!"
    assert named_models(text) == ["atlas", "fjord"]


# Which sources an answer gets ----------------------------------------------------------------

def test_cited_documents_are_sources_and_only_linkable_ones_get_a_link():
    text = "Pads (voltrieve-parts-brakes.md). Fjord (voltrieve-fjord-brochure.pdf)."
    items = build_sources(text, [BRAKES, FJORD_BROCHURE])
    assert [(i["id"], i["kind"]) for i in items[:2]] == [
        ("voltrieve-parts-brakes.md", "source"),
        ("voltrieve-fjord-brochure.pdf", "source"),
    ]
    assert "link" not in items[0]
    assert items[1]["link"] == "/sources/voltrieve-fjord-brochure.pdf"
    assert items[1]["title"] == "Fjord brochure"


def test_a_title_the_index_does_not_know_is_dropped():
    assert build_sources("Made up (voltrieve-imaginary-guide.pdf).", ALL_DOCS) == []


def test_read_more_offers_brochure_and_specifications_of_each_named_model():
    text = "The Fjord tows 2,000 kg (voltrieve-fjord-brochure.pdf). The Atlas tows more."
    items = build_sources(text, ALL_DOCS)
    assert [(i["id"], i["kind"]) for i in items] == [
        ("voltrieve-fjord-brochure.pdf", "source"),
        ("voltrieve-fjord-specifications.pdf", "read_more"),
        ("voltrieve-atlas-brochure.pdf", "read_more"),
        ("voltrieve-atlas-specifications.pdf", "read_more"),
    ]


def test_no_read_more_for_a_parts_answer():
    text = "For the Fjord: VP-10007 (voltrieve-parts-brakes.md)."
    assert [i["kind"] for i in build_sources(text, ALL_DOCS)] == ["source"]


def test_a_non_linkable_document_is_never_offered_to_read_more():
    hidden = doc("voltrieve-atlas-specifications.pdf", "specifications", "atlas", linkable=False)
    text = "Fjord (voltrieve-fjord-brochure.pdf). Atlas too."
    items = build_sources(text, [FJORD_BROCHURE, ATLAS_BROCHURE, hidden])
    assert "voltrieve-atlas-specifications.pdf" not in [i["id"] for i in items]


async def test_resolve_looks_up_the_cited_titles_and_the_named_models():
    index = FakeIndex()
    service = SourceService(index, FakeSigner())
    items = await service.resolve("Atlas (voltrieve-atlas-brochure.pdf).")
    assert index.calls == [(["voltrieve-atlas-brochure.pdf"], ["atlas"])]
    assert [i["id"] for i in items] == ["voltrieve-atlas-brochure.pdf", "voltrieve-atlas-specifications.pdf"]


async def test_resolve_without_citations_asks_nothing():
    index = FakeIndex()
    assert await SourceService(index, FakeSigner()).resolve("Hello! The Atlas is great.") == []
    assert index.calls == []


# Signing ------------------------------------------------------------------------------------

async def test_link_signs_a_linkable_document():
    signer = FakeSigner()
    url = await SourceService(FakeIndex(), signer).link("voltrieve-fjord-brochure.pdf")
    assert signer.signed == [FJORD_BROCHURE.path]
    assert url.startswith(FJORD_BROCHURE.path + "?")


async def test_link_refuses_a_non_linkable_document_and_never_signs_it():
    signer = FakeSigner()
    with pytest.raises(LinkRefused):
        await SourceService(FakeIndex(), signer).link("voltrieve-parts-brakes.md")
    assert signer.signed == []


async def test_link_to_an_unknown_document_is_a_key_error():
    with pytest.raises(KeyError):
        await SourceService(FakeIndex(), FakeSigner()).link("nothing.pdf")


class FakeBlobService:
    def __init__(self):
        self.calls = 0

    def get_user_delegation_key(self, key_start_time, key_expiry_time):
        self.calls += 1
        key = UserDelegationKey()
        key.signed_oid = "00000000-0000-0000-0000-000000000001"
        key.signed_tid = "00000000-0000-0000-0000-000000000002"
        key.signed_start = key_start_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        key.signed_expiry = key_expiry_time.strftime("%Y-%m-%dT%H:%M:%SZ")
        key.signed_service = "b"
        key.signed_version = "2025-11-05"
        key.value = base64.b64encode(b"k" * 32).decode()
        return key


class Clock:
    def __init__(self):
        self.now = dt.datetime(2026, 11, 17, 12, 0, tzinfo=dt.UTC)

    def __call__(self):
        return self.now


def test_signer_makes_a_short_read_only_link_to_one_blob():
    clock = Clock()
    signer = LinkSigner("stvoltrieve", {"voltrieve-docs"}, FakeBlobService(), clock)
    url = signer.sign(FJORD_BROCHURE.path)
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == FJORD_BROCHURE.path
    assert query["sp"] == ["r"]
    assert query["sr"] == ["b"]  # one blob, not the container
    assert query["se"] == ["2026-11-17T12:05:00Z"]
    assert query["skoid"]  # signed with a user delegation key, never an account key
    assert query["rscd"] == ["inline"]


def test_signer_reuses_the_delegation_key_until_it_nears_expiry():
    clock = Clock()
    service = FakeBlobService()
    signer = LinkSigner("stvoltrieve", {"voltrieve-docs"}, service, clock)
    signer.sign(FJORD_BROCHURE.path)
    clock.now += dt.timedelta(minutes=30)
    signer.sign(FJORD_BROCHURE.path)
    assert service.calls == 1
    clock.now += dt.timedelta(minutes=25)  # less than 10 minutes left on the key
    signer.sign(FJORD_BROCHURE.path)
    assert service.calls == 2


@pytest.mark.parametrize(
    "path",
    [
        "https://evil.blob.core.windows.net/voltrieve-docs/a.pdf",
        f"{ACCOUNT}/voltrieve-parts/voltrieve-parts-brakes.md",
        f"{ACCOUNT}/voltrieve-docs",
        "http://stvoltrieve.blob.core.windows.net/voltrieve-docs/a.pdf",
    ],
)
def test_signer_refuses_other_accounts_and_containers(path):
    signer = LinkSigner("stvoltrieve", {"voltrieve-docs"}, FakeBlobService(), Clock())
    with pytest.raises(LinkRefused):
        signer.sign(path)


# Through the API ------------------------------------------------------------------------------

@pytest.fixture
def signer():
    return FakeSigner()


@pytest.fixture
def sources_app(validator, backend, clock, signer):
    from app.backends import BackendRegistry
    from app.main import create_app
    from app.settings import Settings
    from app.turns import TurnStore

    settings = Settings(cors_origins="http://localhost:5173")
    registry = BackendRegistry(settings, {"loop": lambda s: backend})
    return create_app(settings, validator, registry, TurnStore(clock=clock),
                      sources=SourceService(FakeIndex(), signer))


@pytest.fixture
async def sources_client(sources_app):
    import httpx

    transport = httpx.ASGITransport(app=sources_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_an_answer_ends_with_a_sources_event_before_done(sources_client, auth, backend):
    from tests.conftest import answer

    backend.script.append(answer("The Fjord tows 2,000 kg (voltrieve-fjord-brochure.pdf)."))
    r = await sources_client.post("/chat", json={"message": "tow?"}, headers=auth)
    events = parse_sse(r.text)
    names = [name for name, _ in events]
    assert names[-2:] == ["sources", "done"]
    sources = events[-2][1]
    assert sources["items"][0] == {
        "id": "voltrieve-fjord-brochure.pdf",
        "title": "Fjord brochure",
        "kind": "source",
        "link": "/sources/voltrieve-fjord-brochure.pdf",
    }
    assert "sources_resolved" in [d.get("step") for n, d in events if n == "trace"]


async def test_an_answer_without_citations_has_no_sources_event(sources_client, auth, backend):
    from tests.conftest import answer

    backend.script.append(answer("Hello!"))
    r = await sources_client.post("/chat", json={"message": "hi"}, headers=auth)
    assert "sources" not in [name for name, _ in parse_sse(r.text)]


async def test_a_failing_lookup_never_breaks_the_answer(sources_client, sources_app, auth, backend):
    from tests.conftest import answer

    class Broken(FakeIndex):
        def find(self, titles, models):
            raise RuntimeError("search is down")

    sources_app.state.sources = SourceService(Broken(), FakeSigner())
    sources_app.state.turns.sources = sources_app.state.sources
    backend.script.append(answer("Fjord (voltrieve-fjord-brochure.pdf)."))
    r = await sources_client.post("/chat", json={"message": "tow?"}, headers=auth)
    names = [name for name, _ in parse_sse(r.text)]
    assert names[-1] == "done" and "sources" not in names


async def test_get_source_link(sources_client, auth):
    r = await sources_client.get("/sources/voltrieve-fjord-brochure.pdf", headers=auth)
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    assert body["url"].startswith(FJORD_BROCHURE.path + "?")
    assert body["expires_in"] == 300


async def test_get_source_link_needs_a_token(sources_client):
    r = await sources_client.get("/sources/voltrieve-fjord-brochure.pdf")
    assert r.status_code == 401


async def test_get_source_link_of_a_non_linkable_document_is_forbidden(sources_client, auth, signer):
    r = await sources_client.get("/sources/voltrieve-parts-brakes.md", headers=auth)
    assert r.status_code == 403
    assert signer.signed == []


async def test_get_source_link_of_an_unknown_document_is_not_found(sources_client, auth):
    r = await sources_client.get("/sources/voltrieve-nothing.pdf", headers=auth)
    assert r.status_code == 404


async def test_get_source_link_without_signing_configured_is_unavailable(client, auth):
    r = await client.get("/sources/voltrieve-fjord-brochure.pdf", headers=auth)
    assert r.status_code == 503


async def test_no_sas_in_logs_or_spans(sources_client, auth, caplog, monkeypatch):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from app import telemetry

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(telemetry, "tracer", provider.get_tracer("test"))
    with caplog.at_level(logging.DEBUG):
        r = await sources_client.get("/sources/voltrieve-fjord-brochure.pdf", headers=auth)
    assert "sig=SECRET" in r.text
    assert "SECRET" not in caplog.text
    for span in exporter.get_finished_spans():
        assert "SECRET" not in str(dict(span.attributes or {}))
        for event in span.events:
            assert "SECRET" not in str(dict(event.attributes or {}))
