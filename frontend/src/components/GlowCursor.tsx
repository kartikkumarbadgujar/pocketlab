import { useEffect, useRef } from 'react'
import { useReducedMotion } from 'motion/react'
import './GlowCursor.css'

const vertexSource = `#version 300 es
void main(){ vec2 p=vec2(float((gl_VertexID<<1)&2),float(gl_VertexID&2)); gl_Position=vec4(p*2.0-1.0,0.0,1.0); }`
const fragmentSource = `#version 300 es
precision highp float;
uniform vec2 uResolution; uniform vec2 uPointer; uniform float uActive; out vec4 outColor;
void main(){ vec2 uv=gl_FragCoord.xy/uResolution; float aspect=uResolution.x/uResolution.y; vec2 delta=(uv-uPointer)*vec2(aspect,1.0); float d=length(delta); float glow=exp(-d*7.2)*uActive; vec3 teal=vec3(0.0588,0.4627,0.4314); outColor=vec4(teal*glow*0.48,glow*0.42); }`

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
  const reduceMotion = useReducedMotion()

  useEffect(() => {
    const element = host.current, canvas = canvasRef.current
    if (!element || !canvas || reduceMotion || !matchMedia('(pointer: fine)').matches) return

    let gl: WebGL2RenderingContext | null = canvas.getContext('webgl2', {
      alpha: true, antialias: false, depth: false, stencil: false, powerPreference: 'low-power',
    })
    let program: WebGLProgram | null = null
    let vertex: WebGLShader | null = null
    let fragment: WebGLShader | null = null
    let frame = 0
    let disposed = false
    let inView = true
    let active = 0
    let targetActive = 0
    let pointer: [number, number] = [0.5, 0.5]
    let targetPointer: [number, number] = [0.5, 0.5]
    let clientPointer: [number, number] = [innerWidth / 2, innerHeight / 2]
    let resolutionLocation: WebGLUniformLocation | null = null
    let pointerLocation: WebGLUniformLocation | null = null
    let activeLocation: WebGLUniformLocation | null = null

    const createProgram = () => {
      if (!gl) return false
      vertex = shader(gl, gl.VERTEX_SHADER, vertexSource)
      fragment = shader(gl, gl.FRAGMENT_SHADER, fragmentSource)
      program = gl.createProgram()
      if (!vertex || !fragment || !program) return false
      gl.attachShader(program, vertex); gl.attachShader(program, fragment); gl.linkProgram(program)
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return false
      resolutionLocation = gl.getUniformLocation(program, 'uResolution')
      pointerLocation = gl.getUniformLocation(program, 'uPointer')
      activeLocation = gl.getUniformLocation(program, 'uActive')
      return true
    }

    const resize = () => {
      if (!gl) return
      const rect = element.getBoundingClientRect(), dpr = Math.min(devicePixelRatio || 1, 1.25)
      canvas.width = Math.max(1, Math.round(rect.width * dpr))
      canvas.height = Math.max(1, Math.round(rect.height * dpr))
      gl.viewport(0, 0, canvas.width, canvas.height)
      targetPointer = [(clientPointer[0] - rect.left) / rect.width, 1 - (clientPointer[1] - rect.top) / rect.height]
      schedule()
    }

    const draw = () => {
      frame = 0
      if (disposed || document.hidden || !inView || !gl || !program) return
      pointer[0] += (targetPointer[0] - pointer[0]) * 0.24
      pointer[1] += (targetPointer[1] - pointer[1]) * 0.24
      active += (targetActive - active) * 0.24
      gl.clearColor(0, 0, 0, 0); gl.clear(gl.COLOR_BUFFER_BIT); gl.useProgram(program)
      gl.uniform2f(resolutionLocation, canvas.width, canvas.height)
      gl.uniform2f(pointerLocation, pointer[0], pointer[1])
      gl.uniform1f(activeLocation, active)
      gl.drawArrays(gl.TRIANGLES, 0, 3)
      const settled = Math.abs(targetPointer[0] - pointer[0]) < 0.001
        && Math.abs(targetPointer[1] - pointer[1]) < 0.001
        && Math.abs(targetActive - active) < 0.001
      if (!settled) frame = requestAnimationFrame(draw)
    }
    function schedule() { if (!frame && !disposed) frame = requestAnimationFrame(draw) }

    const move = (event: PointerEvent) => {
      if (event.pointerType === 'touch') return
      clientPointer = [event.clientX, event.clientY]
      const rect = element.getBoundingClientRect()
      targetPointer = [(event.clientX - rect.left) / rect.width, 1 - (event.clientY - rect.top) / rect.height]
      targetActive = 1
      element.style.setProperty('--cursor-x', `${event.clientX}px`)
      element.style.setProperty('--cursor-y', `${event.clientY}px`)
      element.style.setProperty('--cursor-active', '1')
      schedule()
    }
    const leave = (event: PointerEvent) => {
      if (event.relatedTarget !== null) return
      targetActive = 0
      element.style.setProperty('--cursor-active', '0')
      schedule()
    }
    const visibility = () => {
      if (document.hidden) { cancelAnimationFrame(frame); frame = 0 }
      else schedule()
    }
    const lost = (event: Event) => { event.preventDefault(); cancelAnimationFrame(frame); frame = 0; element.classList.add('is-fallback') }
    const restored = () => {
      if (!gl) return
      if (program) gl.deleteProgram(program)
      if (vertex) gl.deleteShader(vertex)
      if (fragment) gl.deleteShader(fragment)
      if (!createProgram()) { element.classList.add('is-fallback'); return }
      element.classList.remove('is-fallback'); resize(); schedule()
    }

    element.classList.add('is-fallback')
    if (gl && createProgram()) element.classList.remove('is-fallback')
    else { if (program && gl) gl.deleteProgram(program); program = null; gl = null }

    const intersectionObserver = new IntersectionObserver(([entry]) => {
      inView = entry.isIntersecting
      if (inView) schedule()
      else { cancelAnimationFrame(frame); frame = 0 }
    })
    const resizeObserver = new ResizeObserver(resize)
    resizeObserver.observe(element); intersectionObserver.observe(element); resize()
    canvas.addEventListener('webglcontextlost', lost)
    canvas.addEventListener('webglcontextrestored', restored)
    window.addEventListener('pointermove', move, { passive: true })
    window.addEventListener('pointerout', leave, { passive: true })
    document.addEventListener('visibilitychange', visibility)

    return () => {
      disposed = true; cancelAnimationFrame(frame)
      intersectionObserver.disconnect(); resizeObserver.disconnect()
      canvas.removeEventListener('webglcontextlost', lost); canvas.removeEventListener('webglcontextrestored', restored)
      window.removeEventListener('pointermove', move); window.removeEventListener('pointerout', leave)
      document.removeEventListener('visibilitychange', visibility)
      if (program && gl) gl.deleteProgram(program)
      if (vertex && gl) gl.deleteShader(vertex)
      if (fragment && gl) gl.deleteShader(fragment)
    }
  }, [reduceMotion])

  return <div ref={host} className="glow-cursor is-fallback" aria-hidden="true"><canvas ref={canvasRef} /></div>
}

