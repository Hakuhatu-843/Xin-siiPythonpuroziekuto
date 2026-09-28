---
name: Discord API rate limits
description: Replit workflow behavior when Discord rejects bot login requests with a global HTTP 429.
---

Discord bot login attempts from a Replit workflow can be rejected by Discord with a global HTTP 429 even when the secret is present and never exposed. This project should not automatically retry after that error.

**Why:** The failure occurs at Discord's API boundary before the bot's ready event, so repeated retries can prolong the block and do not prove that the token is invalid. The user explicitly prefers manual control after this failure.

**How to apply:** Stop the workflow, preserve the safe error log, and only retry manually after the rate limit clears. Treat the bot's ready-event log as the only connection-success signal.