import os
import json
import hashlib
from pathlib import Path

import pytest
from triage_sentinel.reference_mcp import FilesystemMCPReferenceClient

RUN_FILESYSTEM_MCP = os.getenv("TRIAGE_RUN_FILESYSTEM_MCP_INTEGRATION") == "1"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "approved_protocols"


@pytest.mark.anyio
@pytest.mark.skipif(
    not RUN_FILESYSTEM_MCP,
    reason="set TRIAGE_RUN_FILESYSTEM_MCP_INTEGRATION=1 after building the MCP image",
)
async def test_official_server_discovers_tools_and_retrieves_approved_document() -> None:
    client = FilesystemMCPReferenceClient(approved_directory=FIXTURES)

    tools = await client.discover_tools()
    result = await client.retrieve("TEST-SYNTHETIC-PROTOCOL")

    assert "read_text_file" in tools
    assert "list_allowed_directories" in tools
    assert result.audit.status == "succeeded"
    assert result.audit.server_identity.endswith(
        "#c003d0d807409b22fa640d1f8e11f38f15755721"
    )
    assert result.audit.tool_name == "read_text_file"
    assert result.audit.returned_version == "test-v1"
    assert result.document is not None
    assert result.document.document_class == "synthetic-test"
    assert result.document.content_sha256 == result.audit.content_sha256


@pytest.mark.anyio
@pytest.mark.skipif(not RUN_FILESYSTEM_MCP, reason="requires the built Filesystem MCP image")
async def test_filesystem_server_cannot_escape_root_or_write_read_only_mount() -> None:
    client = FilesystemMCPReferenceClient(approved_directory=FIXTURES)

    async with client._session() as session:
        outside = await session.call_tool(
            "read_text_file", {"path": "/etc/passwd"}
        )
        write = await session.call_tool(
            "write_file",
            {
                "path": "/protocols/should-not-exist.txt",
                "content": "must be blocked",
            },
        )

    assert outside.isError
    assert write.isError
    assert not (FIXTURES / "should-not-exist.txt").exists()


@pytest.mark.anyio
@pytest.mark.skipif(not RUN_FILESYSTEM_MCP, reason="requires the built Filesystem MCP image")
async def test_missing_and_malformed_protocol_metadata_fail_closed(tmp_path: Path) -> None:
    production_directory = (
        Path(__file__).resolve().parents[1]
        / "docs"
        / "clinical-protocols"
        / "approved"
    )
    missing = await FilesystemMCPReferenceClient(
        approved_directory=production_directory
    ).retrieve("IITT-ROUTINE")
    assert missing.document is None
    assert missing.audit.error_code == "PROTOCOL_DOCUMENT_NOT_FOUND"

    (tmp_path / "registry.json").write_text("not valid JSON", encoding="utf-8")
    malformed = await FilesystemMCPReferenceClient(
        approved_directory=tmp_path
    ).retrieve("TEST-SYNTHETIC-PROTOCOL")
    assert malformed.document is None
    assert malformed.audit.error_code == "PROTOCOL_REGISTRY_INVALID"


@pytest.mark.anyio
@pytest.mark.skipif(not RUN_FILESYSTEM_MCP, reason="requires the built Filesystem MCP image")
async def test_unapproved_and_checksum_mismatched_documents_are_rejected(
    tmp_path: Path,
) -> None:
    content = "Synthetic content, never clinical guidance."
    actual_digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    (tmp_path / "synthetic.md").write_text(content, encoding="utf-8")
    metadata = {
        "document_id": "TEST-SYNTHETIC-PROTOCOL",
        "version": "test-v1",
        "approval_status": "approved",
        "document_class": "synthetic-test",
        "file": "synthetic.md",
        "sha256": actual_digest,
        "source_url": "test-fixture://synthetic-protocol",
        "license": "Synthetic test fixture",
    }

    metadata["approval_status"] = "pending"
    (tmp_path / "registry.json").write_text(
        json.dumps({"schema_version": "1", "documents": [metadata]}),
        encoding="utf-8",
    )
    unapproved = await FilesystemMCPReferenceClient(
        approved_directory=tmp_path
    ).retrieve("TEST-SYNTHETIC-PROTOCOL")
    assert unapproved.document is None
    assert unapproved.audit.status == "failed"

    metadata["approval_status"] = "approved"
    metadata["sha256"] = "0" * 64
    (tmp_path / "registry.json").write_text(
        json.dumps({"schema_version": "1", "documents": [metadata]}),
        encoding="utf-8",
    )
    mismatched = await FilesystemMCPReferenceClient(
        approved_directory=tmp_path
    ).retrieve("TEST-SYNTHETIC-PROTOCOL")
    assert mismatched.document is None
    assert mismatched.audit.error_code == "PROTOCOL_CHECKSUM_MISMATCH"


@pytest.mark.anyio
@pytest.mark.skipif(not RUN_FILESYSTEM_MCP, reason="requires the built Filesystem MCP image")
async def test_unavailable_filesystem_server_returns_audited_failure() -> None:
    result = await FilesystemMCPReferenceClient(
        approved_directory=FIXTURES,
        docker_command="triage-docker-command-not-installed",
    ).retrieve("TEST-SYNTHETIC-PROTOCOL")

    assert result.document is None
    assert result.audit.status == "failed"
    assert result.audit.error_code == "FILESYSTEM_MCP_UNAVAILABLE"
