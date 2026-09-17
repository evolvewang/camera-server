# Camera Server

Camera Server 从本机 Orbbec 相机持续采集 RGB 图像，将最新帧编码为 JPEG，
再通过 ZeroMQ PUB/SUB 对外发布。当前服务单进程支持一个相机流。

## 安装

使用 Python 3.10：

```powershell
python -m pip install -r requirements.txt
```

`pyorbbecsdk` 及其本机运行库请按 Orbbec SDK 的方式安装。可先运行现有的
`test/orbbec_test.py`，确认 SDK、驱动和设备可用。

## 启动 RGB 流服务

请在仓库根目录运行：

```powershell
python -m service.camera_server --device-sn <相机序列号>
```

本机只连接一台 Orbbec 时，可省略 `--device-sn`，服务会自动选中该设备：

```powershell
python -m service.camera_server
```

常用参数：

```text
--camera-id CAM_01             订阅 topic/逻辑相机 ID
--bind tcp://0.0.0.0:5558      服务监听地址
--jpeg-quality 85              JPEG 质量，范围 1-100
--log-level INFO               日志级别
```

也可使用环境变量 `CAMERA_DEVICE_SN`、`CAMERA_ID`、`CAMERA_ZMQ_BIND` 和
`CAMERA_JPEG_QUALITY`。按 `Ctrl+C` 时，服务会依次关闭发布器和相机。

## 外部连接方法

同一台主机查看视频：

```powershell
python -m service.camera_client --endpoint tcp://127.0.0.1:5558 --camera-id CAM_01 --show
```

另一台主机连接时，把 `127.0.0.1` 改成相机主机的 LAN IP，并确认 Windows
防火墙允许 TCP 5558 入站：

```powershell
python -m service.camera_client --endpoint tcp://192.168.1.100:5558 --camera-id CAM_01 --show
```

在自己的 Python 程序中使用：

```python
from service.camera_client import ZmqVideoSubscriber

with ZmqVideoSubscriber(
    endpoint="tcp://192.168.1.100:5558",
    camera_id="CAM_01",
    receive_timeout_ms=5000,
) as stream:
    while True:
        metadata, bgr_frame = stream.receive()
        # bgr_frame 是 OpenCV BGR ndarray，可直接交给 cv2 或算法处理。
        print(metadata["frame_id"], bgr_frame.shape)
```

PUB/SUB 不保证补发历史帧：客户端只接收订阅建立之后的新帧。视频链路优先低延迟，
慢客户端可能丢帧，但不会无界积压旧帧；客户端应根据 `frame_id` 判断跳帧。

## 传输协议

每帧是三段 ZeroMQ multipart 消息：

1. UTF-8 `camera_id`，也是 SUB topic；
2. UTF-8 JSON 元数据；
3. JPEG 字节。

元数据包含 `camera_id`、`frame_id`、`timestamp`、`width`、`height`、
`channels` 和 `encoding`。`timestamp` 是采集主机的 Unix 秒时间戳，解码结果为
OpenCV BGR 三通道图像。

## 测试

无需相机即可验证编码、传输、解码、超时、停止和异常路径：

```powershell
python -m unittest discover -s tests -v
```
