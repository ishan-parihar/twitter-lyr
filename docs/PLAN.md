# Twitter CLI Upgrade Plan

## Overview
Upgrade twitter-lyr (Python, GraphQL/cookie-based) to achieve feature parity with xurl (Go, OAuth/API v2) while maintaining twitter-lyr's superior GraphQL features.

## Current Status
- twitter-lyr v0.9.0 installed on VPS
- xurl repo cloned at `/home/ishanp/Documents/GitHub/CLONED-REPOS/xurl` for reference
- Comprehensive parity analysis complete (see `/tmp/parity_analysis.md`)

## ✅ Phase 1: Quick Wins (COMPLETED)

### 1.1 Media Status Command ✅
**Files**: `twitter_cli/client.py` + `twitter_cli/cli.py`
**Command**: `twitter media status <media_id>`
**Status**: Done - checks media upload processing status

### 1.2 Auth Status/Clear Commands ✅
**Files**: `twitter_cli/auth.py` + `twitter_cli/cli.py`
**Commands**: 
- `twitter auth status` - Check authentication status and show current user
- `twitter auth clear` - Clear stored environment variables
**Status**: Done

### 1.3 Streaming Support (GraphQL-based)
**Decision**: Not feasible with GraphQL; polling with cursors is alternative

## ✅ Phase 2: DM Enhancements (COMPLETED)

### 2.3 DM Mark Read ✅
**Command**: `twitter dm mark-read <conversation_id>`
**GraphQL**: `MarkDMConversationRead` mutation

### 2.4 DM Typing Indicator ✅
**Command**: `twitter dm typing <conversation_id>`
**GraphQL**: `SendDMTypingIndicator` mutation

### 2.5 DM Rotate Keys ✅
**Command**: `twitter dm rotate-keys <conversation_id>`
**GraphQL**: `RotateDMEncryptionKeys` mutation

## ✅ Phase 3: OAuth Support (COMPLETED - MAJOR)

### 3.1 Architecture Changes ✅
- Created `twitter_cli/oauth.py` module
- Supports three auth modes: cookies (current), OAuth1, OAuth2 PKCE, App-only
- Tokens stored in `~/.twitter-lyr/tokens.json`

### 3.2 OAuth1 Flow (User Context) ✅
**Command**: `twitter auth login --oauth1`
**Env**: `TWITTER_OAUTH1_CONSUMER_KEY`, `TWITTER_OAUTH1_CONSUMER_SECRET`

### 3.3 OAuth2 Flow (User Context with PKCE) ✅
**Command**: `twitter auth login --oauth2`
**Env**: `TWITTER_OAUTH2_CLIENT_ID`, `TWITTER_OAUTH2_CLIENT_SECRET`
**Features**: PKCE, refresh tokens, scope support

### 3.4 App-Only Auth (Bearer Token) ✅
**Command**: `twitter auth login --app-only`
**Env**: `TWITTER_OAUTH2_CLIENT_ID`, `TWITTER_OAUTH2_CLIENT_SECRET`

### 3.5 Token Management CLI ✅
- `twitter auth login --oauth1` / `--oauth2` / `--app-only`
- `twitter auth status` - Check auth status
- `twitter auth refresh <refresh_token>` - Refresh OAuth2 token
- `twitter auth clear` - Clear environment variables

## ⏳ Phase 4: MCP Server (NEXT)

### 4.1 FastMCP Integration
- Add `twitter_cli/mcp_server.py`
- Expose read operations as MCP resources/tools
- Expose write operations as MCP tools with confirmation

### 4.2 MCP Tools Mapping
| twitter-lyr command | MCP Tool |
|---------------------|----------|
| feed | get_timeline |
| search | search_tweets |
| user | get_user |
| tweet | get_tweet |
| post | create_tweet |
| reply | reply_tweet |
| like | like_tweet |
| retweet | retweet |
| bookmark | bookmark_tweet |
| follow | follow_user |
| dm conversations | list_dm_conversations |
| dm messages | get_dm_messages |
| dm send | send_dm |

### 4.3 CLI Entry Point
- `twitter mcp` - Start MCP server (stdio)
- `twitter mcp --http` - Start HTTP MCP server

## ⏳ Phase 5: Advanced Features

### 5.1 Webhook Management
- If Twitter API v2 supports: register/unregister/list webhooks

### 5.2 Default Profile Management
- `twitter profile set-default <name>`
- `twitter profile list`

### 5.3 Chat Key Management
- `twitter dm keys export`
- `twitter dm keys import`
- `twitter dm keys status`
- `twitter dm keys restore`

## Implementation Summary

### Files Added/Modified

| File | Changes |
|------|---------|
| `twitter_cli/client.py` | Added `check_media_status()`, `mark_dm_conversation_read()`, `send_dm_typing_indicator()`, `rotate_dm_encryption_keys()` |
| `twitter_cli/graphql.py` | Added query IDs for new DM mutations |
| `twitter_cli/auth.py` | Added `auth_status()`, `auth_clear()` functions |
| `twitter_cli/oauth.py` | **NEW** - Complete OAuth implementation (OAuth1, OAuth2 PKCE, App-Only) |
| `twitter_cli/cli.py` | Added auth group commands (login, status, clear, refresh), media group (status), dm commands (mark-read, typing, rotate-keys) |
| `pyproject.toml` | Added `requests-oauthlib` dependency |

### New Commands Added

```
auth
  ├─ login    --oauth1 | --oauth2 | --app-only
  ├─ status
  ├─ clear
  └─ refresh  <refresh_token>

media
  └─ status   <media_id>

dm
  ├─ mark-read    <conversation_id>
  ├─ typing       <conversation_id>
  └─ rotate-keys  <conversation_id>
```

### Dependencies Added
```
requests-oauthlib>=1.3.0
oauthlib>=3.3.0
```

## Testing

- ✅ All 242 existing tests pass
- ✅ VPS deployment verified
- ✅ All new commands appear in help
- ✅ OAuth module imports correctly

## VPS Deployment

- Repository: https://github.com/ishan-parihar/twitter-lyr
- VPS: Updated to latest (0.9.0) with all new features
- Command: `twitter --help` shows all commands

## Next Steps (Phase 4)

1. Implement MCP server (`twitter_cli/mcp_server.py`)
2. Add `twitter mcp` command to CLI
3. Test with Claude Desktop / MCP clients
4. Deploy to VPS

## Notes

- twitter-lyr's GraphQL approach provides MORE features than xurl's REST v2
- Focus on adding xurl's auth flexibility and MCP, not duplicating all REST endpoints
- Some xurl features (streaming, webhooks) may not be feasible with GraphQL
- Document any intentional gaps in README