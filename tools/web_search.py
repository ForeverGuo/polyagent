from langchain_core.tools import tool
from tavily import TavilyClient
import os

@tool
def web_search(query: str):
    """
    当需要查阅最新的库文档、解决报错或寻找技术实现方案时，调用此工具搜索互联网。
    """
    tavily = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))
    # search_depth="advanced" 适合技术调研
    response = tavily.search(query=query, search_depth="advanced", max_results=3)
    
    results = []
    for res in response['results']:
        results.append(f"来源: {res['url']}\n内容: {res['content']}\n")
    
    return "\n---\n".join(results)