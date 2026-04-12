# Available Tools

This document describes the tools available to pocketfox.

Tools follow a `category_action` naming convention. Glob patterns (e.g. `fs_*`,
`web_*`) can be used in a context's `allowed_tools` whitelist to restrict
access by category.

## File Operations

### fs_read
Read the contents of a file.
```
fs_read(path: str) -> str
```

### fs_write
Write content to a file (creates parent directories if needed).
```
fs_write(path: str, content: str) -> str
```

### fs_edit
Edit a file by replacing specific text.
```
fs_edit(path: str, old_text: str, new_text: str) -> str
```

### fs_list
List contents of a directory.
```
fs_list(path: str) -> str
```

### fs_view_image
View an image file — loads the image into visual context so you can describe it.
```
fs_view_image(path: str) -> image content
```

## Shell Execution

### shell_exec
Execute a shell command and return output.
```
shell_exec(command: str, working_dir: str = None) -> str
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

### message_send
Send a message to the user (used internally).
```
message_send(content: str, channel: str = None, chat_id: str = None) -> str
```

### voice_speak
Generate voice audio from text using ElevenLabs TTS.
```
voice_speak(
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
voice_speak(
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

### agent_spawn
Spawn a subagent to handle a task in the background.
```
agent_spawn(task: str, label: str = None) -> str
```

Use for complex or time-consuming tasks that can run independently. The subagent will complete the task and report back when done.

## Scheduled Reminders

### cron_schedule
Schedule reminders and recurring tasks from within the agent.
```
cron_schedule(action: str, ...) -> str  # action: add | list | remove
```

You can also manage reminders from the shell via `shell_exec`:

```bash
# Every day at 9am
pocketfox cron add --name "morning" --message "Good morning! ☀️" --cron "0 9 * * *"

# Every 2 hours
pocketfox cron add --name "water" --message "Drink water! 💧" --every 7200

# At a specific time (ISO format)
pocketfox cron add --name "meeting" --message "Meeting starts now!" --at "2025-01-31T15:00:00"

pocketfox cron list              # List all jobs
pocketfox cron remove <job_id>   # Remove a job
```

## Heartbeat Task Management

The `HEARTBEAT.md` file in the workspace is checked every 30 minutes.
Use file operations to manage periodic tasks:

### Add a heartbeat task
```python
# Append a new task
fs_edit(
    path="HEARTBEAT.md",
    old_text="## Example Tasks",
    new_text="- [ ] New periodic task here\n\n## Example Tasks"
)
```

### Remove a heartbeat task
```python
# Remove a specific task
fs_edit(
    path="HEARTBEAT.md",
    old_text="- [ ] Task to remove\n",
    new_text=""
)
```

### Rewrite all tasks
```python
# Replace the entire file
fs_write(
    path="HEARTBEAT.md",
    content="# Heartbeat Tasks\n\n- [ ] Task 1\n- [ ] Task 2\n"
)
```

`---`
### File Conversions
The "convert-all" uv python tool has been installed, globally. It can be used to convert various file formats.
Usage: `convert-all INPUT_FILE OUTPUT_FILE`

Examples:
```bash
convert-all audio.mp3 audio.ogg
convert-all picture.png picture.jpg
```


`---`

## Restricting Tools per Context

Contexts can restrict which tools are visible to the agent via the
`allowed_tools` field in `config.toml`. Patterns use glob syntax (`*`, `?`,
`[seq]`) matched against tool names. An empty or missing list means
"all tools allowed".

```toml
[contexts.public_chat]
allowed_tools = ["message_*", "fs_read", "web_*"]

[contexts.sandbox]
allowed_tools = ["fs_*", "shell_*"]
```

## Adding Custom Tools

To add custom tools:
1. Create a class that extends `Tool` in `pocketfox/agent/tools/`
2. Implement `name`, `description`, `parameters`, and `execute`
3. Register it in `AgentLoop._register_default_tools()`
4. Use the `category_action` naming convention so glob whitelists work cleanly
