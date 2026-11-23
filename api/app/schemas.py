import json
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, Field, model_validator

from voltrieve_agent.tools import ClientTool

from app.context import ClientContext

ID_PATTERN = r"^[A-Za-z0-9._-]{1,128}$"
MAX_CLIENT_TOOLS = 16
MAX_CLIENT_TOOLS_BYTES = 16 * 1024
MAX_ATTACHMENTS = 3  # in one request


def _unique(ids: list[str]) -> list[str]:
    if len(set(ids)) != len(ids):
        raise ValueError("an attachment may be named once")
    return ids


AttachmentIds = Annotated[
    list[Annotated[str, Field(pattern=ID_PATTERN)]],
    Field(max_length=MAX_ATTACHMENTS),
    AfterValidator(_unique),
]


class ClientToolModel(BaseModel):
    name: str = Field(pattern=r"^[a-zA-Z0-9_]{1,64}$")
    description: str = Field(max_length=2000)
    parameters: dict[str, Any]


class ChatRequest(BaseModel):
    conversation_id: str | None = Field(default=None, pattern=ID_PATTERN)
    message: str = Field(min_length=1, max_length=8000)
    client_tools: list[ClientToolModel] = Field(default_factory=list, max_length=MAX_CLIENT_TOOLS)
    context: ClientContext | None = None
    attachments: AttachmentIds = Field(default_factory=list)


class ToolResult(BaseModel):
    call_id: str = Field(pattern=ID_PATTERN)
    output: Any = ""
    attachments: AttachmentIds = Field(default_factory=list)

    def output_text(self) -> str:
        return self.output if isinstance(self.output, str) else json.dumps(self.output)


class ToolResultsRequest(BaseModel):
    conversation_id: str = Field(pattern=ID_PATTERN)
    turn_id: str = Field(pattern=ID_PATTERN)
    results: list[ToolResult] = Field(min_length=1, max_length=64)

    def attachment_ids(self) -> list[str]:
        return [id for result in self.results for id in result.attachments]

    @model_validator(mode="after")
    def _limit_attachments(self) -> "ToolResultsRequest":
        if len(_unique(self.attachment_ids())) > MAX_ATTACHMENTS:
            raise ValueError(f"at most {MAX_ATTACHMENTS} attachments in one request")
        return self


def to_client_tools(models: list[ClientToolModel]) -> list[ClientTool]:
    return [ClientTool(m.name, m.description, m.parameters) for m in models]
