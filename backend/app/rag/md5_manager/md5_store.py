import os
import json
from datetime import datetime

import aiofiles
from aiofiles import os as aio_os

from app.utils.config import chroma_config
from app.utils.path_tool import get_abstract_path
from app.core.logger_handler import logger


class MD5Store:
    """MD5存储管理器。

    负责对上传文件的 MD5 摘要进行持久化管理，支持异步和同步两种写入方式。
    每个用户（或公共知识库）拥有独立的 MD5 记录目录，记录以 JSON 行格式
    存储在 md5_hex_store.txt 文件中，每行包含 md5、filename、
    original_filename 和 upload_time 四个字段。

    关键逻辑：
    - 按 user_id 隔离存储目录，user_id 为 None 时使用公共目录；
    - 读写均采用追加（a）/ 覆盖（w）模式，写入时自动序列化为 JSON；
    - 删除操作支持按文件名、按 MD5 值、以及清除整个用户目录三种粒度；
    - 当记录列表为空时，自动清理文件和空目录以释放磁盘空间。
    """

    def __init__(self):
        """初始化 MD5Store 实例。

        从 chroma_config 配置中读取 md5_hex_store 路径，
        并将其父目录作为所有 MD5 记录的根目录（base_dir）。
        """
        self.base_dir = os.path.dirname(get_abstract_path(chroma_config['md5_hex_store']))

    def _get_md5_store_dir(self, user_id: str = None) -> str:
        """
        获取MD5存储目录
        :param user_id: 用户ID，为None时返回公共目录
        :return: MD5存储目录路径
        """
        if user_id:
            return os.path.join(self.base_dir, 'user_md5', user_id)
        else:
            return os.path.join(self.base_dir, 'public_md5')

    async def check_md5_hex(self, md5_for_check: str, user_id: str = None) -> bool:
        """异步检查指定 MD5 值是否已存在于记录中。

        遍历用户（或公共）MD5 记录文件，逐行解析 JSON 或纯文本格式的记录，
        与目标 MD5 进行比对。首次调用时若目录或文件不存在，会自动创建。

        :param md5_for_check: 要检查的 MD5 摘要值。
        :param user_id: 用户 ID，为 None 时检查公共知识库。
        :return: MD5 存在返回 True，不存在或读取出错返回 False。
        """
        md5_dir = self._get_md5_store_dir(user_id)
        md5_path = os.path.join(md5_dir, 'md5_hex_store.txt')

        if not await aio_os.path.exists(md5_dir):
            await aio_os.makedirs(md5_dir, exist_ok=True)
            async with aiofiles.open(md5_path, 'w', encoding="utf-8"):
                pass
            return False

        if not await aio_os.path.exists(md5_path):
            async with aiofiles.open(md5_path, 'w', encoding="utf-8"):
                pass
            return False

        try:
            async with aiofiles.open(md5_path, 'r', encoding="utf-8") as f:
                async for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith('{'):
                        try:
                            data = json.loads(line)
                            if data.get('md5') == md5_for_check:
                                return True
                        except Exception:
                            if line == md5_for_check:
                                return True
                    else:
                        if line == md5_for_check:
                            return True
            return False
        except Exception as e:
            logger.error(f"【向量数据库】检查MD5时出错: {e}")
            return False

    async def save_md5_hex(self, md5_hex: str, filename: str = None, original_filename: str = None, user_id: str = None):
        """异步保存一条 MD5 记录到文件。

        将 MD5 值及关联的文件名信息序列化为 JSON，追加写入记录文件。
        若目录不存在会自动创建。

        :param md5_hex: 要保存的 MD5 摘要值。
        :param filename: 处理后的文件名（可选），用于标识文件在系统中的存储名称。
        :param original_filename: 原始文件名（可选），即用户上传时的文件名。
        :param user_id: 用户 ID，为 None 时保存到公共知识库目录。
        """
        md5_dir = self._get_md5_store_dir(user_id)
        md5_path = os.path.join(md5_dir, 'md5_hex_store.txt')

        if not await aio_os.path.exists(md5_dir):
            await aio_os.makedirs(md5_dir, exist_ok=True)

        data = {
            'md5': md5_hex,
            'filename': filename,
            'original_filename': original_filename,
            'upload_time': datetime.now().isoformat()
        }

        async with aiofiles.open(md5_path, 'a', encoding="utf-8") as f:
            await f.write(json.dumps(data, ensure_ascii=False) + '\n')

    def save_md5_hex_sync(self, md5_hex: str, filename: str = None, original_filename: str = None, user_id: str = None):
        """同步保存一条 MD5 记录到文件（线程安全版本）。

        功能与 save_md5_hex 相同，但使用同步 I/O 操作，适用于多线程
        切片任务中无法使用 async/await 的场景。

        :param md5_hex: 要保存的 MD5 摘要值。
        :param filename: 处理后的文件名（可选）。
        :param original_filename: 原始文件名（可选）。
        :param user_id: 用户 ID，为 None 时保存到公共知识库目录。
        """
        md5_dir = self._get_md5_store_dir(user_id)
        md5_path = os.path.join(md5_dir, 'md5_hex_store.txt')

        if not os.path.exists(md5_dir):
            os.makedirs(md5_dir, exist_ok=True)

        data = {
            'md5': md5_hex,
            'filename': filename,
            'original_filename': original_filename,
            'upload_time': datetime.now().isoformat()
        }

        with open(md5_path, 'a', encoding="utf-8") as f:
            f.write(json.dumps(data, ensure_ascii=False) + '\n')

    async def _read_md5_records(self, user_id: str = None) -> tuple:
        """异步读取指定用户的 MD5 记录文件并解析为列表。

        逐行读取文件，自动兼容 JSON 行格式和旧版纯文本 MD5 格式。
        若文件不存在则返回空列表。

        :param user_id: 用户 ID，为 None 时读取公共知识库记录。
        :return: 二元组 (文件路径, 记录列表)，每条记录为包含 md5、
                 filename、original_filename、upload_time 的字典。
        """
        md5_dir = self._get_md5_store_dir(user_id)
        md5_path = os.path.join(md5_dir, 'md5_hex_store.txt')

        if not await aio_os.path.exists(md5_path):
            return md5_path, []

        records = []
        async with aiofiles.open(md5_path, 'r', encoding="utf-8") as f:
            async for line in f:
                line = line.strip()
                if not line:
                    continue
                if line.startswith('{'):
                    try:
                        records.append(json.loads(line))
                    except Exception:
                        records.append({
                            'md5': line, 'filename': None,
                            'original_filename': None, 'upload_time': None
                        })
                else:
                    records.append({
                        'md5': line, 'filename': None,
                        'original_filename': None, 'upload_time': None
                    })
        return md5_path, records

    async def _write_md5_records(self, md5_path: str, records: list):
        """将记录列表写入指定的 MD5 记录文件。

        当记录列表为空时，自动删除文件及其所在的空目录以释放磁盘空间。
        写入时使用覆盖模式（w），每条记录序列化为一行 JSON。

        :param md5_path: MD5 记录文件的完整路径。
        :param records: 要写入的记录列表，每条为包含 md5 等字段的字典。
        """
        if not records:
            md5_dir = os.path.dirname(md5_path)
            if await aio_os.path.exists(md5_path):
                await aio_os.remove(md5_path)
            if await aio_os.path.exists(md5_dir):
                try:
                    await aio_os.rmdir(md5_dir)
                except OSError:
                    pass
            return

        async with aiofiles.open(md5_path, 'w', encoding="utf-8") as f:
            for record in records:
                await f.write(json.dumps(record, ensure_ascii=False) + '\n')

    async def delete_user_md5(self, user_id: str):
        """删除指定用户的整个 MD5 记录目录及其文件。

        用于用户注销或管理员清理数据时，一次性移除该用户的所有 MD5 记录。

        :param user_id: 要删除记录的用户 ID。
        """
        md5_dir = self._get_md5_store_dir(user_id)
        md5_path = os.path.join(md5_dir, 'md5_hex_store.txt')
        if await aio_os.path.exists(md5_path):
            await aio_os.remove(md5_path)
        if await aio_os.path.exists(md5_dir):
            await aio_os.rmdir(md5_dir)
        logger.info(f"【MD5存储】已删除用户 {user_id} 的MD5记录")

    async def delete_by_filename(self, user_id: str, filename: str):
        """根据文件名删除对应的 MD5 记录。

        遍历用户的所有记录，匹配 filename 或 original_filename 字段，
        找到后从记录中移除并回写文件。仅删除第一个匹配的记录。

        :param user_id: 用户 ID。
        :param filename: 要匹配的文件名。
        :return: 被删除记录的 MD5 值；若未找到匹配记录则返回 None。
        """
        md5_path, records = await self._read_md5_records(user_id)
        if not records:
            return None

        found_md5 = None
        remaining = []
        for record in records:
            record_filename = record.get('filename', record.get('original_filename'))
            if record_filename == filename:
                found_md5 = record.get('md5')
            else:
                remaining.append(record)

        if found_md5 is None:
            return None

        await self._write_md5_records(md5_path, remaining)
        logger.info(f"【MD5存储】已删除用户 {user_id} 的文件 {filename} 的MD5记录")
        return found_md5

    async def delete_single_md5(self, user_id: str, md5_to_delete: str) -> bool:
        """根据 MD5 值删除对应的单条记录。

        使用列表过滤的方式移除匹配的记录，若列表长度未变化说明未找到目标。

        :param user_id: 用户 ID。
        :param md5_to_delete: 要删除的 MD5 摘要值。
        :return: 成功删除返回 True，未找到匹配记录返回 False。
        """
        md5_path, records = await self._read_md5_records(user_id)
        if not records:
            return False

        remaining = [r for r in records if r.get('md5') != md5_to_delete]
        if len(remaining) == len(records):
            return False

        await self._write_md5_records(md5_path, remaining)
        logger.info(f"【MD5存储】已删除用户 {user_id} 的MD5记录: {md5_to_delete}")
        return True

    async def get_md5_info(self, user_id: str, md5_value: str):
        """根据 MD5 值获取对应的文档元信息。

        遍历用户记录列表，返回第一条匹配的记录字典。

        :param user_id: 用户 ID。
        :param md5_value: 要查询的 MD5 摘要值。
        :return: 包含 md5、filename、original_filename、upload_time 的字典；
                 未找到时返回 None。
        """
        _, records = await self._read_md5_records(user_id)
        for record in records:
            if record.get('md5') == md5_value:
                return record
        return None

    async def get_all_md5_records(self, user_id: str) -> list:
        """获取指定用户的所有 MD5 记录。

        :param user_id: 用户 ID。
        :return: MD5 记录列表，每条为包含 md5、filename、
                 original_filename、upload_time 的字典。
        """
        _, records = await self._read_md5_records(user_id)
        return records