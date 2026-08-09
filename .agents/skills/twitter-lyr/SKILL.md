---
name: twitter-lyr
description: Twitter/X automation with timeline reading, search, posting, and engagement features. Use this skill whenever the user requests Twitter/X operations, social media posting, content creation, or any Twitter-related tasks. Triggers on phrases like "twitter post", "twitter search", "twitter timeline", "twitter automation", "social media posting", "content creation", "x twitter", "tweet about", "post on twitter", "search twitter", or any request for Twitter/X functionality.
---

# Twitter-lyr Skill

This skill enables AI agents to interact with Twitter/X using the twitter-lyr CLI tool. It provides comprehensive Twitter/X automation including timeline reading, search, posting, and engagement features.

## Prerequisites

- twitter-lyr CLI must be installed globally on the system
- The CLI must be accessible in the system PATH
- Browser cookies may be required for authenticated operations

## Command Structure

### Available Commands

```bash
# Reading commands
twitter-lyr feed                    # Home timeline (For You)
twitter-lyr feed -t following      # Following feed
twitter-lyr bookmarks              # Bookmarks
twitter-lyr search "query"         # Search tweets
twitter-lyr user <handle>          # User profile
twitter-lyr user-posts <handle>    # User tweets
twitter-lyr tweet <id>             # Tweet detail + replies
twitter-lyr list <id>             # List timeline

# Writing commands
twitter-lyr post "text"            # Post a tweet
twitter-lyr post "text" -i photo.jpg  # Post with image(s)
twitter-lyr reply <id> "text"     # Reply to a tweet
twitter-lyr quote <id> "text"     # Quote-tweet
twitter-lyr delete <id>           # Delete a tweet

# Engagement commands
twitter-lyr like <id>              # Like a tweet
twitter-lyr unlike <id>            # Unlike a tweet
twitter-lyr retweet <id>           # Retweet
twitter-lyr unretweet <id>         # Unretweet
twitter-lyr follow <handle>        # Follow a user
twitter-lyr unfollow <handle>      # Unfollow a user
```

## Output Formats

The CLI supports multiple output formats for different use cases:

```bash
# TOON format (default, token-efficient)
twitter-lyr feed --format toon

# JSON format
twitter-lyr feed --format json

# YAML format
twitter-lyr feed --format yaml

# Rich table format
twitter-lyr feed --format table

# Custom field selection
twitter-lyr feed --fields id,author,text

# Show full tweet text (no truncation)
twitter-lyr feed --full-text
```

## Session Integration

The twitter-lyr CLI supports automatic browser cookie extraction using ObscuraCookieManager:

```bash
# Automatic cookie extraction from browser
twitter-lyr feed
```

The CLI will automatically extract cookies from your browser when needed for authenticated operations.

## Filtering

Enable score-based filtering to get higher quality content:

```bash
twitter-lyr feed --filter
```

Configure filters in `~/.twitter/config.yaml`:
```yaml
filter:
  min_score: 50
  max_age_hours: 24
```

## Workflow

### Step 1: Analyze User Request
- Determine the type of Twitter operation needed (read vs. write)
- Identify if authentication is required
- Select appropriate command based on user intent

### Step 2: Generate Command
- Use the command structure above to build the appropriate CLI command
- Add relevant flags for output format and filtering
- Include any required parameters (usernames, tweet IDs, etc.)

### Step 3: Execute and Validate
- Run the command using shell execution
- Check for successful completion
- Parse the output in the appropriate format
- Report the result to the user

### Step 4: Handle Errors
- If authentication fails: guide user to ensure browser cookies are accessible
- If command not found: suggest installing twitter-lyr
- If rate limited: suggest waiting before retrying
- If invalid parameters: provide correct usage examples

## Error Handling

### Common Issues and Solutions

1. **Command not found**
   - Error: "twitter-lyr: command not found"
   - Solution: Install twitter-lyr globally or add to PATH

2. **Authentication errors**
   - Error: "Authentication required"
   - Solution: Ensure browser cookies are accessible and user is logged into Twitter

3. **Rate limiting**
   - Error: "Rate limit exceeded"
   - Solution: Wait before retrying the operation

4. **Invalid parameters**
   - Error: "Invalid username/tweet ID"
   - Solution: Verify the username or tweet ID is correct

## Integration Example

### User Request: "Post a tweet about the new AI features"

**Skill Processing:**
1. Identify as a write operation requiring authentication
2. Generate command: `twitter-lyr post "Excited about the new AI features!"`
3. Execute and report results

### User Request: "Search for tweets about climate change"

**Skill Processing:**
1. Identify as a search operation
2. Generate command: `twitter-lyr search "climate change"`
3. Execute and parse results in TOON format
4. Report findings to user

### User Request: "Get Elon Musk's recent tweets"

**Skill Processing:**
1. Identify as user profile request
2. Generate command: `twitter-lyr user-posts elonmusk`
3. Execute and format results
4. Report recent tweets to user

## Best Practices

1. **Always check authentication requirements** before executing write operations
2. **Use appropriate output formats** for the task (TOON for data processing, table for human reading)
3. **Handle rate limiting gracefully** by suggesting delays between operations
4. **Provide context in results** when returning tweet data (author, timestamp, metrics)
5. **Use filtering when appropriate** to improve content quality
6. **Handle errors with actionable suggestions** for resolution