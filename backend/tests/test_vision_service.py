"""VisionService 单元测试 —— 后端分派、批量协议三级容错与降级。

模型实例整体注入；DashScope SDK 用假响应对象替换调用入口；
HumanMessage 走真实类以校验多模态消息结构。
"""
import base64

import pytest
from types import SimpleNamespace as NS

from tests.helpers.unmock import LANGCHAIN_STACK, restore_real

restore_real(*LANGCHAIN_STACK)

from app.utils.vision_service import VisionService  # noqa: E402


class FakeNonOllama:
    """类名不含 ChatOllama：走 DashScope 分支。"""

    api_key = None
    model_name = "qvq-test"


class FakeChatOllama:
    model_name = "llava"

    def __init__(self):
        self.invoked = []

    async def ainvoke(self, messages):
        self.invoked.append(messages)
        return type("R", (), {"content": "异步描述"})()

    def invoke(self, messages):
        self.invoked.append(messages)
        return type("R", (), {"content": "同步描述"})()


@pytest.fixture()
def svc() -> VisionService:
    return VisionService(model=FakeChatOllama())


_PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def test_encode_image_b64_and_mime(tmp_path):
    f = tmp_path / "img.png"
    f.write_bytes(_PNG_1PX)
    img_b64, mime = VisionService()._encode_image(str(f))
    assert mime == "image/png"
    assert base64.b64decode(img_b64) == _PNG_1PX

    f2 = tmp_path / "weird.xyz"
    f2.write_bytes(b"zz")
    assert VisionService()._encode_image(str(f2))[1] == "image/png"  # 兜底


def test_build_prompt_truncation_and_empty_marker(svc):
    long_text = "字" * 1000
    prompt = svc._build_prompt(long_text)
    assert "字" * 800 in prompt
    assert "字" * 801 not in prompt

    empty_prompt = svc._build_prompt("   ")
    assert "该页没有提取到文本" in empty_prompt


def test_build_batch_prompt_with_and_without_refs(svc):
    pages = [{"page": 1, "text": "已有甲"}, {"page": 2, "text": ""}]
    p_with = svc._build_batch_prompt(pages)
    assert "--- Page 1 已有文本 ---" in p_with
    assert "Page 2 已有文本" not in p_with

    p_plain = svc._build_batch_prompt([{"page": 3, "text": ""}])
    assert "--- Page [页码] ---" in p_plain  # 模板本体


def test_parse_batch_strict_format_all_pages(svc):
    raw = "--- Page 1 ---\n第一页描述\n\n--- Page 2 ---\n第二页描述"
    out = svc._parse_batch_response(raw, [1, 2])
    assert out == {1: "第一页描述", 2: "第二页描述"}


def test_parse_batch_missing_page_filled_from_first(svc):
    raw = "--- Page 1 ---\n只有这页按格式"
    out = svc._parse_batch_response(raw, [1, 2, 3])
    assert out[1].startswith("只有")
    assert out[2] == out[3] == out[1]


def test_parse_batch_unformatted_single_page_gets_whole(svc):
    out = svc._parse_batch_response("自由发挥的一段话", [7])
    assert out == {7: "自由发挥的一段话"}


def test_parse_batch_unformatted_multi_splits_evenly(svc):
    raw = "\n".join(f"line{i}" for i in range(6))
    out = svc._parse_batch_response(raw, [1, 2, 3])
    assert out[1] == "line0\nline1"
    assert out[2] == "line2\nline3"
    assert out[3] == "line4\nline5"


def test_build_message_shapes(svc):
    msg = svc._build_message_from_b64("QUJD", "image/jpeg", "")
    kinds = [c["type"] for c in msg.content]
    assert kinds == ["text", "image_url"]
    assert msg.content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")

    images_info = [("QQ==", "image/png", "参考"), ("Rg==", "image/gif", "")]
    bmsg = svc._build_batch_message_from_b64(images_info, [4, 5])
    urls = [c["image_url"]["url"] for c in bmsg.content if c["type"] == "image_url"]
    assert len(urls) == 2
    assert "4 已有文本" in bmsg.content[0]["text"]


# ---------- 单页描述 ----------

@pytest.mark.asyncio
async def test_describe_page_ollama_branch(svc, tmp_path):
    img = tmp_path / "p.png"
    img.write_bytes(_PNG_1PX)
    out = await svc.describe_page(str(img), existing_text="辅助")
    assert out == "异步描述"
    assert svc.model.invoked and isinstance(
        svc.model.invoked[0][0].content, list)


@pytest.mark.asyncio
async def test_describe_page_dashscope_via_thread(monkeypatch, tmp_path):
    calls = {}

    def fake_call(**kwargs):
        calls.update(kwargs)
        return NS(output=NS(choices=[NS(message=NS(
            content=[{"text": "云端描述"}]))]))

    service = VisionService(model=FakeNonOllama())

    import dashscope as dashscope_pkg
    monkeypatch.setattr(dashscope_pkg.MultiModalConversation, "call", fake_call)

    img = tmp_path / "d.png"
    img.write_bytes(_PNG_1PX)
    out = await service.describe_page(str(img))
    assert out == "云端描述"
    assert calls["model"] == "qvq-test"


