/**
 * The only module that touches OGL. It re-exports the named classes the
 * exposure uses so the bundler can tree-shake the engine, and the exposure
 * dynamically imports this adapter (never `ogl` directly) to keep the
 * renderer off the homepage dependency graph.
 */
export { Camera, Mesh, Plane, Program, Renderer, Texture, Transform } from 'ogl';
