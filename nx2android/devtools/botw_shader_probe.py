# 对已核验 BOTW FS 生成最小输出诊断；不修改 bundle，不连接设备。
import argparse
import hashlib
from pathlib import Path
import subprocess

KEY = 'fd374d26e6e1e536'
SHA256 = '9d4eabcea983133fc20914a99d8f4ea2b1e8b4a83d67edad06b7bacd826e1d33'
PROBES = {
    'control': '',
    'additive': '_66 = vec4(_242, 0.0);',
    'additive_base_uv': '_66 = vec4(texture(fp_t_tcb_16, vec2(_88, _90)).rgb, 0.0);',
    'layer0': '_66 = vec4(_240, 0.0);',
    'layer1': '_66 = vec4(_238, 0.0);',
    'uv_delta': '_66 = vec4(vec2(_164 - _88, _220 - _90) * vec2(640.0, 360.0) * 0.25 + 0.5, 0.0, 0.0);',
}


def main():
    ap = argparse.ArgumentParser(description='生成目标 FS 的独立诊断变体；输出不是修复版')
    ap.add_argument('spv', type=Path)
    ap.add_argument('out', type=Path)
    ap.add_argument('--tools', type=Path, required=True, help='包含 spirv-cross、glslangValidator、spirv-val 的目录')
    a = ap.parse_args()
    if not a.out.is_dir():
        ap.error('输出父目录必须已存在')
    if hashlib.sha256(a.spv.read_bytes()).hexdigest() != SHA256:
        ap.error('源 SPIR-V 哈希与已审核版本不一致，停止生成')
    suffix = '.exe' if (a.tools / 'spirv-cross.exe').exists() else ''
    cross, compiler, validator = [str(a.tools / (n + suffix)) for n in ('spirv-cross', 'glslangValidator', 'spirv-val')]
    src = subprocess.run([cross, str(a.spv)], check=True, capture_output=True, text=True).stdout
    # 变量编号属于已审核的 SPIRV-Cross 输出，换版本后必须重新审核。
    anchors = ('vec3 _242 = texture(fp_t_tcb_16', 'vec3 _238 = texture(fp_t_tcb_20',
               'vec3 _240 = texture(fp_t_tcb_20', '_66.w = (_234 * _234) * float(_251 & 1);')
    if any(s not in src for s in anchors) or not src.rstrip().endswith('}'):
        ap.error('反编译结构改变，停止生成')
    for mode, statement in PROBES.items():
        folder = a.out / mode
        folder.mkdir(exist_ok=True)
        glsl, spv = folder / (KEY + '.frag'), folder / (KEY + '.spv')
        if glsl.exists() or spv.exists():
            ap.error(f'输出已存在，拒绝覆盖: {folder}')
        # 当前 draw 的混合为 ONE / SRC_ALPHA；诊断 alpha=0 排除原目标背景。
        text = src[:src.rfind('}')] + ('    ' + statement + '\n' if statement else '') + '}\n'
        glsl.write_text(text, encoding='utf-8')
        subprocess.run([compiler, '-V', '--target-env', 'vulkan1.2', '-S', 'frag', str(glsl), '-o', str(spv)], check=True)
        subprocess.run([validator, '--target-env', 'vulkan1.2', str(spv)], check=True)
        print(f'{mode}: 已编译并验证 {spv}')


if __name__ == '__main__':
    main()
