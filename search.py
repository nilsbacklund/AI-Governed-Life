import asyncio
from tavily import TavilyClient


async def web_search(query: str, api_key: str, max_results: int = 5) -> list[dict]:
    client = TavilyClient(api_key=api_key)

    def _search():
        return client.search(query=query, max_results=max_results)

    result = await asyncio.to_thread(_search)
    return [
        {
            "title": r.get("title", ""),
            "snippet": r.get("content", ""),
            "url": r.get("url", ""),
        }
        for r in result.get("results", [])
    ]
