# Installation

## Universal prerequisites

1. Install Python 3.10 or newer.
2. Install the package in editable mode from the repo root:

```bash
python -m pip install -e .
```

3. Configure GitHub authentication for CI fetching:

Preferred:

```bash
gh auth login
```

Fallback:

```bash
export GITHUB_TOKEN=your_token_here
```

The GitHub fetcher prefers `gh` and falls back to `GITHUB_TOKEN` when `gh` is not available.

## Codex

This repo includes a project-scoped MCP configuration in `.codex/config.toml`.

Steps:

1. Open the repository in Codex.
2. Ensure the project is trusted.
3. Run `/mcp` and confirm `ci-log-intelligence` is listed.

Codex will launch the server automatically using the installed `ci-log-intelligence-mcp` command.

## VS Code / GitHub Copilot

This repo includes a workspace MCP configuration in `.vscode/mcp.json`.

Steps:

1. Open the repository in VS Code.
2. Ensure GitHub Copilot agent mode and MCP support are enabled in your installation.
3. Open the chat or MCP tools view and confirm `ci-log-intelligence` is available.

VS Code will use the `ci-log-intelligence-mcp` command from your Python environment.

## Claude Desktop

Preferred organizational distribution is a Claude Desktop extension (`.mcpb`), but this repo also includes a manual local-server example at `docs/claude_desktop_config.example.json`.

Manual setup:

1. Install the package with `python -m pip install -e .`.
2. Copy the example configuration into your Claude Desktop local MCP configuration.
3. Restart Claude Desktop and confirm the server is available.

## Local usage

CLI:

```bash
ci-log-intel analyze --url https://github.com/owner/repo/pull/123 --include-passed
```

MCP server:

```bash
ci-log-intelligence-mcp
```
