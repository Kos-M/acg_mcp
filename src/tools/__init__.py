"""MCP tool registrations for the ACG server."""

import functools
import inspect

import anyio.to_thread


def _in_worker_thread(fn):
    """Wrap a blocking tool function so it runs in a worker thread.

    FastMCP calls plain ``def`` tools directly on the asyncio event loop, so
    one slow call (network fetch, embedding, MongoDB) blocked every other
    request: parallel tool calls queued behind each other and all appeared
    to hang. ``functools.wraps`` keeps the signature FastMCP builds the
    argument schema from.
    """

    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))

    return wrapper


def run_sync_tools_in_threads(mcp) -> None:
    """Make ``mcp.tool()`` register sync functions as thread-offloaded async tools.

    Call before registering tools. Async tools are registered unchanged.
    """
    original_tool = mcp.tool

    @functools.wraps(original_tool)
    def tool(*args, **kwargs):
        decorator = original_tool(*args, **kwargs)

        def register(fn):
            if not inspect.iscoroutinefunction(fn):
                decorator(_in_worker_thread(fn))
                return fn  # callers keep the plain sync function
            return decorator(fn)

        return register

    mcp.tool = tool
