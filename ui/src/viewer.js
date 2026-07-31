/**
 * Minimal Three.js model viewer for plugin HTML panes.
 * Built to ui/viewer.bundle.js via scripts/build_ui.mjs.
 */
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { FBXLoader } from "three/examples/jsm/loaders/FBXLoader.js";
import { DRACOLoader } from "three/examples/jsm/loaders/DRACOLoader.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { OBJLoader } from "three/examples/jsm/loaders/OBJLoader.js";
import { MTLLoader } from "three/examples/jsm/loaders/MTLLoader.js";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { PLYLoader } from "three/examples/jsm/loaders/PLYLoader.js";
import { ColladaLoader } from "three/examples/jsm/loaders/ColladaLoader.js";

const DRACO_PATH = "/plugin-ui/uasset-preview/ui/draco/";

let sharedDraco = null;
function getDraco() {
  if (!sharedDraco) {
    sharedDraco = new DRACOLoader();
    sharedDraco.setDecoderPath(DRACO_PATH);
  }
  return sharedDraco;
}

function fitCamera(camera, controls, object, margin = 1.35) {
  const box = new THREE.Box3().setFromObject(object);
  const size = box.getSize(new THREE.Vector3());
  const center = box.getCenter(new THREE.Vector3());
  const maxDim = Math.max(size.x, size.y, size.z, 0.001);
  const fov = (camera.fov * Math.PI) / 180;
  let dist = (maxDim / (2 * Math.tan(fov / 2))) * margin;
  dist = Math.max(dist, maxDim * 0.8);
  camera.position.set(center.x + dist * 0.6, center.y + dist * 0.45, center.z + dist * 0.6);
  camera.near = Math.max(dist / 100, 0.01);
  camera.far = dist * 100;
  camera.updateProjectionMatrix();
  controls.target.copy(center);
  controls.update();
}

function urlModifier(baseUrl) {
  return (url) => {
    if (!url || /^https?:\/\//i.test(url) || url.startsWith("data:") || url.startsWith("blob:")) {
      return url;
    }
    const name = url.split(/[\\/]/).pop() || url;
    return baseUrl + encodeURIComponent(name);
  };
}

function loadModel(url, baseUrl, filename) {
  const lower = (filename || url).toLowerCase();
  const manager = new THREE.LoadingManager();
  manager.setURLModifier(urlModifier(baseUrl.endsWith("/") ? baseUrl : baseUrl + "/"));

  return new Promise((resolve, reject) => {
    const onErr = (e) => reject(e instanceof Error ? e : new Error(String(e)));
    if (lower.endsWith(".glb") || lower.endsWith(".gltf")) {
      const loader = new GLTFLoader(manager);
      loader.setDRACOLoader(getDraco());
      loader.load(url, (g) => resolve({ root: g.scene, animations: g.animations || [] }), undefined, onErr);
    } else if (lower.endsWith(".fbx")) {
      new FBXLoader(manager).load(url, (obj) => resolve({ root: obj, animations: obj.animations || [] }), undefined, onErr);
    } else if (lower.endsWith(".obj")) {
      const mtlUrl = url.replace(/\.obj$/i, ".mtl");
      const mtlLoader = new MTLLoader(manager);
      mtlLoader.load(
        mtlUrl,
        (mats) => {
          mats.preload();
          const objLoader = new OBJLoader(manager);
          objLoader.setMaterials(mats);
          objLoader.load(url, (obj) => resolve({ root: obj, animations: [] }), undefined, onErr);
        },
        undefined,
        () => {
          new OBJLoader(manager).load(url, (obj) => resolve({ root: obj, animations: [] }), undefined, onErr);
        },
      );
    } else if (lower.endsWith(".stl")) {
      new STLLoader(manager).load(
        url,
        (geo) => {
          const mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color: 0xcccccc, metalness: 0.1, roughness: 0.7 }));
          resolve({ root: mesh, animations: [] });
        },
        undefined,
        onErr,
      );
    } else if (lower.endsWith(".ply")) {
      new PLYLoader(manager).load(
        url,
        (geo) => {
          geo.computeVertexNormals();
          const mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color: 0xcccccc, metalness: 0.1, roughness: 0.7 }));
          resolve({ root: mesh, animations: [] });
        },
        undefined,
        onErr,
      );
    } else if (lower.endsWith(".dae")) {
      new ColladaLoader(manager).load(url, (col) => resolve({ root: col.scene, animations: [] }), undefined, onErr);
    } else {
      reject(new Error("Unsupported model format"));
    }
  });
}

/**
 * Mount a viewer into ``hostEl``.
 * @returns {{ dispose: () => void, setMedia: (m: {media_url, media_base_url, media_filename}) => Promise<void> }}
 */
export function createViewer(hostEl, options = {}) {
  const maxPixelRatio = options.maxPixelRatio ?? 2;
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x1a1a1e);
  const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 2000);
  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, maxPixelRatio));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  hostEl.appendChild(renderer.domElement);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.autoRotate = false;

  const ambient = new THREE.AmbientLight(0xffffff, 0.55);
  const key = new THREE.DirectionalLight(0xffffff, 0.9);
  key.position.set(4, 8, 6);
  const fill = new THREE.DirectionalLight(0xffffff, 0.35);
  fill.position.set(-6, 2, -4);
  scene.add(ambient, key, fill);

  const grid = new THREE.GridHelper(10, 20, 0x444444, 0x333333);
  scene.add(grid);

  let root = null;
  let raf = 0;
  let disposed = false;

  function resize() {
    const w = hostEl.clientWidth || 1;
    const h = hostEl.clientHeight || 1;
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h, false);
  }

  function tick() {
    if (disposed) return;
    controls.update();
    renderer.render(scene, camera);
    raf = requestAnimationFrame(tick);
  }

  const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(resize) : null;
  ro?.observe(hostEl);
  resize();
  tick();

  async function setMedia(media) {
    if (!media?.media_url) throw new Error("No media URL");
    if (root) {
      scene.remove(root);
      root.traverse((o) => {
        if (o.geometry) o.geometry.dispose?.();
        if (o.material) {
          const mats = Array.isArray(o.material) ? o.material : [o.material];
          for (const m of mats) m.dispose?.();
        }
      });
      root = null;
    }
    const loaded = await loadModel(
      media.media_url,
      media.media_base_url || media.media_url.replace(/[^/]+$/, ""),
      media.media_filename || "model.fbx",
    );
    root = loaded.root;
    scene.add(root);
    fitCamera(camera, controls, root);
  }

  function dispose() {
    disposed = true;
    cancelAnimationFrame(raf);
    ro?.disconnect();
    controls.dispose();
    renderer.dispose();
    if (renderer.domElement.parentNode) renderer.domElement.parentNode.removeChild(renderer.domElement);
  }

  return {
    setMedia,
    dispose,
    setAutoRotate(on) {
      controls.autoRotate = !!on;
    },
    setGrid(on) {
      grid.visible = !!on;
    },
  };
}

window.UassetViewer = { createViewer };
