# GitHub Copilot Instructions

Follow [AGENTS.md](../AGENTS.md) at the repository root: it describes the layout, the checks CI runs, how tests are written, and the conventions changes have to follow.

Beyond it, the usual Home Assistant integration rules apply: everything runs on the event loop (`aiohttp`, `asyncio.sleep`, never blocking calls), entities read from their coordinator rather than calling the API, and entities use `_attr_has_entity_name = True` with a `translation_key` instead of a hard-coded name.
