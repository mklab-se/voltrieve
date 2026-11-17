"""Source links: the documents an answer used, and short signed links to the linkable ones.

The model never writes a URL. It cites document titles (file names), as its instructions ask.
After the answer, the API looks those titles up in the indexes, which say where each document
is and whether users may open it, and sends a `sources` event. A link is signed only when the
user clicks it: a user delegation SAS for one blob, read only, valid for five minutes. The SAS
is never logged, traced or shown to the model.
"""

import asyncio
import datetime as dt
import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import quote, unquote, urlparse

logger = logging.getLogger(__name__)

LINK_SECONDS = 300
LOOKUP_TIMEOUT_SECONDS = 3.0
KEY_HOURS = 1
KEY_REFRESH_MARGIN = dt.timedelta(minutes=10)
INDEX_NAMES = ("voltrieve-docs-index", "voltrieve-parts-index")
DOCS_INDEX = INDEX_NAMES[0]

# A file name as the model cites it, not part of a URL, a path or a longer name. The web client
# (clients/web/src/sources.ts) uses the same boundaries, except that it skips code spans.
_CITATION = re.compile(
    r"(?<![\w./:=\[-])([A-Za-z0-9][A-Za-z0-9_-]*(?:\.[A-Za-z0-9_-]+)*\.(?:pdf|md|docx))(?![\w-]|\.\w)"
)
MODELS = {"spark": "Spark", "lumen": "Lumen", "ragn": "Ragn", "fjord": "Fjord", "atlas": "Atlas"}
_MODEL = re.compile(r"\b(" + "|".join(MODELS.values()) + r")\b")
# A sales answer also offers these documents for every model it names.
SALES_TYPES = {"brochure", "specifications", "price_list", "accessories", "buyers_guide"}
READ_MORE_TYPES = ("brochure", "specifications")


@dataclass(frozen=True)
class Document:
    title: str  # the file name, which the model cites
    path: str  # the blob URL
    linkable: bool
    doc_type: str
    model_slug: str


class LinkRefused(Exception):
    """The document exists but must not be linked."""


def cited_titles(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(1) for m in _CITATION.finditer(text)))


def named_models(text: str) -> list[str]:
    names = dict.fromkeys(m.group(1) for m in _MODEL.finditer(text))
    slug_of = {name: slug for slug, name in MODELS.items()}
    return [slug_of[name] for name in names]


def label(document: Document) -> str:
    """A short name for a chip: "voltrieve-fjord-brochure.pdf" becomes "Fjord brochure"."""
    stem = document.title.rsplit(".", 1)[0].removeprefix("voltrieve-").replace("-", " ")
    return stem[:1].upper() + stem[1:]


def _item(document: Document, kind: str) -> dict:
    item = {"id": document.title, "title": label(document), "kind": kind}
    if document.linkable:
        item["link"] = f"/sources/{quote(document.title)}"
    return item


def build_sources(text: str, documents: list[Document]) -> list[dict]:
    """`source` for each cited document the index knows, in citation order. When the answer
    cites a sales document, `read_more` offers the brochure and specifications of every model
    it names. A title the index does not know is dropped, so an invented one never shows."""
    by_title = {d.title: d for d in documents}
    cited = [by_title[t] for t in cited_titles(text) if t in by_title]
    items = [_item(d, "source") for d in cited]
    if not any(d.doc_type in SALES_TYPES for d in cited):
        return items
    shown = {d.title for d in cited}
    for slug in named_models(text):
        for doc_type in READ_MORE_TYPES:
            for d in documents:
                if (d.model_slug == slug and d.doc_type == doc_type and d.linkable
                        and d.title not in shown):
                    items.append(_item(d, "read_more"))
                    shown.add(d.title)
    return items


class DocumentIndex(Protocol):
    def find(self, titles: list[str], models: list[str]) -> list[Document]: ...

    def get(self, title: str) -> Document | None: ...


class Signer(Protocol):
    def sign(self, path: str) -> str: ...


class SourceService:
    def __init__(self, index: DocumentIndex, signer: Signer | None):
        self._index = index
        self._signer = signer

    async def resolve(self, text: str) -> list[dict]:
        titles = cited_titles(text)
        if not titles:
            return []
        documents = await asyncio.to_thread(self._index.find, titles, named_models(text))
        items = build_sources(text, documents)
        if self._signer is None:  # nothing could be opened, so offer no links
            for item in items:
                item.pop("link", None)
        return items

    async def link(self, title: str) -> str:
        document = await asyncio.to_thread(self._index.get, title)
        if document is None:
            raise KeyError(title)
        if not document.linkable:
            raise LinkRefused(title)
        if self._signer is None:
            raise LinkRefused("signing is not configured")
        return await asyncio.to_thread(self._signer.sign, document.path)


# Azure AI Search ------------------------------------------------------------------------------

_SELECT = "title,source_path,linkable,doc_type,model_slug"


