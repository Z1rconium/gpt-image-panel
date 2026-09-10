import { prefersReducedMotion } from '$lib/motion';
import { supportsWebgl } from '$lib/webgl/support';
import type { OGLRenderingContext, Program, Texture } from 'ogl';

/**
 * The exposure: DESIGN.md's one sanctioned WebGL moment. A generated result
 * arrives on a single lit plane - tilted back and dim, it rotates flat into
 * full light as the well deepens (see the parallel CSS in app.css). Nothing
 * else in the product gets this treatment; see DESIGN.md Section 5.
 */

const VERTEX_SHADER = /* glsl */ `
  precision highp float;
  attribute vec3 position;
  attribute vec2 uv;
  uniform mat4 modelViewMatrix;
  uniform mat4 projectionMatrix;
  varying vec2 vUv;
  void main() {
    vUv = uv;
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

const FRAGMENT_SHADER = /* glsl */ `
  precision highp float;
  uniform sampler2D tMap;
  uniform float uLight;
  varying vec2 vUv;
  void main() {
    vec4 tex = texture2D(tMap, vUv);
    gl_FragColor = vec4(tex.rgb * uLight, tex.a);
  }
`;

const START_TILT_RAD = 0.15; // ~8.6deg, "tilted back" per DESIGN.md
const START_SCALE = 0.97;
const START_LIGHT = 0.4;
const FADE_IN_MS = 120;
const CAMERA_FOV_DEG = 35;
const CAMERA_DISTANCE = 5;

/** cubic-bezier(0.2, 0, 0, 1) - the --ease-std token, evaluated in JS. */
const EASE_STANDARD = cubicBezierEasing(0.2, 0, 0, 1);

function cubicBezierEasing(x1: number, y1: number, x2: number, y2: number) {
  const a = (a1: number, a2: number) => 1 - 3 * a2 + 3 * a1;
  const b = (a1: number, a2: number) => 3 * a2 - 6 * a1;
  const c = (a1: number) => 3 * a1;
  const bezier = (t: number, a1: number, a2: number) => ((a(a1, a2) * t + b(a1, a2)) * t + c(a1)) * t;
  const slope = (t: number, a1: number, a2: number) => 3 * a(a1, a2) * t * t + 2 * b(a1, a2) * t + c(a1);
  const tForX = (x: number) => {
    let t = x;
    for (let i = 0; i < 8; i += 1) {
      const dx = bezier(t, x1, x2) - x;
      if (Math.abs(dx) < 1e-6) return t;
      const d = slope(t, x1, x2);
      if (Math.abs(d) < 1e-6) break;
      t -= dx / d;
    }
    return t;
  };
  return (x: number) => {
    if (x <= 0) return 0;
    if (x >= 1) return 1;
    return bezier(tForX(x), y1, y2);
  };
}

/** Never a shortened version of the reveal - only ever call runExposure() when this is true. */
export function canRunExposure(): boolean {
  return !prefersReducedMotion() && supportsWebgl();
}

export type ExposureHandle = {
  /** Resolves once the reveal (and its brief fade-in) has finished, or resolves early if cancelled/lost. */
  done: Promise<void>;
  cancel: () => void;
};

/**
 * Renders the exposure once into `canvas`, texturing a lit plane with
 * `image` (already loaded). Resolves after `durationMs`. Safe to call again
 * on the same canvas for a later result - each call builds its own
 * program/texture and disposes them on completion.
 */
export function runExposure(canvas: HTMLCanvasElement, image: HTMLImageElement, durationMs: number): ExposureHandle {
  let cancelled = false;
  let rafId = 0;
  let contextLost = false;

  const onContextLost = (event: Event) => {
    event.preventDefault();
    contextLost = true;
    cancelled = true;
  };
  canvas.addEventListener('webglcontextlost', onContextLost, { once: true });

  const done = (async () => {
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    if (cancelled || !width || !height) return;

    let gl: OGLRenderingContext | null = null;
    let texture: Texture | null = null;
    let program: Program | null = null;

    try {
      const { Renderer, Camera, Transform, Mesh, Program, Texture, Plane } = await import('ogl');
      if (cancelled) return;

      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const renderer = new Renderer({ canvas, alpha: true, antialias: true, depth: false, dpr });
      gl = renderer.gl;
      if (!gl) return;
      renderer.setSize(width, height);

      const camera = new Camera(gl, { fov: CAMERA_FOV_DEG, aspect: width / height, near: 0.1, far: 100 });
      camera.position.z = CAMERA_DISTANCE;

      const fovRad = (CAMERA_FOV_DEG * Math.PI) / 180;
      const visibleHeight = 2 * Math.tan(fovRad / 2) * CAMERA_DISTANCE;
      const visibleWidth = visibleHeight * (width / height);

      const scene = new Transform();
      const geometry = new Plane(gl, { width: visibleWidth, height: visibleHeight });
      texture = new Texture(gl, { image, generateMipmaps: false, minFilter: gl.LINEAR, flipY: true });
      program = new Program(gl, {
        vertex: VERTEX_SHADER,
        fragment: FRAGMENT_SHADER,
        uniforms: { tMap: { value: texture }, uLight: { value: START_LIGHT } },
        depthTest: false,
        depthWrite: false,
        cullFace: false
      });
      const mesh = new Mesh(gl, { geometry, program });
      mesh.setParent(scene);

      await new Promise<void>((resolve) => {
        const start = performance.now();
        const tick = (now: number) => {
          if (cancelled) {
            resolve();
            return;
          }
          const elapsed = now - start;
          const linear = Math.min(1, elapsed / durationMs);
          const eased = EASE_STANDARD(linear);

          mesh.rotation.x = START_TILT_RAD * (1 - eased);
          const scale = START_SCALE + (1 - START_SCALE) * eased;
          mesh.scale.set(scale, scale, 1);
          if (program) {
            program.uniforms.uLight.value = START_LIGHT + (1 - START_LIGHT) * eased;
          }
          canvas.style.opacity = String(Math.min(1, elapsed / FADE_IN_MS));

          renderer.render({ scene, camera });

          if (linear < 1) {
            rafId = requestAnimationFrame(tick);
          } else {
            resolve();
          }
        };
        rafId = requestAnimationFrame(tick);
      });
    } catch {
      // Gracefully exit and allow plain CSS transition fallback
    } finally {
      if (gl && !contextLost) {
        if (texture?.texture) gl.deleteTexture(texture.texture);
        if (program?.program) gl.deleteProgram(program.program);
        gl.getExtension('WEBGL_lose_context')?.loseContext();
      }
      canvas.style.opacity = '';
      canvas.removeEventListener('webglcontextlost', onContextLost);
    }
  })();

  return {
    done,
    cancel() {
      cancelled = true;
      if (rafId) cancelAnimationFrame(rafId);
    }
  };
}
