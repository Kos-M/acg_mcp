"""MCP tools for URL crawling + ACG indexing.

Provides crawl-and-index pipeline using BFS URL discovery + ACG indexing.
Supports background threading for long-running operations.
Uses FastMCP @mcp.tool() decorator pattern.
"""

import json
import logging
import threading
import time
import uuid

from mcp.server.fastmcp import FastMCP

from src.acg.spider import crawl_urls
from src.acg.indexer import index_url

logger = logging.getLogger(__name__)

# In-memory store for background tasks
_background_tasks: dict[str, dict] = {}
_tasks_lock = threading.Lock()


def _run_crawl_and_index(task_id, start_url, max_depth, max_urls, include_pattern, exclude_pattern, allow_external, sentences_per_chunk):
    """Run crawl + index pipeline in a background thread."""
    try:
        with _tasks_lock:
            _background_tasks[task_id]["status"] = "crawling"
            _background_tasks[task_id]["progress"] = {"phase": "crawling", "urls_discovered": 0, "urls_indexed": 0, "total_to_index": 0}

        discovered = crawl_urls(
            start_url=start_url, max_depth=max_depth, max_urls=max_urls,
            include_pattern=include_pattern, exclude_pattern=exclude_pattern,
            allow_external=allow_external,
        )

        total = len(discovered)
        with _tasks_lock:
            _background_tasks[task_id]["progress"]["total_to_index"] = total
            _background_tasks[task_id]["progress"]["urls_discovered"] = total
            _background_tasks[task_id]["status"] = "indexing"
            _background_tasks[task_id]["progress"]["phase"] = "indexing"

        indexed, failed = [], []
        for i, url in enumerate(discovered):
            try:
                result = index_url(url, sentences_per_chunk=sentences_per_chunk)
                indexed.append({"url": url, "status": "indexed", "chunks": result.get("total_chunks", 0), "shi_prefix": result.get("shi_prefix", "")})
            except Exception as e:
                failed.append({"url": url, "error": str(e)})
            with _tasks_lock:
                _background_tasks[task_id]["progress"]["urls_indexed"] = i + 1

        with _tasks_lock:
            _background_tasks[task_id]["status"] = "completed"
            _background_tasks[task_id]["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            _background_tasks[task_id]["result"] = {
                "start_url": start_url, "total_discovered": total,
                "total_indexed": len(indexed), "total_failed": len(failed),
                "indexed": indexed, "failed": failed,
            }
    except Exception as e:
        logger.error(f"Crawl+Index task {task_id} failed: {e}")
        with _tasks_lock:
            _background_tasks[task_id]["status"] = "failed"
            _background_tasks[task_id]["error"] = str(e)
            _background_tasks[task_id]["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def register_tools(mcp: FastMCP) -> None:
    """Register all spider-related tools on the FastMCP instance."""

    @mcp.tool(
        name="acg_crawl_and_index",
        description="Crawl a starting URL to discover linked pages, then index them all via the ACG protocol. Supports background threading for large crawls. Two-phase: crawl (BFS) then index each URL.",
    )
    def acg_crawl_and_index(
        start_url: str,
        max_depth: int = 2,
        max_urls: int = 50,
        include_pattern: str = "",
        exclude_pattern: str = "",
        allow_external: bool = False,
        sentences_per_chunk: int = 8,
        background: bool = True,
    ) -> str:
        """Crawl and index URLs.

        Args:
            start_url: The URL to start crawling from.
            max_depth: Maximum crawl depth (default: 2).
            max_urls: Maximum URLs to index (default: 50).
            include_pattern: Optional regex — only URLs matching this.
            exclude_pattern: Optional regex — URLs matching this are skipped.
            allow_external: If True, crawl external domains.
            sentences_per_chunk: Sentences per ACG chunk (5-15, default: 8).
            background: If True, run in background and return task_id immediately.

        Returns:
            JSON with results or task tracking info.
        """
        inc = include_pattern or None
        exc = exclude_pattern or None

        if background:
            task_id = uuid.uuid4().hex[:12]
            started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

            with _tasks_lock:
                _background_tasks[task_id] = {
                    "status": "starting", "start_url": start_url,
                    "started_at": started, "completed_at": None,
                    "progress": {"phase": "starting", "urls_discovered": 0, "urls_indexed": 0, "total_to_index": 0},
                    "result": None, "error": None,
                }

            thread = threading.Thread(
                target=_run_crawl_and_index,
                args=(task_id, start_url, max_depth, max_urls, inc, exc, allow_external, sentences_per_chunk),
                daemon=True,
            )
            thread.start()

            return json.dumps({
                "task_id": task_id, "status": "started", "start_url": start_url,
                "max_depth": max_depth, "max_urls": max_urls,
                "started_at": started,
                "check_status_with": f'acg_crawl_status(task_id="{task_id}")',
            }, indent=2)
        else:
            discovered = crawl_urls(
                start_url=start_url, max_depth=max_depth, max_urls=max_urls,
                include_pattern=inc, exclude_pattern=exc, allow_external=allow_external,
            )
            indexed, failed = [], []
            for url in discovered:
                try:
                    result = index_url(url, sentences_per_chunk=sentences_per_chunk)
                    indexed.append({"url": url, "status": "indexed", "chunks": result.get("total_chunks", 0), "shi_prefix": result.get("shi_prefix", "")})
                except Exception as e:
                    failed.append({"url": url, "error": str(e)})

            return json.dumps({
                "start_url": start_url, "total_discovered": len(discovered),
                "total_indexed": len(indexed), "total_failed": len(failed),
                "indexed": indexed, "failed": failed,
            }, indent=2)

    @mcp.tool(
        name="acg_crawl_status",
        description="Check the status of a background crawl+index task started with acg_crawl_and_index. Returns progress and results when completed.",
    )
    def acg_crawl_status(task_id: str) -> str:
        """Check status of a background crawl task.

        Args:
            task_id: The task ID returned by acg_crawl_and_index.

        Returns:
            JSON with current status, progress, and result (if completed).
        """
        with _tasks_lock:
            task = _background_tasks.get(task_id)

        if task is None:
            return json.dumps({"error": f"Task '{task_id}' not found. Task IDs are valid for the current session only."})

        resp = {
            "task_id": task_id, "status": task["status"],
            "start_url": task.get("start_url", ""),
            "started_at": task.get("started_at", ""),
            "completed_at": task.get("completed_at"),
            "progress": task.get("progress"),
        }
        if task["status"] == "completed" and task.get("result"):
            resp["result"] = task["result"]
        if task["status"] == "failed" and task.get("error"):
            resp["error"] = task["error"]

        return json.dumps(resp, indent=2)

    @mcp.tool(
        name="acg_crawl_list_tasks",
        description="List all background crawl+index tasks and their current status.",
    )
    def acg_crawl_list_tasks() -> str:
        """List all background tasks."""
        with _tasks_lock:
            tasks = list(_background_tasks.items())

        result = [
            {
                "task_id": tid, "status": t["status"],
                "start_url": t.get("start_url", ""),
                "progress": t.get("progress"),
                "completed_at": t.get("completed_at"),
            }
            for tid, t in tasks
        ]
        return json.dumps(result, indent=2)
