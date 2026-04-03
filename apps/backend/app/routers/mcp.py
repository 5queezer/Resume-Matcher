"""MCP (Model Context Protocol) endpoint — JSON-RPC 2.0 over Streamable HTTP."""

import json
import logging
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.auth.jwt import verify_access_token
from app.database import db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mcp"])

_PROTOCOL_VERSION = "2025-06-18"
_SERVER_NAME = "resume-matcher"
_SERVER_VERSION = "1.0.0"

# -- Tool definitions ---------------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_resumes",
        "description": "List all resumes belonging to the authenticated user.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_resume",
        "description": "Get a specific resume by ID, including its processed content.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "resume_id": {"type": "string", "description": "The resume UUID"},
            },
            "required": ["resume_id"],
        },
    },
    {
        "name": "get_status",
        "description": "Get dashboard stats: resume count, job count, improvements count.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "upload_job_description",
        "description": "Upload a job description text. Optionally link to a resume.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "The job description text"},
                "resume_id": {"type": "string", "description": "Optional resume ID to link"},
            },
            "required": ["content"],
        },
    },
    {
        "name": "set_master_resume",
        "description": "Set a resume as the master (source of truth) resume.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "resume_id": {"type": "string", "description": "The resume UUID to set as master"},
            },
            "required": ["resume_id"],
        },
    },
]


# -- Tool handlers ------------------------------------------------------------

async def _tool_list_resumes(user: dict, _args: dict) -> str:
    resumes = await db.list_resumes(user["id"])
    if not resumes:
        return "No resumes found."
    lines = []
    for r in resumes:
        master = " [MASTER]" if r.get("is_master") else ""
        title = r.get("title") or r.get("filename") or "Untitled"
        lines.append(f"- {title} (id: {r['resume_id']}){master}")
    return "\n".join(lines)


async def _tool_get_resume(user: dict, args: dict) -> str:
    resume_id = args.get("resume_id")
    if not resume_id:
        return "Error: resume_id is required"
    resume = await db.get_resume(resume_id, user["id"])
    if not resume:
        return "Resume not found."
    return json.dumps(resume, indent=2, default=str)


async def _tool_get_status(user: dict, _args: dict) -> str:
    stats = await db.get_stats(user["id"])
    return json.dumps(stats, indent=2)


async def _tool_upload_job(user: dict, args: dict) -> str:
    content = args.get("content")
    if not content:
        return "Error: content is required"
    resume_id = args.get("resume_id")
    if resume_id:
        resume = await db.get_resume(resume_id, user["id"])
        if not resume:
            return "Error: resume not found"
    job = await db.create_job(content=content, user_id=user["id"], resume_id=resume_id)
    return f"Job created: {job['job_id']}"


async def _tool_set_master(user: dict, args: dict) -> str:
    resume_id = args.get("resume_id")
    if not resume_id:
        return "Error: resume_id is required"
    ok = await db.set_master_resume(resume_id, user["id"])
    if ok:
        return f"Resume {resume_id} set as master."
    return "Error: resume not found."


_TOOL_HANDLERS: dict[str, Any] = {
    "list_resumes": _tool_list_resumes,
    "get_resume": _tool_get_resume,
    "get_status": _tool_get_status,
    "upload_job_description": _tool_upload_job,
    "set_master_resume": _tool_set_master,
}


# -- JSON-RPC helpers ---------------------------------------------------------

def _jsonrpc_result(msg_id: int | str | None, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _jsonrpc_error(msg_id: int | str | None, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


# -- Auth helper --------------------------------------------------------------

async def _resolve_user(request: Request) -> dict | None:
    """Extract Bearer token and resolve user. Returns None if not authenticated."""
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        return None
    token = auth_header[7:]
    try:
        claims = verify_access_token(token)
    except ValueError:
        return None
    return await db.get_user_by_id(claims["sub"])


# -- Main handler -------------------------------------------------------------

@router.post("/mcp")
async def mcp_handler(request: Request) -> JSONResponse:
    """MCP Streamable HTTP endpoint (JSON-RPC 2.0)."""
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse(
            content=_jsonrpc_error(None, -32700, "Parse error"),
            status_code=200,
        )

    if isinstance(body, list):
        return JSONResponse(
            content=_jsonrpc_error(None, -32600, "Batch requests not supported"),
            status_code=200,
        )
    if not isinstance(body, dict):
        return JSONResponse(
            content=_jsonrpc_error(None, -32600, "Invalid Request"),
            status_code=200,
        )

    method = body.get("method")
    msg_id = body.get("id")
    params = body.get("params") or {}
    if not isinstance(params, dict):
        return JSONResponse(
            content=_jsonrpc_error(msg_id, -32600, "Invalid Request"),
            status_code=200,
        )

    # -- initialize (no auth required) --
    if method == "initialize":
        result = {
            "protocolVersion": _PROTOCOL_VERSION,
            "serverInfo": {"name": _SERVER_NAME, "version": _SERVER_VERSION},
            "capabilities": {"tools": {"listChanged": False}},
        }
        session_id = str(uuid4())
        return JSONResponse(
            content=_jsonrpc_result(msg_id, result),
            headers={"mcp-session-id": session_id},
        )

    # -- notifications (no response) --
    if method == "notifications/initialized":
        return JSONResponse(content=None, status_code=204)

    # -- Auth required for all other methods --
    user = await _resolve_user(request)
    if not user:
        return JSONResponse(
            content=_jsonrpc_error(msg_id, -32600, "Unauthorized — Bearer token required"),
            status_code=200,
            headers={"WWW-Authenticate": "Bearer"},
        )

    # -- tools/list --
    if method == "tools/list":
        return JSONResponse(content=_jsonrpc_result(msg_id, {"tools": TOOLS}))

    # -- tools/call --
    if method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments") or {}
        if not isinstance(tool_args, dict):
            return JSONResponse(
                content=_jsonrpc_error(msg_id, -32602, "Invalid params"),
            )

        handler = _TOOL_HANDLERS.get(tool_name)
        if not handler:
            return JSONResponse(
                content=_jsonrpc_error(msg_id, -32602, f"Unknown tool: {tool_name}"),
            )

        try:
            text_result = await handler(user, tool_args)
        except Exception:
            logger.exception("MCP tool %s failed", tool_name)
            return JSONResponse(
                content=_jsonrpc_error(msg_id, -32603, "Tool execution failed"),
            )

        return JSONResponse(content=_jsonrpc_result(msg_id, {
            "content": [{"type": "text", "text": text_result}],
        }))

    return JSONResponse(content=_jsonrpc_error(msg_id, -32601, f"Method not found: {method}"))
