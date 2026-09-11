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
  attribute vec3 normal;
  attribute vec2 uv;
  uniform mat4 modelViewMatrix;
  uniform mat4 projectionMatrix;
  uniform mat3 normalMatrix;
  varying vec2 vUv;
  varying vec3 vNormal;
  void main() {
    vUv = uv;
    vNormal = normalize(normalMatrix * normal);
    gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
  }
`;

// The plane's normal rotates with the mesh, so lighting follows the reveal
// instead of a flat multiplier. Light comes from above and slightly toward
// the viewer; oriented lighting is normalized against the flat pose so the
// final frame keeps the image's original colour and alpha.
const FRAGMENT_SHADER = /* glsl */ `
  precision highp float;
  uniform sampler2D tMap;
  uniform float uLight;
  varying vec2 vUv;
  varying vec3 vNormal;
  void main() {
    vec3 lightDir = normalize(vec3(0.0, 1.0, 0.28));
    vec4 tex = texture2D(tMap, vUv);
    float oriented = max(dot(normalize(vNormal), lightDir), 0.0) / lightDir.z;
    float exposure = clamp(uLight * oriented, 0.0, 1.0);
    gl_FragColor = vec4(tex.rgb * exposure, tex.a);
  }
`;

const START_TILT_RAD = 0.15; // ~8.6deg, "tilted back" per DESIGN.md
const START_SCALE = 0.97;
const START_LIGHT = 0.4;
const FADE_IN_MS = 120;
const CAMERA_FOV_DEG = 35;
const CAMERA_DISTANCE = 5;
const OGL_READY_TIMEOUT_MS = 1200;

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

type ExposureResources = {
  gl: OGLRenderingContext | null;
  geometry: { remove: () => void } | null;
  texture: Texture | null;
  program: Program | null;
};

/**
 * Renders the exposure once into `canvas`, texturing a lit plane with
 * `image` (already loaded). Resolves after `durationMs`. Safe to call again
 * on the same canvas for a later result - each call builds its own
 * program/texture and disposes them on completion.
 *
 * Every exit path (normal completion, cancel, initialization failure, context
 * loss, zero size, unmount) funnels through the idempotent settle(), so `done`
 * always resolves and every owned GPU object and listener is released exactly
 * once.
 */
export function runExposure(canvas: HTMLCanvasElement, image: HTMLImageElement, durationMs: number): ExposureHandle {
  let settled = false;
  let cancelled = false;
  let rafId = 0;
  let contextLost = false;
  let resolveDone: () => void = () => {};
  const done = new Promise<void>((resolve) => {
    resolveDone = resolve;
  });

  const resources: ExposureResources = { gl: null, geometry: null, texture: null, program: null };

  function dispose() {
    const { gl, geometry, texture, program } = resources;
    if (gl && !contextLost) {
      try {
        geometry?.remove();
      } catch {
        // The context may already be gone; releasing what we can is best effort.
      }
      try {
        program?.remove();
      } catch {
        // Same as above.
      }
      if (texture?.texture) {
        try {
          gl.deleteTexture(texture.texture);
        } catch {
          // Same as above.
        }
      }
      gl.getExtension('WEBGL_lose_context')?.loseContext();
    }
    resources.gl = null;
    resources.geometry = null;
    resources.texture = null;
    resources.program = null;
  }

  /** Idempotent: resolves done, cancels the pending frame, and frees all resources. */
  function settle() {
    if (settled) return;
    settled = true;
    if (rafId) {
      cancelAnimationFrame(rafId);
      rafId = 0;
    }
    canvas.removeEventListener('webglcontextlost', onContextLost);
    if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', onVisibilityChange);
    dispose();
    canvas.style.opacity = '';
    resolveDone();
  }

  const onContextLost = (event: Event) => {
    event.preventDefault();
    contextLost = true;
    cancelled = true;
    settle();
  };
  canvas.addEventListener('webglcontextlost', onContextLost, { once: true });

  // A hidden tab should not keep rendering; finish as a static result instead.
  const onVisibilityChange = () => {
    if (document.visibilityState === 'hidden') {
      cancelled = true;
      settle();
    }
  };
  if (typeof document !== 'undefined') document.addEventListener('visibilitychange', onVisibilityChange);

  void (async () => {
    const width = canvas.clientWidth;
    const height = canvas.clientHeight;
    if (cancelled || settled || !width || !height) {
      settle();
      return;
    }

    try {
      const modulePromise = import('$lib/webgl/oglAdapter');
      const ready = await Promise.race([
        modulePromise,
        new Promise<null>((resolve) => setTimeout(() => resolve(null), OGL_READY_TIMEOUT_MS))
      ]);
      if (!ready) {
        // The 3D module never arrived in time; fall back to the CSS reveal.
        settle();
        return;
      }
      if (cancelled || settled) {
        settle();
        return;
      }
      const { Renderer, Camera, Transform, Mesh, Program, Texture, Plane } = ready;

      const dpr = Math.min(window.devicePixelRatio || 1, 2);
      const renderer = new Renderer({ canvas, alpha: true, antialias: true, depth: false, dpr });
      const gl = renderer.gl;
      if (!gl) {
        settle();
        return;
      }
      resources.gl = gl;
      renderer.setSize(width, height);

      // Never upload an image larger than the GPU can hold as a texture.
      const maxTextureSize = gl.getParameter(gl.MAX_TEXTURE_SIZE) as number;
      if (image.naturalWidth > maxTextureSize || image.naturalHeight > maxTextureSize) {
        settle();
        return;
      }

      const camera = new Camera(gl, { fov: CAMERA_FOV_DEG, aspect: width / height, near: 0.1, far: 100 });
      camera.position.z = CAMERA_DISTANCE;

      const fovRad = (CAMERA_FOV_DEG * Math.PI) / 180;
      const visibleHeight = 2 * Math.tan(fovRad / 2) * CAMERA_DISTANCE;
      const visibleWidth = visibleHeight * (width / height);

      const scene = new Transform();
      const geometry = new Plane(gl, { width: visibleWidth, height: visibleHeight });
      const texture = new Texture(gl, { image, generateMipmaps: false, minFilter: gl.LINEAR, flipY: true });
      const program = new Program(gl, {
        vertex: VERTEX_SHADER,
        fragment: FRAGMENT_SHADER,
        uniforms: { tMap: { value: texture }, uLight: { value: START_LIGHT } },
        depthTest: false,
        depthWrite: false,
        cullFace: false
      });
      resources.geometry = geometry;
      resources.texture = texture;
      resources.program = program;

      const mesh = new Mesh(gl, { geometry, program });
      mesh.setParent(scene);

      await new Promise<void>((resolve) => {
        const start = performance.now();
        const tick = (now: number) => {
          rafId = 0;
          if (cancelled || settled) {
            resolve();
            return;
          }
          const elapsed = now - start;
          const linear = Math.min(1, elapsed / durationMs);
          const eased = EASE_STANDARD(linear);

          mesh.rotation.x = START_TILT_RAD * (1 - eased);
          const scale = START_SCALE + (1 - START_SCALE) * eased;
          mesh.scale.set(scale, scale, 1);
          program.uniforms.uLight.value = START_LIGHT + (1 - START_LIGHT) * eased;
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
      settle();
    }
  })();

  return {
    done,
    cancel() {
      cancelled = true;
      settle();
    }
  };
}
