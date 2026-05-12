# Installation

This guide walks through installing `ci-log-intelligence` and registering its MCP server with the most common AI coding clients.

## 1. Universal prerequisites

1. **Install Python 3.10 or newer.**

2. **Install the package:**

   ```bash
   pip install ci-log-intelligence
   ```

   Or from a clone:

   ```bash
   git clone https://github.com/kuldeep0020/ci-log-intelligence.git
   cd ci-log-intelligence
   pip install -e .
   ```

   This puts a `ci-log-intelligence-mcp` executable on your `PATH` — that's the command every MCP client below will be pointed at.

3. **Configure GitHub authentication** (so the server can fetch CI logs from your repos):

   Preferred — use the `gh` CLI:

   ```bash
   gh auth login
   ```

   Fallback — set a personal access token:

   ```bash
   export GITHUB_TOKEN=ghp_…
   ```

   The fetcher tries `gh` first, then falls back to `GITHUB_TOKEN`.

4. **Verify the server starts:**

   ```bash
   ci-log-intelligence-mcp --help
   ```

   You should see the stdio/http transport options. If you get "command not found", your `pip` install location isn't on `PATH` — fix that before continuing.

---

## 2. Claude Code (CLI)

[Claude Code](https://github.com/anthropics/claude-code) configures MCP servers through the `claude mcp` subcommands.

### Add the server (user-wide, available in all projects)

```bash
claude mcp add ci-log-intelligence --scope user -- ci-log-intelligence-mcp
```

The `--` separates the `claude mcp add` flags from the command Claude Code will spawn — `ci-log-intelligence-mcp` in this case. The `--scope user` flag makes it available in every project; drop it (or use `--scope project`) if you want a single-project setup.

If your repo uses a personal access token rather than `gh` auth, attach it as an environment variable:

```bash
claude mcp add ci-log-intelligence \
  --scope user \
  -e GITHUB_TOKEN=ghp_… \
  -- ci-log-intelligence-mcp
```

### Verify

```bash
claude mcp list
```

You should see `ci-log-intelligence` in the output. To inspect what got configured:

```bash
claude mcp get ci-log-intelligence
```

### Use it

Start a Claude Code session (`claude` in the terminal) and ask it to use the tool:

> "There's a failing CI run at https://github.com/owner/repo/actions/runs/12345 — can you debug it?"

Claude Code will discover the three exposed tools (`list_failed_jobs`, `analyze_ci_failure`, `get_block`) and call them as needed.

### Remove it

```bash
claude mcp remove ci-log-intelligence
```

---

## 3. Claude Desktop

Claude Desktop reads MCP server configuration from a JSON file. The file's location depends on your OS:

| OS | Path |
|---|---|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |
| Linux | `~/.config/Claude/claude_desktop_config.json` |

### Step-by-step

1. Open (or create) the config file in your editor of choice.

2. Merge the contents of [`docs/claude_desktop_config.example.json`](docs/claude_desktop_config.example.json) into the `mcpServers` object — the example looks like this:

   ```json
   {
     "mcpServers": {
       "ci-log-intelligence": {
         "command": "ci-log-intelligence-mcp",
         "args": []
       }
     }
   }
   ```

   If you already have other MCP servers configured, add the `"ci-log-intelligence"` key alongside them — don't replace the whole `mcpServers` object.

3. If you're using a personal access token rather than `gh` auth, add an `env` block:

   ```json
   {
     "mcpServers": {
       "ci-log-intelligence": {
         "command": "ci-log-intelligence-mcp",
         "args": [],
         "env": {
           "GITHUB_TOKEN": "ghp_…"
         }
       }
     }
   }
   ```

4. **Fully quit and relaunch** Claude Desktop (not just close the window — quit the app entirely so it reloads the config).

5. Confirm the server appears in the MCP settings panel inside Claude Desktop.

### Troubleshooting "command not found"

Claude Desktop spawns the MCP server with the GUI's `PATH`, which on macOS often does NOT include user-installed Python packages. If you see "command not found":

- Find the absolute path to the executable: `which ci-log-intelligence-mcp`
- Use that absolute path in the config:

  ```json
  "command": "/Users/you/.local/bin/ci-log-intelligence-mcp"
  ```

---

## 4. Codex

This repo includes a project-scoped MCP configuration in `.codex/config.toml`.

1. Open the repository in Codex.
2. Ensure the project is trusted.
3. Run `/mcp` and confirm `ci-log-intelligence` is listed.

Codex will launch the server automatically using the installed `ci-log-intelligence-mcp` command.

For a user-wide setup outside this repo, follow Codex's documentation for adding a custom MCP server pointed at the `ci-log-intelligence-mcp` command.

---

## 5. VS Code / GitHub Copilot

This repo includes a workspace-scoped MCP configuration in `.vscode/mcp.json`.

1. Open the repository in VS Code.
2. Ensure GitHub Copilot agent mode and MCP support are enabled in your installation.
3. Open the chat or MCP tools view and confirm `ci-log-intelligence` is available.

VS Code will use the `ci-log-intelligence-mcp` command from your Python environment.

For a user-wide setup, add the server to your global VS Code MCP configuration following [VS Code's MCP docs](https://code.visualstudio.com/docs/copilot/customization/mcp-servers).

---

## 6. Other MCP clients

Any MCP-aware client that supports stdio servers can use this tool. Point the client at the `ci-log-intelligence-mcp` command:

```json
{
  "command": "ci-log-intelligence-mcp",
  "args": [],
  "env": {
    "GITHUB_TOKEN": "ghp_…"
  }
}
```

The `env` block is only needed if you're not using `gh auth login`.

---

## 7. HTTP transport (alternative)

If your client only supports HTTP MCP servers, run:

```bash
ci-log-intelligence-mcp --transport http --host 127.0.0.1 --port 8001
```

And point the client at `http://127.0.0.1:8001`.

---

## CLI usage (no MCP client needed)

You can also use the tool directly without any MCP client.

### From a GitHub URL

```bash
ci-log-intel analyze \
  --url https://github.com/owner/repo/pull/123 \
  --include-passed
```

Or for machine-readable output:

```bash
ci-log-intel analyze \
  --url https://github.com/owner/repo/actions/runs/12345 \
  --json
```

### From a local log file

The detector pipeline also runs on any plain log file — Jenkins, Buildkite, local builds, `kubectl logs` output, anything. Use `--file <path>` or `--file -` for stdin:

```bash
ci-log-intel analyze --file ./build.log
kubectl logs my-pod --tail=50000 | ci-log-intel analyze --file -
```

The output is the same ranked failure blocks and typed detected-failure records you get from a GitHub URL, minus the cross-run comparison (which needs failed+passed runs from the same workflow).

---

## Progress notifications

The MCP server emits standard MCP progress notifications during long fetches (`Resolving CI URL` → `Fetching log for <job> (i/N)` → `Done`). Whether you see them depends on your client:

| Client | Status |
|---|---|
| `ci-log-intel` CLI | Progress prints directly to stderr — always visible |
| Codex CLI | Sends `progressToken` in tool requests — progress renders |
| Claude Code (CLI) | Does NOT send `progressToken` as of this writing — progress is silently dropped by the MCP SDK |
| Claude Desktop | Same as Claude Code — no `progressToken` sent |

This is an MCP-spec behavior: the server may only emit progress notifications when the client opts in by sending `_meta.progressToken` with the tool call. The tools still work correctly in every client — only the live progress bar is missing.

**Workaround:** use the CLI when you want a visible progress bar:

```bash
ci-log-intel analyze --url https://github.com/owner/repo/pull/123
```

### Diagnosing progress in your client

If you're integrating a new MCP client and want to know whether it's sending `progressToken`, set the diagnostic env var when registering the server:

```bash
claude mcp add ci-log-intelligence \
  --scope user \
  -e CI_LOG_INTEL_PROGRESS_DEBUG=1 \
  -- ci-log-intelligence-mcp
```

(or the equivalent in your client's config). The server will then print to stderr on every tool call:

```text
[ci-log-intel] progress: ctx present, progressToken=1            <- client opted in
[ci-log-intel] progress: emit current=0 total=100 msg='...'      <- server emitted
```

vs.

```text
[ci-log-intel] progress: ctx present, progressToken=None         <- client did NOT opt in
```

Run `claude --mcp-debug` (or your client's equivalent) to surface stderr from the MCP subprocess.