def _in(field: str, values: list[str]) -> str:
    """An OData search.in filter. Quotes are doubled; values holding the delimiter are left out
    by the callers, never sent."""
    safe = [v for v in values if "|" not in v]
    return f"search.in({field}, '{'|'.join(v.replace(chr(39), chr(39) * 2) for v in safe)}', '|')"


class SearchDocumentIndex:
    """Looks documents up by title in the chunk indexes. One query per index, deduplicated."""

    def __init__(self, client, index_names: tuple[str, ...] = INDEX_NAMES):
        self._client = client
        self._indexes = index_names

    def _query(self, index: str, filter_: str) -> list[Document]:
        _, payload = self._client.post(
            f"/indexes/{index}/docs/search",
            {"search": "*", "filter": filter_, "select": _SELECT, "top": 1000},
        )
        documents: dict[str, Document] = {}
        for hit in payload.get("value", []):
            title = hit.get("title")
            if title and title not in documents:
                documents[title] = Document(
                    title=title,
                    path=hit.get("source_path") or "",
                    linkable=hit.get("linkable") == "true",  # anything else is not linkable
                    doc_type=hit.get("doc_type") or "",
                    model_slug=hit.get("model_slug") or "",
                )
        return list(documents.values())

    def find(self, titles: list[str], models: list[str]) -> list[Document]:
        titles = [t for t in titles if "|" not in t]
        if not titles:
            return []
        found: dict[str, Document] = {}
        for index in self._indexes:
            filter_ = _in("title", titles)
            if index == DOCS_INDEX and models:
                filter_ = (f"{filter_} or ({_in('model_slug', models)} and "
                           f"{_in('doc_type', list(READ_MORE_TYPES))})")
            for d in self._query(index, filter_):
                found.setdefault(d.title, d)
        return list(found.values())

    def get(self, title: str) -> Document | None:
        if "|" in title:
            return None
        for index in self._indexes:
            for d in self._query(index, _in("title", [title])):
                if d.title == title:
                    return d
        return None


# Signing --------------------------------------------------------------------------------------

def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class LinkSigner:
    """User delegation SAS for one blob in an allowed container of one account."""

    def __init__(self, account_name: str, containers: set[str], service,
                 clock: Callable[[], dt.datetime] = _utc_now):
        self._account = account_name
        self._host = f"{account_name}.blob.core.windows.net"
        self._containers = containers
        self._service = service  # a BlobServiceClient signed in with the API's identity
        self._clock = clock
        self._key: tuple[object, dt.datetime] | None = None  # the key and when it expires
        self._lock = threading.Lock()  # sign runs in worker threads

    def _delegation_key(self, now: dt.datetime):
        with self._lock:
            if self._key is None or self._key[1] - now < KEY_REFRESH_MARGIN:
                start = now - dt.timedelta(minutes=5)
                expiry = now + dt.timedelta(hours=KEY_HOURS)
                key = self._service.get_user_delegation_key(key_start_time=start, key_expiry_time=expiry)
                self._key = (key, expiry)
            return self._key[0]

    def sign(self, path: str) -> str:
        from azure.storage.blob import BlobSasPermissions, generate_blob_sas

        url = urlparse(path)
        container, _, blob = url.path.lstrip("/").partition("/")
        if url.scheme != "https" or url.netloc != self._host:
            raise LinkRefused("another storage account")
        if container not in self._containers or not blob:
            raise LinkRefused("a container that is not linkable")
        blob = unquote(blob)
        now = self._clock()
        sas = generate_blob_sas(
            account_name=self._account,
            container_name=container,
            blob_name=blob,
            user_delegation_key=self._delegation_key(now),
            permission=BlobSasPermissions(read=True),
            start=now - dt.timedelta(minutes=5),
            expiry=now + dt.timedelta(seconds=LINK_SECONDS),
            content_disposition="inline",
        )
        return f"https://{self._host}/{container}/{quote(blob)}?{sas}"


def build_source_service(settings) -> SourceService | None:
    """From the environment. Without a search endpoint there are no sources; without a storage
    account, sources are listed but cannot be opened."""
    if not settings.search_endpoint:
        return None
    from azure.identity import DefaultAzureCredential
    from voltrieve_search.client import SearchRestClient

    credential = DefaultAzureCredential()
    client = SearchRestClient(
        settings.search_endpoint,
        lambda: credential.get_token("https://search.azure.com/.default").token,
        timeout=LOOKUP_TIMEOUT_SECONDS,  # a slow Search costs the links, not a worker thread
    )
    signer = None
    containers = {c.strip() for c in settings.source_containers.split(",") if c.strip()}
    if settings.storage_account_name and containers:
        from azure.storage.blob import BlobServiceClient

        service = BlobServiceClient(
            f"https://{settings.storage_account_name}.blob.core.windows.net", credential=credential
        )
        signer = LinkSigner(settings.storage_account_name, containers, service)
    return SourceService(SearchDocumentIndex(client), signer)
