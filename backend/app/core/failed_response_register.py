"""
异常处理器注册 —— 将所有异常处理器注册到 FastAPI 应用。

异常处理优先级（从高到低）：
1. RAGException: RAG 统一业务异常
2. HTTPException: FastAPI HTTP 异常（401/403/404 等）
3. IntegrityError: 数据库完整性约束错误
4. SQLAlchemyError: 数据库通用错误
5. BusinessException: 自定义业务异常
6. RequestValidationError: 参数校验异常
7. Exception: 兜底处理所有未捕获异常
"""
from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.core.failed_response import (
    http_exception_handler, integrity_error_handler, sqlalchemy_error_handler,
    general_exception_handler, BusinessException, business_exception_handler,
    validation_exception_handler, rag_exception_handler,
)
from app.core.exceptions import RAGException


def register_exception_handlers(app):
    """注册全局异常处理器 —— 在 main.py 启动时调用"""
    app.add_exception_handler(RAGException, rag_exception_handler)
    app.add_exception_handler(HTTPException, http_exception_handler)
    app.add_exception_handler(IntegrityError, integrity_error_handler)
    app.add_exception_handler(SQLAlchemyError, sqlalchemy_error_handler)
    app.add_exception_handler(BusinessException, business_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, general_exception_handler)