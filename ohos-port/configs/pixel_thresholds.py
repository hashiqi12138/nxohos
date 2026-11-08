# pixel_thresholds.py — 像素判定阈值集中配置（B 文档 §测试基础设施约定：
# "阈值常量集中在 configs/pixel_thresholds.py，各闸门引用同一配置"）
#
# verify_pixel.py 缺省值从这里读取；闸门文档（docs/verify/Gxx.md）引用同一名值，
# 调阈值只改本文件并留档。

# TC0-2-2：探针纯色 #0FDC78 中心区域，容差 ±16/通道（截屏色域偏移兜底前）
CENTER_DEFAULT_TOL = 16

# G0.2 兜底：vkCmdCopyImage readback 逐像素断言，容差 ±2/通道（不留人工复核）
READBACK_DEFAULT_TOL = 2

# fullframe 非纯色判定（亮度标准差与主色占比，0-255 刻度）
FULLFRAME_MIN_STD = 8          # 亮度 std > 8
FULLFRAME_MIN_NONDOMINANT = 0.05   # 非主色像素占比 > 5%

# compare-baseline：同尺寸逐像素比对，≥99.5% 像素在容差内视为通过
BASELINE_PASS_RATIO = 0.995
BASELINE_DEFAULT_TOL = 16

# TC0-3-2 / compare-baseline：PSNR 下限（B 文档 M0.3：>40dB）。
# PSNR = 10·log10(255²/MSE)。实现按"逐像素最大通道差"计算 MSE，该值 ≥ 任一
# 单通道 MSE ⇒ 打包 PSNR ≤ 逐通道最小 PSNR，即保守（更严）估计，方向安全。
BASELINE_MIN_PSNR_DB = 40.0
