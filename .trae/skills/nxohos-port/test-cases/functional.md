### 测试用例：FUNC-001

**skill**：nxohos-port  
**类型**：functional（功能验证）

#### 输入

BOTW 菜单能通过替代 shader 显示，但原始最终 blit 仍不正确。请继续定位并修复。

#### 预期输出

**验证标准**：

- 先固定当前 PID 和菜单加载后的真实帧，不用启动 frame0 或历史 PPM 作结论。
- 每次只替换 vertex、fragment、depth mode 或 present 中的一个变量。
- 将替代 shader 限定为诊断手段，最终使用原始 bundle 验收。
- 实际代码改动经过 subagent 审核后再构建和真机验证。
- 记录日志、截图、性能及未解决项。

**不应包含**：把常量色或任意非黑图宣布为正式修复

---

### 测试用例：FUNC-002

**skill**：nxohos-port  
**类型**：functional（功能验证）

#### 输入

游戏数据约 15 GB，需要制作并安装 HarmonyOS HAP，首启后还要读取所有 RomFS 文件。

#### 预期输出

**验证标准**：

- 使用 mkhap.py 将 entry 与 dataN feature 模块分别构建和签名。
- 多 HAP 整组推送后使用 bm install -p 和 -w 600 安装。
- manifest 第三列标记文件所在模块，ArkTS 用 createModuleContext 读取。
- staging 分块复制并核对长度，全部成功后才写 stamp 和启动游戏。
- 不使用 assembleApp 或单文件 hdc install 代替多 HAP 流程。
