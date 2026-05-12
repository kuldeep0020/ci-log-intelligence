---
name: progress-token-client-gap
description: "Claude Code's MCP client doesn't send progressToken, so FastMCP silently drops progress notifications — server-side code is correct"
metadata: 
  node_type: memory
  type: project
  originSessionId: fb52fcd1-253a-41d3-9003-f16cd6d448c9
---

The MCP server emits standard progress notifications during long fetches, but they don't render in Claude Code or Claude Desktop.

**Root cause:** the MCP spec requires the *client* to send `_meta.progressToken` in the tool-call request. FastMCP's `Context.report_progress` short-circuits with `if progress_token is None: return` (see `mcp/server/fastmcp/server.py` in the installed mcp package). Claude Code's MCP client does not send `progressToken`, so every progress event is dropped before going over the wire. Codex CLI and the official mcp Python client (with `progress_callback=`) both work correctly.

**Why:** verified end-to-end on 2026-05-12 by spawning the MCP server via `stdio_client` with an explicit `progress_callback` and watching all progress events arrive at the callback. The server code in `ci_log_intelligence/mcp/server.py::_make_progress_bridge` is correct — the gap is on Claude Code's side.

**How to apply:**
- Don't keep "fixing" the server-side progress code; it works.
- Set `CI_LOG_INTEL_PROGRESS_DEBUG=1` on the MCP server's environment to log `progressToken` state to stderr when diagnosing a new client. The bridge logs `progressToken=None` when the client didn't opt in, or the actual token when it did.
- The CLI (`ci-log-intel analyze ...`) always shows progress because it calls the callback directly, bypassing the MCP protocol. Recommend the CLI as a workaround for users who want visible progress.
- If Claude Code adds `progress_callback` support in the future, progress will start rendering automatically with no server changes.
