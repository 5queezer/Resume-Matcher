"""Tests for MCP endpoint (JSON-RPC 2.0 over Streamable HTTP)."""
import json
import pytest


def _jsonrpc(method: str, params: dict | None = None, msg_id: int = 1) -> dict:
    msg = {"jsonrpc": "2.0", "method": method, "id": msg_id}
    if params:
        msg["params"] = params
    return msg


class TestMCPInitialize:
    @pytest.mark.anyio
    async def test_initialize_without_auth(self, client):
        resp = await client.post("/mcp", json=_jsonrpc("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0"},
        }))
        assert resp.status_code == 200
        data = resp.json()
        assert data["jsonrpc"] == "2.0"
        assert "result" in data
        result = data["result"]
        assert "serverInfo" in result
        assert "capabilities" in result
        assert result["capabilities"].get("tools") is not None

    @pytest.mark.anyio
    async def test_initialize_returns_session_id(self, client):
        resp = await client.post("/mcp", json=_jsonrpc("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0"},
        }))
        assert "mcp-session-id" in resp.headers


class TestMCPAuth:
    @pytest.mark.anyio
    async def test_tools_list_requires_auth(self, client):
        resp = await client.post("/mcp", json=_jsonrpc("tools/list"))
        assert resp.status_code == 200
        data = resp.json()
        assert "error" in data
        assert data["error"]["code"] == -32600

    @pytest.mark.anyio
    async def test_tools_list_with_auth(self, client, auth_headers_a):
        resp = await client.post("/mcp", json=_jsonrpc("tools/list"), headers=auth_headers_a)
        assert resp.status_code == 200
        data = resp.json()
        assert "result" in data
        tools = data["result"]["tools"]
        assert len(tools) > 0
        tool_names = [t["name"] for t in tools]
        assert "list_resumes" in tool_names
        assert "get_resume" in tool_names
        assert "get_status" in tool_names


class TestMCPToolCalls:
    @pytest.mark.anyio
    async def test_list_resumes_empty(self, client, auth_headers_a):
        resp = await client.post("/mcp", json=_jsonrpc("tools/call", {
            "name": "list_resumes",
            "arguments": {},
        }), headers=auth_headers_a)
        assert resp.status_code == 200
        data = resp.json()
        assert "result" in data
        content = data["result"]["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "text"

    @pytest.mark.anyio
    async def test_list_resumes_returns_user_data(self, client, auth_headers_a, auth_user_a, test_db, sample_resume):
        user_a, _ = auth_user_a
        await test_db.create_resume(
            content=json.dumps(sample_resume), user_id=user_a["id"], title="My Resume"
        )
        resp = await client.post("/mcp", json=_jsonrpc("tools/call", {
            "name": "list_resumes",
            "arguments": {},
        }), headers=auth_headers_a)
        data = resp.json()
        content = data["result"]["content"]
        assert "My Resume" in content[0]["text"]

    @pytest.mark.anyio
    async def test_tool_call_isolation(self, client, auth_headers_a, auth_headers_b, auth_user_a, auth_user_b, test_db, sample_resume):
        user_a, _ = auth_user_a
        await test_db.create_resume(
            content=json.dumps(sample_resume), user_id=user_a["id"], title="A's Resume"
        )
        resp = await client.post("/mcp", json=_jsonrpc("tools/call", {
            "name": "list_resumes",
            "arguments": {},
        }), headers=auth_headers_b)
        data = resp.json()
        content = data["result"]["content"]
        assert "A's Resume" not in content[0]["text"]

    @pytest.mark.anyio
    async def test_unknown_tool_returns_error(self, client, auth_headers_a):
        resp = await client.post("/mcp", json=_jsonrpc("tools/call", {
            "name": "nonexistent_tool",
            "arguments": {},
        }), headers=auth_headers_a)
        data = resp.json()
        assert "error" in data

    @pytest.mark.anyio
    async def test_get_status(self, client, auth_headers_a):
        resp = await client.post("/mcp", json=_jsonrpc("tools/call", {
            "name": "get_status",
            "arguments": {},
        }), headers=auth_headers_a)
        data = resp.json()
        assert "result" in data
        text = data["result"]["content"][0]["text"]
        stats = json.loads(text)
        assert "total_resumes" in stats
