import asyncio
import sys


def pytest_asyncio_loop_factories(config, item):
    # psycopg needs Selector on Windows, while subprocess MCP clients need Proactor.
    if sys.platform == "win32" and item.path.name == "test_database.py":
        return {"selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.new_event_loop}
