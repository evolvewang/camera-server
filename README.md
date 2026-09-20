# Camera Server

Camera Server 从本机一台 Orbbec 相机持续采集 RGB、深度或两路图像，
并通过 ZeroMQ PUB/SUB 对外发布。RGB 使用 JPEG；深度保留原始
`uint16` 数值，使用无损 16 位 PNG。

## 安装

使用 Python 3.10：

```powershell
python -m pip install -r requirements.txt
```

`pyorbbecsdk` 及其本机运行库请按 Orbbec SDK 的方式安装。可先运行现有的
`tests/test_orbbec.py`，确认 SDK、驱动和设备可用。

## 启动视频流服务

先在 `configs/config.yaml` 中填写当前 Orbbec 相机序列号：

```yaml
camera:
  device_sn: "<相机序列号>"
```

然后在仓库根目录运行：

```powershell
python zmq_server.py
```

服务端、视频流和日志的项目级参数统一位于 `configs/config.yaml`：

| 配置 | 含义 |
| --- | --- |
| `camera.device_sn` | 物理相机 SN，同时是 ZMQ 订阅 topic |
| `stream.type` | `color`、`depth` 或 `depth_and_color` |
| `stream.align_mode` | `DISABLE`、`HW_D2C` 或 `SW_D2C`；后两者仅适用于双流 |
| `stream.color/depth.width/height/format/fps` | 启动前选择设备支持的流 Profile |
| `stream.color.auto_exposure/exposure/gain` | RGB 自动曝光或手动曝光与增益 |
| `stream.color.auto_white_balance/white_balance` | RGB 自动或手动白平衡 |
| `stream.color.brightness/sharpness/saturation/contrast/power_line_frequency` | 可选 RGB 图像控制；`null` 表示不修改设备当前值 |
| `stream.depth.auto_exposure/exposure/gain` | 深度自动曝光或手动曝光与增益 |
| `stream.jpeg_quality` | JPEG 质量，范围 1–100 |
| `zmq.publisher_endpoint` | 服务端 PUB 监听端点 |
| `zmq.send_hwm` | 服务端发送队列的高水位限制 |
| `logging.*` | 日志级别、文件名和保留天数；日志目录固定为项目根目录 `log/` |

本机只连接一台 Orbbec 且 `device_sn` 留空时，服务端可以自动选中该设备；
客户端必须配置明确的 SN 才能订阅。日志同时输出到控制台和 `log/` 目录。
按 `Ctrl+C` 时，服务会依次关闭发布器和相机。

启用自动曝光时，相应的 `exposure` 和 `gain` 必须设为 `null`；
启用自动白平衡时，`white_balance` 必须设为 `null`。
手动值会先校验设备是否支持写入，再按设备报告的范围和步长检查；
不支持的 Profile 或控制项会让服务明确报错，不会悄悄回退。
配置只在启动时应用；修改 YAML 后需重启服务。
`HW_D2C` 必须选择与 RGB Profile 兼容的硬件对齐深度 Profile。
当前示例默认 `DISABLE`，因为是否支持硬件对齐及其分辨率组合取决于相机。
原 JSON 中的 `sample_dir` 和 `clean_sample_nums` 属于本地采样存盘业务，
不影响连续 PUB，因此未加入服务配置。

## 外部连接方法

同一台主机运行客户端时，在 `zmq_client.py` 的入口块中设置：

```python
device_sn = "<相机序列号>"
endpoint = "tcp://127.0.0.1:5558"
receive_timeout_ms = 5000
receive_hwm = 2
show = True
```

在另一个已激活 `camera-server` 环境的 Anaconda Prompt 中运行：

```powershell
python zmq_client.py
```

另一台主机连接时，把 `endpoint` 中的 `127.0.0.1` 改成相机主机的 LAN IP，
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
        metadata, frame = stream.receive()
        if metadata["stream_type"] == "color":
            # frame: OpenCV BGR uint8
            print("RGB", metadata["frame_id"], frame.shape)
        else:
            # frame: 原始单通道 uint16；乘 depth_scale 得到毫米
            depth_mm = frame.astype("float32") * metadata["depth_scale"]
            print("Depth", metadata["frame_id"], depth_mm.shape)
```

PUB/SUB 不保证补发历史帧：客户端只接收订阅建立之后的新帧。视频链路优先低延迟，
慢客户端可能丢帧，但不会无界积压旧帧；客户端应根据 `frame_id` 判断跳帧。

## 传输协议

Camera Server 使用物理相机 SN 作为订阅通道名称。每帧是三段 ZeroMQ
multipart 消息。RGB 和深度共用 SN topic，通过元数据区分：

1. UTF-8 `device_sn`，也是 SUB topic；
2. UTF-8 JSON 元数据；
3. RGB 为 JPEG 字节；深度为无损 16 位 PNG 字节。

元数据包含 `protocol_version`、`device_sn`、`stream_type`、
`frame_id`、`timestamp`、`width`、`height`、`channels` 和 `encoding`。
深度帧另外包含 `depth_scale`，其单位是毫米/原始数值。当前协议版本为 `2`；
它与原来的 RGB-only v1 客户端不兼容，必须同步更新客户端。
`frame_id` 在每一路流中分别递增。PUB/SUB 不保证两路帧严格一一配对；
`timestamp` 是采集主机的 Unix 秒时间戳，可用于粗略关联。
`service/video_protocol.py` 中的协议版本和编码标识属于服务端与客户端共同遵守的
消息契约，不是部署时随意修改的运行参数，因此不放入 YAML。

## 测试

无需相机即可验证编码、传输、解码、超时、停止和异常路径：

```powershell
python -m unittest discover -s tests -v
```
