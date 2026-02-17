# Available Tools

This document describes the tools available to pocketfox.

## File Operations

### read_file
Read the contents of a file.
```
read_file(path: str) -> str
```

### write_file
Write content to a file (creates parent directories if needed).
```
write_file(path: str, content: str) -> str
```

### edit_file
Edit a file by replacing specific text.
```
edit_file(path: str, old_text: str, new_text: str) -> str
```

### list_dir
List contents of a directory.
```
list_dir(path: str) -> str
```

## Shell Execution

### exec
Execute a shell command and return output.
```
exec(command: str, working_dir: str = None) -> str
```

**Safety Notes:**
- Commands have a configurable timeout (default 60s)
- Dangerous commands are blocked (rm -rf, format, dd, shutdown, etc.)
- Output is truncated at 10,000 characters
- Optional `restrictToWorkspace` config to limit paths

## Web Access

### web_search
Search the web using Brave Search API.
```
web_search(query: str, count: int = 5) -> str
```

Returns search results with titles, URLs, and snippets. Requires `tools.web.search.apiKey` in config.

### web_fetch
Fetch and extract main content from a URL.
```
web_fetch(url: str, extractMode: str = "markdown", maxChars: int = 50000) -> str
```

**Notes:**
- Content is extracted using readability
- Supports markdown or plain text extraction
- Output is truncated at 50,000 characters by default

## Communication

### message
Send a message to the user (used internally).
```
message(content: str, channel: str = None, chat_id: str = None) -> str
```

### voice
Generate voice audio from text using ElevenLabs TTS.
```
voice(
    text: str,                    # Required: Text to convert to speech
    output_path: str = None,      # Optional: Custom output path
    voice_id: str = None,         # Optional: ElevenLabs voice ID (default from config)
    stability: float = 0.0,       # Optional: 0.0=creative, 0.5=natural, 1.0=robust
    speed: float = 1.0,           # Optional: 0.7=slow, 1.0=normal, 1.2=fast
    title: str = None,            # Optional: Audio metadata title
    artist: str = "Pocketfox"    # Optional: Audio metadata artist
) -> str                          # Returns path to generated audio file
```

**Direction Tags (eleven_v3 model):**
The text can include direction tags for expressive speech:
- Emotions: `[excited]`, `[happy]`, `[sad]`, `[angry]`, `[sarcastic]`
- Delivery: `[whispers]`, `[shouts]`, `[softly]`, `[firmly]`
- Pacing: `[short pause]`, `[pause]`, `[long pause]`
- Reactions: `[laughs]`, `[sighs]`, `[gasps]`, `[chuckles]`

**Example:**
```python
voice(
    text="[excited] Hello! [pause] I have [whispers] a secret... [normal, playful] Just kidding!",
    stability=0.0,  # Creative mode for best emotional expression
    title="Greeting"
)
```

**Configuration:**
Requires `tools.voice.apiKey` in config. Optional defaults:
- `tools.voice.default_voice_id` — Default voice to use
- `tools.voice.default_stability` — Default stability setting

**Notes:**
- Output format is MP3 (44.1kHz, 128kbps)
- If ffmpeg is available, ID3 metadata is added automatically
- Files are saved to `workspace/media/voice/` by default

## Background Tasks

### spawn
Spawn a subagent to handle a task in the background.
```
spawn(task: str, label: str = None) -> str
```

Use for complex or time-consuming tasks that can run independently. The subagent will complete the task and report back when done.

## Scheduled Reminders (Cron)

Use the `exec` tool to create scheduled reminders with `pocketfox cron add`:

### Set a recurring reminder
```bash
# Every day at 9am
pocketfox cron add --name "morning" --message "Good morning! ☀️" --cron "0 9 * * *"

# Every 2 hours
pocketfox cron add --name "water" --message "Drink water! 💧" --every 7200
```

### Set a one-time reminder
```bash
# At a specific time (ISO format)
pocketfox cron add --name "meeting" --message "Meeting starts now!" --at "2025-01-31T15:00:00"
```

### Manage reminders
```bash
pocketfox cron list              # List all jobs
pocketfox cron remove <job_id>   # Remove a job
```

## Heartbeat Task Management

The `HEARTBEAT.md` file in the workspace is checked every 30 minutes.
Use file operations to manage periodic tasks:

### Add a heartbeat task
```python
# Append a new task
edit_file(
    path="HEARTBEAT.md",
    old_text="## Example Tasks",
    new_text="- [ ] New periodic task here\n\n## Example Tasks"
)
```

### Remove a heartbeat task
```python
# Remove a specific task
edit_file(
    path="HEARTBEAT.md",
    old_text="- [ ] Task to remove\n",
    new_text=""
)
```

### Rewrite all tasks
```python
# Replace the entire file
write_file(
    path="HEARTBEAT.md",
    content="# Heartbeat Tasks\n\n- [ ] Task 1\n- [ ] Task 2\n"
)
```

---

## Adding Custom Tools

To add custom tools:
1. Create a class that extends `Tool` in `pocketfox/agent/tools/`
2. Implement `name`, `description`, `parameters`, and `execute`
3. Register it in `AgentLoop._register_default_tools()`
