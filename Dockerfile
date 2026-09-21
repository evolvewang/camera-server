FROM python:orbbec-2.1.2

WORKDIR /app

# 安装 ZeroMQ Python 库
RUN python -m pip install \
    --no-cache-dir \
    pyzmq==27.2.0 \
    -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY . /app

# 启动服务
CMD ["python", "zmq_server.py"]