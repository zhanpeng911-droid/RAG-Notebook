import os
import base64

from typing import Any
import asyncio
import re

from langchain_core.messages import HumanMessage

from app.utils.factory import get_default_vision_model
from app.core.logger_handler import logger


# 批量视觉识别模板：要求模型按固定格式输出每个页面的描述，
# 格式为 "--- Page N ---" + 描述内容，便于后续用正则解析。
_BATCH_PROMPT_TEMPLATE = """请逐页描述以下多张文档页面图片。

每张图片对应一个页面，请严格按照以下格式输出每个页面的描述：

--- Page [页码] ---
[该页的详细描述，包括文字内容、图片/图表/表格、布局结构等]

确保每个页面的描述前都有 "--- Page N ---" 标记（N为页码），不同页面的描述之间用空行隔开。"""

_BATCH_TEXT_REF_TEMPLATE = """以下是一些页面已有的文本内容（仅供参考，图片中的文字更优先）:
{refs}"""


class VisionService:
    """
    多模态视觉服务——将图片发送给视觉模型进行描述（支持单页和批量）。

    为什么需要这个服务？
    传统 PDF 解析只能提取文本，无法获取图片、图表、流程图中的信息。
    本服务通过调用视觉大模型（如 Qwen-VL），对 PDF 页面截图进行"看图说话"，
    将视觉信息转化为文本描述，补充到 Document 内容中，提升 RAG 检索质量。
    """

    def __init__(self, model=None):
        """
        初始化视觉服务实例。

        Args:
            model: 视觉模型实例（LangChain ChatModel），可选。
                   如果不传则使用默认配置的 vision_model（来自 app.utils.factory）。
                   支持 ChatOllama（本地部署）和 DashScope（阿里云百炼）两种后端。
        """
        self.model = model or get_default_vision_model()

    def _is_ollama(self) -> bool:
        """
        检测当前使用的模型是否为 Ollama 本地部署模型。

        通过检查模型类名中是否包含 'ChatOllama' 来判断。
        不同后端的调用方式不同：
        - Ollama：使用 LangChain 的 ainvoke/invoke 方法，支持多模态 HumanMessage。
        - DashScope：需要使用原生 SDK 调用，API 格式与 LangChain 不兼容。

        Returns:
            True 表示当前使用 Ollama 模型，False 表示使用其他后端（如 DashScope）。
        """
        return 'ChatOllama' in type(self.model).__name__

    def _encode_image(self, image_path: str) -> tuple[str, str]:
        """
        读取图片文件并编码为 base64 字符串，同时返回 MIME 类型。

        将图片文件读取为二进制数据，然后进行 base64 编码，
        以便通过 API 以 data URL 格式发送给视觉模型。
        支持 png、jpg、jpeg、tiff、bmp、gif、webp 等常见图片格式。

        Args:
            image_path: 图片文件的绝对路径。

        Returns:
            二元组 (base64编码字符串, MIME类型字符串)。
            例如 ("iVBORw0KGgo...", "image/png")。
        """
        ext = os.path.splitext(image_path)[1].lower()
        mime_map = {
            '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
            '.tiff': 'image/tiff', '.tif': 'image/tiff', '.bmp': 'image/bmp',
            '.gif': 'image/gif', '.webp': 'image/webp',
        }
        mime = mime_map.get(ext, 'image/png')
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")
        return img_b64, mime

    def _build_prompt(self, existing_text: str) -> str:
        """
        构造单页视觉识别的提示词。

        提示词要求视觉模型完成以下任务：
        1. 提取页面中的所有文字信息
        2. 描述图片、图表、流程图、表格等视觉元素
        3. 提取表格结构和数据
        4. 说明页面整体布局

        已有的纯文本提取结果会作为上下文附在提示词末尾（截取前800字符），
        辅助视觉模型理解页面内容，但提示词中明确说明"图片中的文字更优先"，
        因为视觉模型能看到版面布局，能纠正纯文本提取的顺序错误。

        Args:
            existing_text: 通过传统文本提取方法获得的页面文本内容。
                          如果为空字符串，提示词中会标注"该页没有提取到文本"。

        Returns:
            构造好的提示词字符串。
        """
        text_part = (
            f"页面已有文本（仅供参考）:\n{existing_text[:800]}"
            if existing_text.strip()
            else "该页没有提取到文本。"
        )
        return (
            "请详细描述这张文档页面图片中的内容：\n\n"
            "1. 提取页面中的所有文字信息，保持原文表述\n"
            "2. 描述页面中的图片、图表、流程图、表格等视觉元素的内容和作用\n"
            "3. 如果有表格，提取表格的结构和数据\n"
            "4. 说明页面整体的布局结构\n\n"
            f"{text_part}"
        )

    def _build_batch_prompt(self, pages_info: list[dict]) -> str:
        """
        构造批量视觉识别的提示词。

        使用预定义的 _BATCH_PROMPT_TEMPLATE 模板，要求模型按固定格式
        "--- Page N ---" 逐页输出描述，便于后续用正则解析。

        如果 pages_info 中包含已有的文本提取结果，会将它们作为参考信息
        附在提示词末尾（每页截取前800字符）。

        Args:
            pages_info: 页面信息列表，每个元素为字典，包含：
                       - "page": 页码（int）
                       - "text": 该页已有的文本内容（str）

        Returns:
            构造好的批量提示词字符串。
        """
        text_refs = []
        for info in pages_info:
            txt = info.get("text", "").strip()
            if txt:
                text_refs.append(
                    f"--- Page {info['page']} 已有文本 ---\n{txt[:800]}"
                )

        if text_refs:
            ref_block = _BATCH_TEXT_REF_TEMPLATE.format(
                refs="\n\n".join(text_refs)
            )
            return f"{_BATCH_PROMPT_TEMPLATE}\n\n{ref_block}"
        return _BATCH_PROMPT_TEMPLATE

    def _parse_batch_response(
        self, response_text: str, expected_pages: list[int]
    ) -> dict[int, str]:
        """
        解析批量视觉模型返回的文本，提取每个页面的描述。

        解析策略（三级容错）：
        1. 优先使用正则表达式匹配 "--- Page N ---" 格式的结构化输出。
        2. 如果正则只匹配到部分页面，缺失的页面用已解析的第一个页面内容填充。
        3. 如果正则完全没有匹配到（模型未按格式输出），则按行数平均分割文本作为 fallback。

        Args:
            response_text: 视觉模型返回的原始文本。
            expected_pages: 期望解析出的页码列表，例如 [1, 2, 3, 4, 5]。

        Returns:
            字典 {页码: 该页的描述文本}。如果解析完全失败，fallback 结果可能不够精确。
        """
        result = {}

        # 优先匹配严格格式：--- Page 1 --- 描述内容
        pattern = r"--- Page (\d+) ---\s*(.*?)(?=--- Page \d+ ---|\Z)"
        matches = re.findall(pattern, response_text.strip(), re.DOTALL)

        if matches:
            for page_num_str, description in matches:
                result[int(page_num_str)] = description.strip()

        # 如果所有页面都解析到了，直接返回
        if result and all(p in result for p in expected_pages):
            return result

        # 容错处理：如果模型没有按格式输出，尝试按行数平均分割（粗略 fallback）
        if not result:
            lines = response_text.strip().split('\n')
            if len(expected_pages) == 1:
                result[expected_pages[0]] = response_text.strip()
            else:
                per_page = max(1, len(lines) // len(expected_pages))
                for i, pn in enumerate(expected_pages):
                    start = i * per_page
                    end = start + per_page if i < len(expected_pages) - 1 else len(lines)
                    result[pn] = '\n'.join(lines[start:end]).strip()
        else:
            # 部分页面解析到了，缺失的页面用已解析的第一个页面内容填充
            for pn in expected_pages:
                if pn not in result:
                    first_key = next(iter(result))
                    result[pn] = result[first_key]

        return result

    def _build_message_from_b64(
        self, img_b64: str, mime: str, existing_text: str
    ) -> HumanMessage:
        """
        构造 LangChain HumanMessage 对象（单图模式）。

        HumanMessage 的 content 为列表格式，包含：
        - 一个 text 类型元素：视觉识别提示词
        - 一个 image_url 类型元素：图片的 data URL（data:{mime};base64,{img_b64}）

        这种格式同时兼容 Ollama 和阿里云百炼的视觉模型输入要求。

        Args:
            img_b64: 图片的 base64 编码字符串。
            mime: 图片的 MIME 类型（如 "image/png"）。
            existing_text: 该页已有的纯文本内容，用于辅助视觉模型理解。

        Returns:
            构造好的 LangChain HumanMessage 对象。
        """
        prompt = self._build_prompt(existing_text)
        content: list[Any] = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img_b64}"}},
        ]
        return HumanMessage(content=content)

    def _build_batch_message_from_b64(
        self,
        images_info: list[tuple[str, str, str]],
        page_numbers: list[int],
    ) -> HumanMessage:
        """
        构造 LangChain HumanMessage 对象（多图批量模式）。

        将多张图片和统一的批量提示词组合成一个 HumanMessage，
        让视觉模型一次性处理多页，减少 API 调用次数和共享 prompt 前缀的 token 消耗。

        Args:
            images_info: 图片信息列表，每个元素为三元组 (base64编码, MIME类型, 已有文本)。
            page_numbers: 对应的页码列表，与 images_info 一一对应。

        Returns:
            构造好的 LangChain HumanMessage 对象，content 包含
            一个 text 元素和多个 image_url 元素。
        """
        prompt = self._build_batch_prompt([
            {"page": pn, "text": txt}
            for pn, (_, _, txt) in zip(page_numbers, images_info)
        ])
        content_b: list[Any] = [{"type": "text", "text": prompt}]
        for img_b64, mime, _ in images_info:
            content_b.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{img_b64}"}
            })
        return HumanMessage(content=content_b)

    def _dashscope_describe(self, img_b64: str, mime: str, existing_text: str) -> str:
        """
        使用阿里云 DashScope 原生 SDK 进行单页视觉识别。

        由于 DashScope 的 API 格式与 LangChain 的 HumanMessage 不兼容，
        需要使用 dashscope.MultiModalConversation.call 直接调用。
        该方法是同步阻塞的，调用方应通过 asyncio.to_thread 或
        ThreadPoolExecutor 在异步/多线程环境中使用。

        Args:
            img_b64: 图片的 base64 编码字符串。
            mime: 图片的 MIME 类型。
            existing_text: 该页已有的纯文本内容。

        Returns:
            视觉模型返回的描述文本字符串。调用失败时返回空字符串。
        """
        import dashscope

        api_key = getattr(self.model, 'api_key', None) or os.getenv("ALIYUN_ACCESS_KEY_SECRET")
        model_name = str(getattr(self.model, "model_name", ""))

        messages = [{
            "role": "user",
            "content": [
                {"image": f"data:{mime};base64,{img_b64}"},
                {"text": self._build_prompt(existing_text)}
            ]
        }]

        response = dashscope.MultiModalConversation.call(
            model=model_name,
            messages=messages,
            api_key=api_key,
        )

        if response is None:
            logger.error("【视觉服务】DashScope 返回 None，可能是网络错误或请求超时")
            return ""

        choices = response.output.choices
        if not choices:
            logger.error("【视觉服务】DashScope 返回空 choices")
            return ""

        content_list = choices[0].message.content
        if isinstance(content_list, list) and len(content_list) > 0:
            return content_list[0].get("text", "")
        return str(content_list) if content_list else ""

    def _dashscope_describe_batch(
        self,
        images_info: list[tuple[str, str, str]],
        page_numbers: list[int],
    ) -> str:
        """
        使用阿里云 DashScope 原生 SDK 进行批量视觉识别。

        将多张图片和批量提示词一次性发送给 DashScope API，
        要求模型按 "--- Page N ---" 格式逐页输出描述。
        该方法是同步阻塞的，适用于多线程环境。

        Args:
            images_info: 图片信息列表，每个元素为三元组 (base64编码, MIME类型, 已有文本)。
            page_numbers: 对应的页码列表，与 images_info 一一对应。

        Returns:
            视觉模型返回的原始文本字符串，包含所有页面的描述。
            调用失败时返回空字符串。
        """
        import dashscope

        api_key = getattr(self.model, 'api_key', None) or os.getenv("ALIYUN_ACCESS_KEY_SECRET")
        model_name = str(getattr(self.model, "model_name", ""))

        prompt = self._build_batch_prompt([
            {"page": pn, "text": txt}
            for pn, (_, _, txt) in zip(page_numbers, images_info)
        ])

        content = [{"text": prompt}]
        for img_b64, mime, _ in images_info:
            content.append({"image": f"data:{mime};base64,{img_b64}"})

        messages = [{"role": "user", "content": content}]

        response = dashscope.MultiModalConversation.call(
            model=model_name,
            messages=messages,
            api_key=api_key,
        )

        if response is None:
            logger.error("【视觉服务·批量】DashScope 返回 None，可能是网络错误或请求超时")
            return ""

        choices = response.output.choices
        if not choices:
            logger.error("【视觉服务·批量】DashScope 返回空 choices")
            return ""

        content_list = choices[0].message.content
        if isinstance(content_list, list) and len(content_list) > 0:
            return content_list[0].get("text", "")
        return str(content_list) if content_list else ""

    async def describe_page(self, image_path: str, existing_text: str = "") -> str:
        """
        异步单页视觉描述——将单张 PDF 页面截图发送给视觉模型进行描述。

        根据模型后端自动选择调用方式：
        - Ollama：使用 LangChain 的 ainvoke 方法异步调用。
        - DashScope：使用原生 SDK，通过 asyncio.to_thread 避免阻塞事件循环。

        Args:
            image_path: 页面渲染后的图片文件绝对路径。
            existing_text: 该页已有的纯文本内容，作为上下文辅助视觉模型。
                          默认为空字符串。

        Returns:
            视觉模型返回的页面描述文本。图片不存在或调用失败时返回空字符串。
        """
        if not os.path.exists(image_path):
            logger.error(f"【视觉服务】图片文件不存在: {image_path}")
            return ""

        try:
            img_b64, mime = self._encode_image(image_path)

            if self._is_ollama():
                # Ollama：使用 LangChain 的 ChatOllama，支持多模态 HumanMessage
                message = self._build_message_from_b64(img_b64, mime, existing_text)
                response = await self.model.ainvoke([message])
                return str(response.content)
            else:
                # 阿里云百炼：DashScope 的 API 不兼容 LangChain 的 HumanMessage 格式，
                # 需要使用 DashScope 原生 SDK 调用（通过 asyncio.to_thread 避免阻塞事件循环）
                return await asyncio.to_thread(
                    self._dashscope_describe, img_b64, mime, existing_text
                )
        except Exception as e:
            logger.error(f"【视觉服务】视觉模型调用失败: {e}")
            return ""

    def describe_page_sync(self, image_path: str, existing_text: str = "") -> str:
        """
        同步单页视觉描述，用于 ThreadPoolExecutor 多线程环境。

        与 describe_page 功能相同，但使用同步调用方式，
        适用于 SSE 上传流程中的多线程处理场景。
        调用失败时返回已有文本（如果有的话），否则返回空字符串。

        Args:
            image_path: 页面渲染后的图片文件绝对路径。
            existing_text: 该页已有的纯文本内容。默认为空字符串。

        Returns:
            视觉模型返回的页面描述文本。
        """
        if not os.path.exists(image_path):
            logger.error(f"【视觉服务】图片文件不存在: {image_path}")
            return ""

        try:
            img_b64, mime = self._encode_image(image_path)

            if self._is_ollama():
                message = self._build_message_from_b64(img_b64, mime, existing_text)
                response = self.model.invoke([message])
                return str(response.content)
            else:
                return self._dashscope_describe(img_b64, mime, existing_text)
        except Exception as e:
            logger.error(f"【视觉服务·同步】调用失败: {e}")
            return existing_text if existing_text.strip() else ""

    async def describe_pages_batch(
        self,
        image_paths: list[str],
        page_numbers: list[int],
        existing_texts: list[str],
    ) -> dict[int, str]:
        """
        异步批量视觉描述——将多张页面图片一次性发送给视觉模型。

        相比逐页调用，批量模式可以减少 HTTP 请求次数和 token 消耗
        （多张图片共享同一个 prompt 前缀）。视觉模型会按 "--- Page N ---"
        格式逐页输出描述，然后通过正则解析提取各页结果。

        Args:
            image_paths: 页面渲染图片的路径列表。
            page_numbers: 对应的页码列表，与 image_paths 一一对应。
            existing_texts: 每页已有的纯文本内容列表，与 image_paths 一一对应。

        Returns:
            字典 {页码: 该页的视觉描述文本}。某页解析失败时对应值为空字符串。
        """
        for path in image_paths:
            if not os.path.exists(path):
                logger.error(f"【视觉服务·批量】图片文件不存在: {path}")
                return {pn: "" for pn in page_numbers}

        try:
            images_info = []
            for path, txt in zip(image_paths, existing_texts):
                img_b64, mime = self._encode_image(path)
                images_info.append((img_b64, mime, txt))

            if self._is_ollama():
                message = self._build_batch_message_from_b64(images_info, page_numbers)
                response = await self.model.ainvoke([message])
                raw_text = str(response.content)
            else:
                raw_text = await asyncio.to_thread(
                    self._dashscope_describe_batch, images_info, page_numbers
                )

            result = self._parse_batch_response(raw_text, page_numbers)
            logger.info(
                f"【视觉服务·批量】成功: {len(page_numbers)}页 -> {len(result)}页解析结果 "
                f"(页: {page_numbers})"
            )
            return result

        except Exception as e:
            logger.error(f"【视觉服务·批量】调用失败: {e}")
            return {
                pn: existing_texts[i] if existing_texts[i].strip() else ""
                for i, pn in enumerate(page_numbers)
            }

    def describe_pages_batch_sync(
        self,
        image_paths: list[str],
        page_numbers: list[int],
        existing_texts: list[str],
    ) -> dict[int, str]:
        """
        同步批量视觉描述，用于 ThreadPoolExecutor 多线程环境。

        与 describe_pages_batch 功能相同，但使用同步调用方式。
        适用于 SSE 上传流程中的多线程处理场景。

        Args:
            image_paths: 页面渲染图片的路径列表。
            page_numbers: 对应的页码列表，与 image_paths 一一对应。
            existing_texts: 每页已有的纯文本内容列表。

        Returns:
            字典 {页码: 该页的视觉描述文本}。
        """
        for path in image_paths:
            if not os.path.exists(path):
                logger.error(f"【视觉服务·批量·同步】图片文件不存在: {path}")
                return {pn: "" for pn in page_numbers}

        try:
            images_info = []
            for path, txt in zip(image_paths, existing_texts):
                img_b64, mime = self._encode_image(path)
                images_info.append((img_b64, mime, txt))

            if self._is_ollama():
                message = self._build_batch_message_from_b64(images_info, page_numbers)
                response = self.model.invoke([message])
                raw_text = str(response.content)
            else:
                raw_text = self._dashscope_describe_batch(images_info, page_numbers)

            return self._parse_batch_response(raw_text, page_numbers)

        except Exception as e:
            logger.error(f"【视觉服务·批量·同步】调用失败: {e}")
            return {
                pn: existing_texts[i] if existing_texts[i].strip() else ""
                for i, pn in enumerate(page_numbers)
            }

    def compute_image_hash(self, image_path: str) -> str:
        """
        计算图片的感知哈希（pHash, Perceptual Hash）。

        pHash 基于图片的视觉特征生成指纹，对轻微的缩放、压缩、颜色变化不敏感，
        因此可以判断两张图片是否"视觉相似"。这在 PDF 处理中非常有用，
        因为同一份文档的不同页可能包含相同的装饰性图片（如页眉、背景图、水印），
        对这些重复页面都调用视觉模型是浪费的。

        依赖 Pillow 和 imagehash 库。如果未安装，返回空字符串并记录警告日志。

        Args:
            image_path: 图片文件的绝对路径。

        Returns:
            pHash 的十六进制字符串表示（如 "a1b2c3d4e5f6a7b8"）。
            计算失败或依赖库未安装时返回空字符串。
        """
        try:
            from PIL import Image
            import imagehash
            with Image.open(image_path) as img:
                return str(imagehash.phash(img))
        except ImportError:
            logger.warning(
                "【视觉服务】imagehash 或 Pillow 未安装，无法进行图片去重"
            )
            return ""
        except Exception as e:
            logger.error(f"【视觉服务】计算图片哈希失败: {e}")
            return ""

    @staticmethod
    def hamming_distance(hash1: str, hash2: str) -> int:
        """
        计算两个感知哈希（pHash）之间的汉明距离。

        汉明距离是两个等长字符串在相同位置上不同字符的个数。
        在 pHash 语境下，距离越小代表图片越视觉相似：
        - 距离 = 0：完全相同的图片
        - 距离 <= 10（默认阈值）：视觉高度相似，视为重复页面
        - 距离 > 10：视觉差异较大，不视为重复

        Args:
            hash1: 第一张图片的 pHash 十六进制字符串。
            hash2: 第二张图片的 pHash 十六进制字符串。

        Returns:
            汉明距离（非负整数）。任一哈希为空或解析失败时返回 999（表示不相似）。
        """
        if not hash1 or not hash2:
            return 999
        try:
            import imagehash
            return imagehash.hex_to_hash(hash1) - imagehash.hex_to_hash(hash2)
        except Exception:
            return 999
