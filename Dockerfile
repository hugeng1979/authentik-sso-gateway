# 基础镜像：自带 Python 3.12 运行环境（本镜像仅提供依赖，代码由 compose 挂载注入）
FROM python:3.12-slim

# 禁用字节码文件、实时输出日志（容器最佳实践）
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 只安装 Python 依赖；改代码/配置无需重建镜像，docker compose restart 即生效
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 9010

# 使用 gunicorn 生产级启动（app.py / gunicorn.conf.py / handlers/ 由 volume 挂载到 /app）
CMD ["gunicorn", "-c", "gunicorn.conf.py", "app:app"]
