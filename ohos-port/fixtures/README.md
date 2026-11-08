# fixtures — 二进制测试资产归档

## 归档约定（B 文档 §fixtures）

1. 每个二进制资产入本目录后，必须在 `fixtures_index.md` 登记：
   `文件名 | 用途 | 生成方式 | SHA256 | 登记日期`。
2. SHA256 由 `sha256sum <file>` 生成，闸门用例运行前**先校验再使用**
   （资产损坏时用例必须 FAIL 而非误报 PASS）。
3. 资产只增不改：需要变更时生成新文件并保留旧文件与登记行（可复现历史
   闸门结论）；废弃资产标记 `deprecated` 但不删除。
4. 生成方式必须写到可复现级别（脚本名+参数），优先用脚本在 CI 重新生成。

## 当前清单

| 文件 | 用途 | 生成方式 | SHA256 | 状态 |
|---|---|---|---|---|
| （暂无——TC0-3-2 的 bc7_texture_512.ktx2 及其 bcdec 参考 RGBA8 待真机阶段生成） | | | | |

## 待生成资产（真机阶段）

- `textures/bc7_texture_512.ktx2` —— TC0-3-2 用；BC7 512×512 标准纹理，
  由 `ktx` 工具链或 dm 工具生成，同目录放 `bcdec` 参考解码的 RGBA8 raw。
- `baseline/solid_0FDC78.png` —— TC0-2-2 首次真机截屏留档后归档为基准，
  供 `verify_pixel.py compare-baseline` 复用（G2.6 同款）。
