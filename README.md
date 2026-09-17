# Camera Server

Camera Server 从本机 Orbbec 相机持续采集 RGB 图像，将最新帧编码为 JPEG，
再通过 ZeroMQ PUB/SUB 对外发布。当前服务单进程支持一个相机流。

## 安装

使用 Python 3.10：

```powershell
python -m pip install -r requirements.txt
```

`pyorbbecsdk` 及其本机运行库请按 Orbbec SDK 的方式安装。可先运行现有的
`tests/test_orbbec.py`，确认 SDK、驱动和设备可用。

## 启动 RGB 流服务

先在 `configs/config.yaml` 中填写当前 Orbbec 相机序列号：

```yaml
camera:
  device_sn: "<相机序列号>"
```

然后在仓库根目录运行：

```powershell
python zmq_server.py
```

服务端、视频流、客户端和日志的项目级参数统一位于 `configs/config.yaml`：

| 配置 | 含义 |
| --- | --- |
| `camera.device_sn` | 物理相机 SN，同时是 ZMQ 订阅 topic |
| `stream.jpeg_quality` | JPEG 质量，范围 1–100 |
| `zmq.publisher_endpoint` | 服务端 PUB 监听端点 |
| `zmq.subscriber_endpoint` | 客户端 SUB 连接端点 |
| `zmq.send_hwm` / `receive_hwm` | 实时消息高水位限制 |
| `zmq.receive_timeout_ms` | 客户端接收超时 |
| `client.show` | 是否显示 OpenCV 预览窗口 |
| `logging.*` | 日志级别、目录、文件名和保留天数 |

本机只连接一台 Orbbec 且 `device_sn` 留空时，服务端可以自动选中该设备；
客户端必须配置明确的 SN 才能订阅。日志同时输出到控制台和 `Log/` 目录。
按 `Ctrl+C` 时，服务会依次关闭发布器和相机。

## 外部连接方法

同一台主机运行客户端时，保持以下配置：

```yaml
zmq:
  subscriber_endpoint: "tcp://127.0.0.1:5558"
```

在另一个已激活 `camera-server` 环境的 Anaconda Prompt 中运行：

```powershell
python -m service.camera_client
```

另一台主机连接时，把 `subscriber_endpoint` 中的 `127.0.0.1` 改成相机主机的 LAN IP，
例如 `tcp://192.168.1.100:5558`，并确认 Windows 防火墙允许 TCP 5558 入站。

在自己的 Python 程序中使用：

```python
from zmq_client import ZmqVideoSubscriber

with ZmqVideoSubscriber(
        device_sn="<相机序列号>",
        endpoint="tcp://192.168.1.100:5558",
        receive_timeout_ms=5000,
        receive_hwm=2,
) as stream:
    while True:
        metadata, bgr_frame = stream.receive()
        # bgr_frame 是 OpenCV BGR ndarray，可直接交给 cv2 或算法处理。
        print(metadata["frame_id"], bgr_frame.shape)
```

PUB/SUB 不保证补发历史帧：客户端只接收订阅建立之后的新帧。视频链路优先低延迟，
慢客户端可能丢帧，但不会无界积压旧帧；客户端应根据 `frame_id` 判断跳帧。

## 传输协议

Camera Server 使用物理相机 SN 作为订阅通道名称。每帧是三段 ZeroMQ
multipart 消息：

1. UTF-8 `device_sn`，也是 SUB topic；
2. UTF-8 JSON 元数据；
3. JPEG 字节。

元数据包含 `protocol_version`、`device_sn`、`frame_id`、`timestamp`、
`width`、`height`、`channels` 和 `encoding`。当前协议版本为 `1`。
`timestamp` 是采集主机的 Unix 秒时间戳，解码结果为 OpenCV BGR 三通道图像。

## 测试

无需相机即可验证编码、传输、解码、超时、停止和异常路径：

```powershell
python -m unittest discover -s tests -v
```
