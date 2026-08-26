"""在 conftest 全局 mock 环境下按需恢复真实模块。

conftest 在业务代码导入前把 langchain 系、chromadb 以及部分 app.rag.*
模块整体替换为 MagicMock。数据面单元测试需要真实实现（Document、
BM25Retriever、RecursiveCharacterTextSplitter 等）时，先用本模块删除
对应假条目再导入目标，Python 会从真实包重新加载。此前已导入并持有
旧引用的模块不受影响。
"""
import sys
import types
from unittest.mock import MagicMock


# conftest 把顶层 "langchain" 替换为无 __path__ 的 MagicMock，
# 形如 from langchain.embeddings.base import X 的子模块导入会直接
# ModuleNotFoundError。这里补齐缺失的假子模块条目，属性访问照常生效。
_ensure_entries = {
    "langchain.embeddings": {},
    "langchain.embeddings.base": {"Embeddings": MagicMock},
}
for _name, _attrs in _ensure_entries.items():
    if _name not in sys.modules or isinstance(sys.modules.get(_name), MagicMock):
        _mod = types.ModuleType(_name)
        for _k, _v in _attrs.items():
            setattr(_mod, _k, _v)
        sys.modules[_name] = _mod


def restore_real(*names):
    """删除指定名字的 MagicMock 假模块，使后续 import 命中真实包。"""
    for name in names:
        mod = sys.modules.get(name)
        if mod is not None and isinstance(mod, MagicMock):
            del sys.modules[name]


LANGCHAIN_STACK = [
    "langchain_core",
    "langchain_core.documents",
    "langchain_core.retrievers",
    "langchain_core.callbacks",
    "langchain_core.embeddings",
    "langchain_core.language_models",
    "langchain_chroma",
    "langchain_community",
    "langchain_community.retrievers",
    "langchain_classic",
    "langchain_classic.retrievers",
    "langchain_text_splitters",
]


def install_config_stub(separators=None):
    """用兼容的配置桩替换 conftest 的 app.utils.config mock。

    conftest 注入的 chroma_config 是缺键普通字典，访问
    chroma_config['separators'] / ['md5_hex_store'] 会直接 KeyError；
    这里换成带 __missing__ 兜底的字典：已知键给真值，未知键返回
    MagicMock，对既有消费者保持行为兼容。
    """
    from collections.abc import MutableMapping

    class _ConfigDict(dict):
        def __missing__(self, key):
            return MagicMock()

    class _ConfigModule(MutableMapping):
        def __init__(self, chroma_config):
            self._data = {"chroma_config": chroma_config}

        def __getitem__(self, key):
            return self._data[key]

        def __getattr__(self, name):
            return self._data.get(name, MagicMock())

        def __setattr__(self, name, value):
            if name.startswith("_") or name == "_data":
                object.__setattr__(self, name, value)
            else:
                self._data[name] = value

        def __len__(self):
            return len(self._data)

        def __iter__(self):
            return iter(self._data)

        def __delitem__(self, key):
            del self._data[key]

        def __setitem__(self, key, value):
            self._data[key] = value

    cfg = _ConfigDict()
    cfg["persist_directory"] = "/tmp/test_chroma"
    cfg["separators"] = separators if separators is not None else [
        "\n\n", "\n", "。", " ", "",
    ]
    sys.modules["app.utils.config"] = _ConfigModule(cfg)
