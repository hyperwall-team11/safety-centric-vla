import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { KTX2Loader } from 'three/addons/loaders/KTX2Loader.js';
import { MeshoptDecoder } from 'three/addons/libs/meshopt_decoder.module.js';

export async function loadWarehouseVehicles(renderer, geoms) {
  const ktx = new KTX2Loader().setTranscoderPath('/node_modules/three/examples/jsm/libs/basis/');
  ktx.detectSupport(renderer);
  const loader = new GLTFLoader().setKTX2Loader(ktx).setMeshoptDecoder(MeshoptDecoder);
  let forklift, tug;
  try {
    [forklift, tug] = await Promise.all(['forklift', 'tug'].map(async name =>
      (await loader.loadAsync(`/models/warehouse-${name}.glb`)).scene));
  } finally {
    ktx.dispose();
  }

  const forkScale = 0.56;
  const carriage = forklift.getObjectByName('carriage');
  const forkBottom = new THREE.Box3().setFromObject(forklift.getObjectByName('fork_L')).min.y;
  const carriageRest = carriage.position.y - forkBottom;
  const forkIndex = geoms.findIndex(geom => geom.name === 'forklift_carriage');
  const chassisIndex = geoms.findIndex(geom => geom.name === 'forklift_chassis');
  tug.getObjectByName('tow_bar').removeFromParent();

  function mount(model, scale, position) {
    const anchor = new THREE.Group();
    model.position.set(...position);
    model.rotation.set(Math.PI / 2, Math.PI / 2, 0, 'XYZ');
    model.scale.setScalar(scale);
    model.traverse(node => {
      if (node.isMesh) node.castShadow = node.receiveShadow = true;
    });
    anchor.add(model);
    return anchor;
  }

  const anchors = {
    forklift_chassis: mount(forklift, forkScale, [-0.06, 0, -0.19]),
    warehouse_tug_frame: mount(tug, 0.19, [0, 0, -0.19]),
  };
  return {
    anchors,
    replaces(name) {
      return name.startsWith('forklift_') || name.startsWith('warehouse_tug_')
        || name === 'tug_control_housing' || name === 'tug_status_light';
    },
    update(frames, base) {
      const forkZ = frames[base + forkIndex * 12 + 2] - 0.12;
      const chassisZ = frames[base + chassisIndex * 12 + 2] - 0.19;
      carriage.position.y = carriageRest + (forkZ - chassisZ) / forkScale;
    },
  };
}
