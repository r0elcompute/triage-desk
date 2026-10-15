import asyncio
import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from triage_tools_mcp.schemas import ProtocolReferenceInput

FILESYSTEM_SERVER_ID = (
    "@modelcontextprotocol/server-filesystem@1.0.0#c003d0d807409b22fa640d1f8e11f38f15755721"
)
FILESYSTEM_IMAGE_DEFAULT = "triage-filesystem-mcp:1.0.0"
FILESYSTEM_ROOT = "/protocols"
REGISTRY_FILE = "registry.json"


class ReferenceRegistryDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: Annotated[
        str,
        StringConstraints(
            min_length=1,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        ),
    ]
    version: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    approval_status: Literal["approved"]
    document_class: Literal["clinical", "synthetic-test"]
    file: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    source_url: Annotated[str, StringConstraints(min_length=1, max_length=2048)]
    license: Annotated[str, StringConstraints(min_length=1, max_length=512)]


class ReferenceRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"]
    documents: list[ReferenceRegistryDocument]
    notice: str | None = None


class ReferenceRetrievalAudit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server_identity: str = FILESYSTEM_SERVER_ID
    tool_name: Literal["read_text_file"] = "read_text_file"
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    requested_document_id: str
    returned_version: str | None = None
    content_sha256: str | None = None
    status: Literal["succeeded", "failed"]
    error_code: str | None = None


class ReferenceRetrievalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document: ProtocolReferenceInput | None
    audit: ReferenceRetrievalAudit


class ReferenceRetrievalError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class FilesystemMCPReferenceClient:
    def __init__(
        self,
        *,
        approved_directory: str | Path,
        image: str = FILESYSTEM_IMAGE_DEFAULT,
        docker_command: str = "docker",
        timeout_seconds: float = 20,
    ) -> None:
        self.approved_directory = Path(approved_directory).resolve()
        self.image = image
        self.docker_command = docker_command
        self.timeout_seconds = timeout_seconds

    async def discover_tools(self) -> list[str]:
        async with self._session() as session:
            tools = await session.list_tools()
            return [tool.name for tool in tools.tools]

    async def retrieve(self, document_id: str) -> ReferenceRetrievalResult:
        audit_values: dict[str, object] = {
            "requested_document_id": document_id,
            "status": "failed",
        }
        try:
            document = await self._retrieve(document_id)
            audit_values.update(
                {
                    "returned_version": document.version,
                    "content_sha256": document.content_sha256,
                    "status": "succeeded",
                }
            )
            return ReferenceRetrievalResult(
                document=document,
                audit=ReferenceRetrievalAudit(**audit_values),
            )
        except ReferenceRetrievalError as error:
            audit_values["error_code"] = error.code
        except BaseExceptionGroup as error:
            audit_values["error_code"] = (
                _find_reference_error(error) or "FILESYSTEM_MCP_UNAVAILABLE"
            )
        except TimeoutError:
            audit_values["error_code"] = "FILESYSTEM_MCP_TIMEOUT"
        except Exception:
            audit_values["error_code"] = "FILESYSTEM_MCP_UNAVAILABLE"
        return ReferenceRetrievalResult(
            document=None,
            audit=ReferenceRetrievalAudit(**audit_values),
        )

    async def _retrieve(self, document_id: str) -> ProtocolReferenceInput:
        async with asyncio.timeout(self.timeout_seconds):
            async with self._session() as session:
                tools = await session.list_tools()
                tool_names = {tool.name for tool in tools.tools}
                if not {"read_text_file", "list_allowed_directories"} <= tool_names:
                    raise ReferenceRetrievalError("FILESYSTEM_TOOLS_MISSING")

                allowed = _text_result(
                    await session.call_tool("list_allowed_directories", {})
                )
                allowed_paths = {
                    line.strip()
                    for line in allowed.splitlines()
                    if line.strip().startswith("/")
                }
                if allowed_paths != {FILESYSTEM_ROOT}:
                    raise ReferenceRetrievalError("FILESYSTEM_ROOT_MISMATCH")

                registry_text = _text_result(
                    await session.call_tool(
                        "read_text_file",
                        {"path": f"{FILESYSTEM_ROOT}/{REGISTRY_FILE}"},
                    )
                )
                try:
                    registry = ReferenceRegistry.model_validate_json(registry_text)
                except Exception as error:
                    raise ReferenceRetrievalError(
                        "PROTOCOL_REGISTRY_INVALID"
                    ) from error
                matches = [
                    document
                    for document in registry.documents
                    if document.document_id == document_id
                ]
                if len(matches) != 1:
                    raise ReferenceRetrievalError("PROTOCOL_DOCUMENT_NOT_FOUND")
                metadata = matches[0]
                path = _safe_document_path(metadata.file)
                content = _text_result(
                    await session.call_tool(
                        "read_text_file",
                        {"path": f"{FILESYSTEM_ROOT}/{path.as_posix()}"},
                    )
                )
                digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                if digest != metadata.sha256:
                    raise ReferenceRetrievalError("PROTOCOL_CHECKSUM_MISMATCH")
                return ProtocolReferenceInput(
                    protocol_id=metadata.document_id,
                    version=metadata.version,
                    approval_status=metadata.approval_status,
                    document_class=metadata.document_class,
                    source_url=metadata.source_url,
                    license=metadata.license,
                    content_sha256=digest,
                    content=content,
                )

    @asynccontextmanager
    async def _session(self) -> AsyncIterator[ClientSession]:
        if not self.approved_directory.is_dir():
            raise ReferenceRetrievalError("PROTOCOL_DIRECTORY_UNAVAILABLE")
        mount = (
            f"type=bind,src={self.approved_directory},dst={FILESYSTEM_ROOT},readonly"
        )
        params = StdioServerParameters(
            command=self.docker_command,
            args=[
                "run",
                "--rm",
                "-i",
                "--network=none",
                "--read-only",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges:true",
                "--pids-limit=32",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=16m",
                "--mount",
                mount,
                self.image,
                FILESYSTEM_ROOT,
            ],
        )
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                yield session


def _safe_document_path(filename: str) -> PurePosixPath:
    path = PurePosixPath(filename)
    if path.is_absolute() or not path.parts or any(
        part in {".", ".."} for part in path.parts
    ):
        raise ReferenceRetrievalError("PROTOCOL_PATH_INVALID")
    if path.as_posix() == REGISTRY_FILE:
        raise ReferenceRetrievalError("PROTOCOL_PATH_INVALID")
    return path


def _text_result(result) -> str:
    if result.isError:
        raise ReferenceRetrievalError("FILESYSTEM_TOOL_FAILED")
    if result.structuredContent is not None:
        content = result.structuredContent
        if isinstance(content, dict) and isinstance(content.get("content"), str):
            return content["content"]
        raise ReferenceRetrievalError("FILESYSTEM_RESULT_INVALID")
    blocks = [block.text for block in result.content if getattr(block, "text", None)]
    if len(blocks) != 1:
        raise ReferenceRetrievalError("FILESYSTEM_RESULT_INVALID")
    return blocks[0]


def _find_reference_error(error: BaseExceptionGroup) -> str | None:
    for nested in error.exceptions:
        if isinstance(nested, ReferenceRetrievalError):
            return nested.code
        if isinstance(nested, BaseExceptionGroup):
            code = _find_reference_error(nested)
            if code is not None:
                return code
    return None
