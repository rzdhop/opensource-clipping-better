"""rzdhop story MCP: the backend as tools for Claude (the brain) to drive.

Claude writes the story (bible, cast, script, storyboard) in the chat; this
server is the muscle it calls: RunPod Serverless ComfyUI for images and
clips, the story store on disk, and (later stages) TTS, the renderer and
the metadata pack. Everything it wraps already exists in ``clipping/``;
nothing here spends without a tool call that says so.

Run it with ``python -m mcp_server`` (streamable HTTP on ``MCP_HOST:MCP_PORT``,
8787 by default), or ``python -m mcp_server --stdio`` for a local client.
"""