@pytest.mark.asyncio
async def test_describe_page_missing_file_returns_empty(svc, tmp_path):
    assert await svc.describe_page(str(tmp_path / "no.png")) == ""
    assert svc.describe_page_sync(str(tmp_path / "no.png")) == ""


@pytest.mark.asyncio
async def test_describe_page_exception_returns_empty(svc, tmp_path):
    img = tmp_path / "e.png"
    img.write_bytes(_PNG_1PX)

    async def boom(messages):
        raise RuntimeError("模型挂了")

    svc.model.ainvoke = boom
    assert await svc.describe_page(str(img)) == ""


def test_describe_page_sync_exception_returns_existing_text(tmp_path):
    def _raise(self, messages):
        raise RuntimeError("同步失败")

    FailSyncOllama = type("FailSyncChatOllama", (FakeChatOllama,),
                          {"invoke": _raise})
    s = VisionService(model=FailSyncOllama())
    img = tmp_path / "s.png"
    img.write_bytes(_PNG_1PX)
    assert s.describe_page_sync(str(img), existing_text="兜底旧文") == "兜底旧文"
    assert s.describe_page_sync(str(img)) == ""


# ---------- 批量描述 ----------

_BULK_RAW = ("--- Page 1 ---\n图一\n--- Page 2 ---\n图二")


@pytest.mark.asyncio
async def test_batch_async_missing_any_file_short_circuits(svc, tmp_path):
    ok = tmp_path / "ok.png"
    ok.write_bytes(_PNG_1PX)
    out = await svc.describe_pages_batch([str(ok), str(tmp_path / "x.png")],
                                         [1, 2], ["t1", "t2"])
    assert out == {1: "", 2: ""}


@pytest.mark.asyncio
async def test_batch_async_ollama_parses_strict(svc, tmp_path):
    paths = []
    for i in range(2):
        f = tmp_path / f"b{i}.png"
        f.write_bytes(_PNG_1PX)
        paths.append(str(f))

    async def _ainvoke(self, messages):
        self.invoked.append(messages)
        return type("R", (), {"content": _BULK_RAW})()

    Responder = type("BatchChatOllamaResponder", (FakeChatOllama,),
                     {"ainvoke": _ainvoke})
    svc.model = Responder()
    out = await svc.describe_pages_batch(paths, [1, 2], ["", ""])
    assert out == {1: "图一", 2: "图二"}
    # 批量消息一次携带两张图片
    body = svc.model.invoked[0][0]
    assert sum(c["type"] == "image_url" for c in body.content) == 2


@pytest.mark.asyncio
async def test_batch_async_exception_falls_back_to_existing(monkeypatch,
                                                            tmp_path, svc):
    f = tmp_path / "c.png"
    f.write_bytes(_PNG_1PX)

    class Boom(FakeNonOllama):
        pass

    svc.model = Boom()
    import dashscope as dashscope_pkg
    monkeypatch.setattr(dashscope_pkg.MultiModalConversation, "call",
                        staticmethod(lambda **k: (_ for _ in ()).throw(RuntimeError("云爆了"))))

    out = await svc.describe_pages_batch([str(f)], [9], ["原始文本", ""])
    assert out == {9: "原始文本"}


def test_batch_sync_dashscope_paths(monkeypatch, tmp_path):
    f = tmp_path / "syncb.png"
    f.write_bytes(_PNG_1PX)
    s = VisionService(model=FakeNonOllama())

    import dashscope as dashscope_pkg

    # 空 choices：调用“成功”但无文本，解析兜底得到空串而非走异常回退
    empty_choices = NS(output=NS(choices=[]))
    monkeypatch.setattr(dashscope_pkg.MultiModalConversation, "call",
                        lambda **k: empty_choices)
    assert s.describe_pages_batch_sync([str(f)], [3], ["旧"]) == {3: ""}

    none_resp = NS(output=None)
    monkeypatch.setattr(dashscope_pkg.MultiModalConversation, "call",
                        lambda **k: none_resp)
    assert s.describe_pages_batch_sync([str(f)], [3], [""]) == {3: ""}

    # 正常返回链路
    _page_txt = {"text": "--- Page 7 ---\n批量解析"}
    _msg = NS(content=[_page_txt])
    ok_resp = NS(output=NS(choices=[NS(message=_msg)]))

    monkeypatch.setattr(dashscope_pkg.MultiModalConversation, "call",
                        lambda **k: ok_resp)
    out = s.describe_pages_batch_sync([str(f)], [7], [""])
    assert out == {7: "批量解析"}


# ---------- 感知哈希 ----------

def test_compute_image_hash_returns_hex(tmp_path):
    img = tmp_path / "h.png"
    img.write_bytes(_PNG_1PX)
    h = VisionService().compute_image_hash(str(img))
    assert isinstance(h, str) and len(h) >= 8
    assert VisionService().compute_image_hash(str(tmp_path / "ghost.png")) == ""


def test_hamming_distance_rules():
    h = "a" * 16
    assert VisionService.hamming_distance(h, h) == 0
    assert VisionService.hamming_distance("", h) == 999
    assert VisionService.hamming_distance(h, "ffff") == 999  # 非法长度兜底
