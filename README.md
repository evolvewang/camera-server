# Camera Server

当前开发版本：**v0.3.0**（请求式拍照待真机验证）

Camera Server 在相机主机上连接一台 Orbbec 相机，持续采集 color、depth 或双流数据，
通过 ZeroMQ PUB/SUB 对外提供低延迟视频流，也支持通过独立的 REQ/REP 端点按需获取
最新 color 和 depth 帧。color 使用 JPEG，depth 使用无损 16 位 PNG；客户端分别还原为
OpenCV BGR `uint8` 和原始 `uint16` 数组。

## 当前功能

- 支持 `color`、`depth` 和 `depth_and_color` 三种采集模式。
- 支持设备 Profile 选择、自动/手动曝光、增益、白平衡以及部分 RGB ISP 控制。
- 支持 `DISABLE`、`HW_D2C` 和 `SW_D2C` 深度对齐模式；对齐只适用于双流。
- 服务端使用物理相机 SN 作为订阅 topic，color 和 depth 共用同一个 topic。
- 请求式拍照可在一次请求中取得 color、depth 或两者，并返回各路帧的元数据。
- 客户端校验协议版本、消息结构、图像尺寸、通道、编码和深度比例。
- 支持 ArUco 标记中心点识别和预览叠加，入口为 `aruco_detect.py`。
- 服务端和客户端都支持超时、异常传播和可重复关闭；视频链路只保留最新帧，优先低延迟。

## 安装

项目使用 Python 3.10。先安装通用依赖：

```powershell
python -m pip install -r requirements.txt
```

`requirements.txt` 锁定 `pyorbbecsdk2`；其本机运行库需按照 Orbbec SDK 的方式安装，
并确保设备驱动可用。
项目测试环境使用 `pyorbbecsdk2==2.1.2`、NumPy 1.26.4、OpenCV 4.11.0、PyYAML 6.0.3
和 pyzmq 27.2.0。

## 配置与启动

服务端配置统一位于 `configs/config.yaml`。至少确认相机 SN 和流模式：

```yaml
camera:
  device_sn: "<相机序列号>"

stream:
  type: "depth_and_color"
  align_mode: "DISABLE"
```

当本机只连接一台 Orbbec 且 `camera.device_sn` 为空时，服务端会自动选择该设备；
连接多台设备时必须填写 SN。

主要配置项如下：

| 配置 | 说明 |
| --- | --- |
| `camera.device_sn` | 物理相机 SN，同时是 ZMQ topic |
| `stream.type` | `color`、`depth` 或 `depth_and_color` |
| `stream.align_mode` | `DISABLE`、`HW_D2C` 或 `SW_D2C`；仅双流可用 |
| `stream.color/depth.width/height/format/fps` | 采集 Profile |
| `stream.color.*` / `stream.depth.*` | 相机曝光、增益和图像控制 |
| `stream.jpeg_quality` | color JPEG 质量，范围 1–100 |
| `zmq.publisher_endpoint` | 服务端 PUB 监听地址 |
| `zmq.send_hwm` | 服务端发送队列上限 |
| `zmq.snapshot_endpoint` | 请求式拍照的 REP 监听地址，默认 `tcp://0.0.0.0:5559` |
| `zmq.snapshot_max_frame_age_ms` | 最新帧的最长允许年龄；过期时返回 `NO_FRAME` |
| `logging.*` | 日志级别、文件名和轮转备份数量 |

启用自动曝光时，相应的 `exposure` 和 `gain` 必须为 `null`；启用自动白平衡时，
`white_balance` 必须为 `null`。修改 YAML 后需重启服务。日志输出到控制台和项目根目录
的 `log/` 目录。

在已激活项目环境的终端中启动服务：

```powershell
python zmq_server.py
```

按 `Ctrl+C` 可优雅停止发布器和相机采集线程。

## 客户端接收

在 `zmq_client.py` 的入口块中填写订阅参数：

```python
device_sn = "<相机序列号>"
endpoint = "tcp://127.0.0.1:5558"
receive_timeout_ms = 5000
receive_hwm = 2
show = True
```

然后运行：

```powershell
python zmq_client.py
```

在 Python 程序中复用订阅器：

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
            # frame 是 OpenCV BGR uint8 图像
            print("color", metadata["frame_id"], frame.shape)
        else:
            # frame 是原始单通道 uint16；转换为毫米时乘 depth_scale
            depth_mm = frame.astype("float32") * metadata["depth_scale"]
            print("depth", metadata["frame_id"], depth_mm.shape)
