"""
统一成功响应 —— 所有 API 接口返回标准 JSON 格式。

响应格式: {"code": 200, "message": "success", "data": ...}
"""
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder


def success_response(message: str = "success", data=None) -> JSONResponse:
    """
    构造成功响应。

    :param message: 响应消息
    :param data: 响应数据（任意类型，会被 jsonable_encoder 序列化）
    :return: JSONResponse
    """
    response = {
        "code": 200,
        "message": message,
        "data": data
    }
    return JSONResponse(content=jsonable_encoder(response))