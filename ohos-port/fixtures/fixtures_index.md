# fixtures 索引

真机用测试素材。入库前补齐文件并回填 SHA256；缺失的 fixture 对应 TC 不得执行。

| 路径 | 用途 | 关联 TC | SHA256 |
|---|---|---|---|
| `textures/bc7_texture_512.ktx2` | 512×512 BC7 纹理（M0.3 渲染源） | TC0-3-2 | 待入库 |
| `textures/bc7_reference_rgba.png` | bcdec 参考解码 RGBA8 封 PNG（512×512，compare-baseline 基准，与被测代码不同源生成） | TC0-3-2 | 待入库 |
| `textures/bc1_texture_256.ktx2` | 256×256 BC1 纹理（转码线对拍用，M3.4 预备） | M3.4 对拍 | 待入库 |
| `frames/baseline_trimap.bin` | 基准帧 RGBA8（compare-baseline 基准） | TC0-2-2 兜底 | 待入库 |

生成约定：

- BCn 源：任一 KTX2 工具（如 toktx）从 PNG 生成；参考解码用 `bcdec` 独立生成，
  不与被测代码共享实现（防同源盲区）。
- SHA256 计算后回填本表；`verify_report.py`/闸门文档引用同一份数据。
