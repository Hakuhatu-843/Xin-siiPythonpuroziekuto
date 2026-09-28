---
name: Discord API rate limits
description: Replit workflow behavior when Discord rejects bot login requests with a global HTTP 429.
---

Discord bot login attempts from a Replit workflow can be rejected by Discord with a global HTTP 429 even when the secret is present and never exposed.

**Why:** The failure occurs at Discord's API boundary before the bot's ready event, so repeated immediate restarts can prolong the block and do not prove that the token is invalid.

**How to apply:** Keep the workflow alive with token-safe exponential backoff, log only the retry state, and treat the bot's ready-event log as the only connection-success signal.