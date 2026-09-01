import logging

import pytest
from fastmcp import Client
from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken

import librenms_mcp.access_log
from librenms_mcp.access_log import AccessLogMiddleware


def make_server() -> FastMCP:
    server = FastMCP("test")

    @server.tool
    def hello() -> str:
        return "hi"

    @server.tool
    def boom() -> str:
        raise ValueError("nope")

    server.add_middleware(AccessLogMiddleware())
    return server


@pytest.mark.asyncio
async def test_access_log_names_the_authenticated_client(caplog, monkeypatch):
    monkeypatch.setattr(
        librenms_mcp.access_log,
        "get_access_token",
        lambda: AccessToken(token="t", client_id="poste-alice", scopes=[]),
    )
    with caplog.at_level(logging.INFO, logger="librenms_mcp.access_log"):
        async with Client(make_server()) as client:
            await client.call_tool("hello", {})

    messages = [r.getMessage() for r in caplog.records]
    assert any(
        m.startswith("client=poste-alice tool=hello outcome=ok") for m in messages
    )


@pytest.mark.asyncio
async def test_access_log_falls_back_to_local_without_auth(caplog):
    with caplog.at_level(logging.INFO, logger="librenms_mcp.access_log"):
        async with Client(make_server()) as client:
            await client.call_tool("hello", {})

    messages = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("client=local tool=hello outcome=ok") for m in messages)


@pytest.mark.asyncio
async def test_access_log_records_errors(caplog):
    with caplog.at_level(logging.INFO, logger="librenms_mcp.access_log"):
        async with Client(make_server()) as client:
            with pytest.raises(Exception, match="nope"):
                await client.call_tool("boom", {})

    messages = [r.getMessage() for r in caplog.records]
    assert any(m.startswith("client=local tool=boom outcome=error") for m in messages)
