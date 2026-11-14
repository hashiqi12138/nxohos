#version 450
// Android port: a clear of some channels only (gfx_vk_exec.c clear_masked): one triangle over the whole pass, cut to the clear's rectangle by the scissor
void main() {
    vec2 p = vec2(gl_VertexIndex & 1, gl_VertexIndex >> 1) * 4.0 - 1.0;
    gl_Position = vec4(p, 0.0, 1.0);
}
