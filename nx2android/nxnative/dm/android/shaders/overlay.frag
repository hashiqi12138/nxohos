#version 450
layout(location = 0) in vec2 uv;
layout(location = 0) out vec4 color;
layout(set = 0, binding = 0) uniform sampler2D tex;
layout(push_constant) uniform Q { vec4 pos; vec4 uv; float alpha; } q;   // alpha: the quad's opacity (the hidden gamepad's toggle button is faint)
void main() { color = texture(tex, uv) * vec4(1.0, 1.0, 1.0, q.alpha); }
