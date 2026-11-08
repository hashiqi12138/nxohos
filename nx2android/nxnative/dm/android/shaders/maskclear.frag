#version 450
// Android port: the clear colour of a masked clear (gfx_vk_exec.c clear_masked); the pipeline's colour write mask keeps the other channels
layout(push_constant) uniform P { vec4 c; } p;
layout(location = 0) out vec4 o;
void main() { o = p.c; }
