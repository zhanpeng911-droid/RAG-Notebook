"""
直接测试 DashScope TextReRank API。

仅从本地环境变量读取 DASHSCOPE_API_KEY，不在仓库中内置任何真实密钥。
"""
import os
from dotenv import load_dotenv

# 加载 .env
load_dotenv()

# 仅使用本地环境变量中的 Key
api_key = os.getenv("DASHSCOPE_API_KEY", "").strip()
if not api_key:
    raise SystemExit("请先在本地 .env 或环境变量中配置 DASHSCOPE_API_KEY，再运行该脚本。")

masked_key = f"{api_key[:6]}...{api_key[-4:]}" if len(api_key) > 10 else "[configured]"
print(f"API Key: {masked_key}")

# 设置 API Key
import dashscope
dashscope.api_key = api_key

# 测试 API 调用
from dashscope import TextReRank

query = "什么是 Python？"
documents = [
    "Python 是一种高级编程语言。",
    "Java 是一种面向对象的编程语言。",
    "Python 列表推导式是创建列表的简洁语法。",
]

print(f"\n查询: {query}")
print(f"文档数: {len(documents)}")

try:
    response = TextReRank.call(
        model="gte-rerank",
        query=query,
        documents=documents,
        top_k=2,
        return_documents=True
    )

    print(f"\n响应状态码: {response.status_code}")

    if response.status_code == 200:
        print("API 调用成功！")
        for i, result in enumerate(response.output.results, 1):
            print(f"  {i}. 分数: {result.relevance_score:.4f}, 文档: {documents[result.index][:30]}...")
    else:
        print(f"API 调用失败: {response.code} - {response.message}")

except Exception as e:
    print(f"❌ 异常: {e}")
