import { useEffect, useRef } from 'react'
import { useReducedMotion } from 'motion/react'
import './GlowCursor.css'

const vertexSource = `#version 300 es
void main(){ vec2 p=vec2(float((gl_VertexID<<1)&2),float(gl_VertexID&2)); gl_Position=vec4(p*2.0-1.0,0.0,1.0); }`
const fragmentSource = `#version 300 es
precision highp float;
uniform vec2 uResolution; uniform vec2 uPointer; uniform float uActive; out vec4 outColor;
void main(){ vec2 uv=gl_FragCoord.xy/uResolution; float aspect=uResolution.x/uResolution.y; vec2 delta=(uv-uPointer)*vec2(aspect,1.0); float d=length(delta); float glow=exp(-d*7.2)*uActive; vec3 blue=vec3(0.12,0.43,1.0); vec3 violet=vec3(0.45,0.22,1.0); vec3 color=mix(blue,violet,smoothstep(0.0,0.7,uv.x)); outColor=vec4(color*glow*0.65,glow*0.72); }`

function shader(gl: WebGL2RenderingContext, type: number, source: string) {
  const value = gl.createShader(type)
  if (!value) return null
  gl.shaderSource(value, source); gl.compileShader(value)
  if (!gl.getShaderParameter(value, gl.COMPILE_STATUS)) { gl.deleteShader(value); return null }
  return value
}

export default function GlowCursor() {
  const host = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const reduce = useReducedMotion()
  useEffect(() => {
    const element = host.current, canvas = canvasRef.current
    if (!element || !canvas || reduce || matchMedia('(pointer: coarse)').matches) return
    const gl = canvas.getContext('webgl2', { alpha: true, antialias: false, depth: false, stencil: false, powerPreference: 'low-power' })
    if (!gl || !gl.getExtension('EXT_color_buffer_float')) { element.classList.add('is-fallback'); return }
    let vs: WebGLShader | null = null, fs: WebGLShader | null = null, program: WebGLProgram | null = null
    const createProgram = () => {
      vs = shader(gl, gl.VERTEX_SHADER, vertexSource); fs = shader(gl, gl.FRAGMENT_SHADER, fragmentSource); program = gl.createProgram()
      if (!vs || !fs || !program) return false
      gl.attachShader(program, vs); gl.attachShader(program, fs); gl.linkProgram(program)
      return Boolean(gl.getProgramParameter(program, gl.LINK_STATUS))
    }
    if (!createProgram()) { if (program) gl.deleteProgram(program); if (vs) gl.deleteShader(vs); if (fs) gl.deleteShader(fs); element.classList.add('is-fallback'); return }
    element.classList.remove('is-fallback')
    let frame = 0, inside = false, inView = true, pointer: [number, number] = [0.7, 0.3], disposed = false
    let res = gl.getUniformLocation(program!, 'uResolution'), pos = gl.getUniformLocation(program!, 'uPointer'), active = gl.getUniformLocation(program!, 'uActive')
    const resize = () => { const rect = element.getBoundingClientRect(), dpr = Math.min(devicePixelRatio || 1, 1.25); canvas.width = Math.max(1, Math.round(rect.width * dpr)); canvas.height = Math.max(1, Math.round(rect.height * dpr)); gl.viewport(0, 0, canvas.width, canvas.height) }
    const render = () => { if (disposed || document.hidden || !inView || !program) { frame = 0; return } gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT); gl.useProgram(program); gl.uniform2f(res, canvas.width, canvas.height); gl.uniform2f(pos, pointer[0], pointer[1]); gl.uniform1f(active, inside ? 1 : 0); gl.drawArrays(gl.TRIANGLES, 0, 3); frame = requestAnimationFrame(render) }
    const start = () => { if (!frame && !disposed) frame = requestAnimationFrame(render) }
    const move = (event: PointerEvent) => { const rect = element.getBoundingClientRect(); pointer = [(event.clientX - rect.left) / rect.width, 1 - (event.clientY - rect.top) / rect.height]; inside = true; start() }
    const leave = () => { inside = false }
    const visibility = () => { if (document.hidden) { cancelAnimationFrame(frame); frame = 0 } else start() }
    const lost = (event: Event) => { event.preventDefault(); cancelAnimationFrame(frame); frame = 0; element.classList.add('is-fallback') }
    const restored = () => {
      if (program) gl.deleteProgram(program); if (vs) gl.deleteShader(vs); if (fs) gl.deleteShader(fs)
      if (!createProgram()) { element.classList.add('is-fallback'); return }
      res = gl.getUniformLocation(program!, 'uResolution'); pos = gl.getUniformLocation(program!, 'uPointer'); active = gl.getUniformLocation(program!, 'uActive'); element.classList.remove('is-fallback'); resize(); start()
    }
    const observer = new IntersectionObserver(([entry]) => { inView = entry.isIntersecting; if (inView) start(); else { cancelAnimationFrame(frame); frame = 0 } })
    const resizeObserver = new ResizeObserver(resize); resizeObserver.observe(element); observer.observe(element); resize()
    canvas.addEventListener('webglcontextlost', lost); canvas.addEventListener('webglcontextrestored', restored)
    element.addEventListener('pointermove', move); element.addEventListener('pointerleave', leave); document.addEventListener('visibilitychange', visibility); start()
    return () => { disposed = true; cancelAnimationFrame(frame); observer.disconnect(); resizeObserver.disconnect(); canvas.removeEventListener('webglcontextlost', lost); canvas.removeEventListener('webglcontextrestored', restored); element.removeEventListener('pointermove', move); element.removeEventListener('pointerleave', leave); document.removeEventListener('visibilitychange', visibility); if (program) gl.deleteProgram(program); if (vs) gl.deleteShader(vs); if (fs) gl.deleteShader(fs) }
  }, [reduce])
  return <div ref={host} className="glow-cursor is-fallback" aria-hidden="true"><canvas ref={canvasRef} /></div>
}