```

另一台主机连接时，将 endpoint 中的 `127.0.0.1` 替换为相机主机的 LAN IP，并允许
TCP 5558 通过防火墙。

## 请求式拍照

拍照服务随 `python zmq_server.py` 一起启动。它读取相机采集线程已经缓存的最新帧，
不会重新打开相机，也不会等待下一帧。单次请求可以选择 color、depth 或双流；
双流各自携带独立的帧号和时间戳，不保证严格同步。若有一路尚无帧、已超过
`snapshot_max_frame_age_ms` 或未启用，整次请求返回错误，不返回部分图像。

编辑 `zmq_snapshot_client.py` 入口块中的 `device_sn` 和 `endpoint`，然后运行一次拍照：

```powershell
python zmq_snapshot_client.py
```

默认请求 `("color", "depth")`，返回的 color 为 BGR `uint8`、depth 为原始单通道
`uint16`。`show=True` 预览图像；要保存图片，可将入口块的 `save_dir` 设置为
`Path("photos")`。保存的 depth PNG 保留原始 16 位值，毫米值需乘元数据中的
`depth_scale`。请求单路时将 `stream_types` 改为 `("color",)` 或 `("depth",)`。

在自己的程序中调用：

```python
from zmq_snapshot_client import ZmqSnapshotClient

with ZmqSnapshotClient(
    device_sn="<相机序列号>",
    endpoint="tcp://127.0.0.1:5559",
    request_timeout_ms=5000,
) as client:
    photos = client.capture(("color", "depth"))
    color_meta, color_bgr = photos["color"]
    depth_meta, depth_raw = photos["depth"]
    depth_mm = depth_raw.astype("float32") * depth_meta["depth_scale"]
```

若从另一台主机请求，将 `127.0.0.1` 改为相机主机的 LAN IP，并放行 TCP 5559。
服务端返回的 `NO_FRAME`、`STREAM_UNAVAILABLE`、`DEVICE_MISMATCH` 等错误会由客户端
抛出 `SnapshotRemoteError`，其 `code` 属性可用于分支处理；网络超时抛出
`TimeoutError`。客户端在超时后会重建 REQ socket，以便继续发起下一次请求。

## ArUco 识别

`aruco_detect.py` 复用同一个 `ZmqVideoSubscriber`，在 color 帧上识别 ArUco 标记并返回
像素中心点；depth 帧返回空中心列表。可直接在入口块中配置 SN、endpoint、预览开关和
字典名称后运行：

```powershell
python aruco_detect.py
```

也可以在代码中使用：

```python
from aruco_detect import detect_aruco_centers

centers = detect_aruco_centers(color_bgr, "DICT_4X4_50")
```

## ZeroMQ 视频协议

当前协议版本为 **2**。每帧是三段 multipart 消息：

1. UTF-8 编码的 `device_sn`，同时作为 SUB topic；
2. UTF-8 JSON 元数据；
3. 图像字节：color 为 JPEG，depth 为无损 16 位 PNG。

元数据字段：

| 字段 | 说明 |
| --- | --- |
| `protocol_version` | 固定为 `2` |
| `device_sn` | 物理相机 SN，与 topic 相同 |
| `stream_type` | `color` 或 `depth` |
| `frame_id` | 每一路流独立递增 |
| `timestamp` | 采集主机 Unix 时间戳，单位秒 |
| `width` / `height` | 图像尺寸 |
| `channels` | color 为 3，depth 为 1 |
| `encoding` | color 为 `jpeg`，depth 为 `png16` |
| `depth_scale` | 仅 depth；原始值乘此比例得到毫米 |

PUB/SUB 不补发历史帧，客户端只接收建立订阅后的新帧；慢客户端可能丢帧，但不会无限
积压旧帧。两路 `frame_id` 独立递增，不能仅凭帧号认为 color 和 depth 严格配对；需要
关联时应参考 `timestamp`。

协议常量维护在 `service/video_protocol.py`，变更消息结构或编码时需要同步更新发布端
和客户端，并递增协议版本。

拍照请求使用独立的协议版本 `1`，与上述视频帧版本 `2` 分开。REQ 发送单段 JSON：

```json
{"protocol_version":1,"device_sn":"<相机序列号>","stream_types":["color","depth"]}
```

成功响应为多段消息：第一段 JSON 包含 `protocol_version: 1`、`status: "ok"`、
`device_sn` 和按请求顺序排列的 `frames` 元数据列表；之后每路图像各占一段字节，
顺序与 `frames` 相同。每个帧元数据沿用上表的 v2 格式。失败响应只有一段 JSON，
包含 `status: "error"` 及 `error.code`、`error.message`。这是独立端点，
不会改变现有 PUB/SUB 三段消息的格式。

## 测试

不连接相机即可验证配置、日志、Profile 选择、图像编码、双流传输、解码、超时、停止和
异常路径：

```powershell
python -m unittest discover -s tests -v
```

真实设备验证还需要 Orbbec 相机、驱动和 SDK 运行时；启动服务后可使用 `zmq_client.py`
检查连续流，并使用 `zmq_snapshot_client.py` 检查单次请求和响应。
