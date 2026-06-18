"""
统一异常体系 —— 所有业务异常继承自 RAGException，返回统一 JSON 格式。

格式: {"code": 错误码, "message": "错误描述", "data": null}

异常层级：
    RAGException（基类）
    ├── NoteException
    │   └── NoteNotFoundException
    ├── KnowledgeException
    ├── OrganizationException
    │   └── OrganizationNotFoundException
    └── SpaceException
        └── SpaceNotFoundException
"""


class RAGException(Exception):
    """RAG 系统统一异常基类 —— 所有业务异常的根"""
    def __init__(self, code: int = 500, message: str = "服务内部错误"):
        self.code = code
        self.message = message
        super().__init__(message)


class NoteException(RAGException):
    """笔记业务异常"""
    def __init__(self, message: str = "笔记操作失败", code: int = 400):
        super().__init__(code=code, message=message)


class NoteNotFoundException(NoteException):
    """笔记不存在"""
    def __init__(self, message: str = "笔记不存在"):
        super().__init__(code=404, message=message)


class KnowledgeException(RAGException):
    """知识库业务异常"""
    def __init__(self, message: str = "知识库操作失败", code: int = 400):
        super().__init__(code=code, message=message)


class OrganizationException(RAGException):
    """组织业务异常"""
    def __init__(self, message: str = "组织操作失败", code: int = 400):
        super().__init__(code=code, message=message)


class OrganizationNotFoundException(OrganizationException):
    """组织不存在"""
    def __init__(self, message: str = "组织不存在"):
        super().__init__(code=404, message=message)


class SpaceException(RAGException):
    """空间业务异常"""
    def __init__(self, message: str = "空间操作失败", code: int = 400):
        super().__init__(code=code, message=message)


class SpaceNotFoundException(SpaceException):
    """空间不存在"""
    def __init__(self, message: str = "空间不存在"):
        super().__init__(code=404, message=message)
