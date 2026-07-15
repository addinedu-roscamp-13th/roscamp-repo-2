import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { RefreshCw, RotateCcw } from "lucide-react";

// ── 공개 타입 ────────────────────────────────────────────────
export interface Twin3DHandle {
  resetCamera(): void;
  rebuild(): void;
}

export interface MapData3D {
  width: number;
  height: number;
  resolution: number;
  origin_x: number;
  origin_y: number;
  data: number[];
}
export interface OdomData3D { x: number; y: number; yaw: number; }
export interface ExploreData3D {
  frontiers: { x: number; y: number; size: number }[];
  current_goal: { x: number; y: number } | null;
}

interface Props {
  mapData?: MapData3D | null;
  odomData: OdomData3D | null;
  exploreData?: ExploreData3D | null;
  pathData?: { x: number; y: number }[];
  height?: number;
}

// ── Three.js 빌더 함수 ───────────────────────────────────────
function buildWallMesh(
  scene: THREE.Scene, map: MapData3D, prev: THREE.InstancedMesh | null,
): THREE.InstancedMesh {
  if (prev) { scene.remove(prev); prev.dispose(); }
  const { width, height, resolution, origin_x, origin_y, data } = map;
  const WALL_H = 0.35;
  let count = 0;
  for (const v of data) if (v > 0) count++;
  const geo = new THREE.BoxGeometry(resolution, WALL_H, resolution);
  const mat = new THREE.MeshStandardMaterial({ color: 0x475569, roughness: 0.7 });
  const mesh = new THREE.InstancedMesh(geo, mat, Math.max(count, 1));
  mesh.castShadow = true;
  const m4 = new THREE.Matrix4();
  let idx = 0;
  for (let r = 0; r < height; r++) {
    for (let c = 0; c < width; c++) {
      if (data[r * width + c] > 0) {
        const wx = origin_x + (c + 0.5) * resolution;
        const wy = origin_y + (r + 0.5) * resolution;
        m4.makeTranslation(wx, WALL_H / 2, -wy);
        mesh.setMatrixAt(idx++, m4);
      }
    }
  }
  mesh.instanceMatrix.needsUpdate = true;
  scene.add(mesh);
  return mesh;
}

function buildFloorMesh(
  scene: THREE.Scene, map: MapData3D, prev: THREE.Mesh | null,
): THREE.Mesh {
  if (prev) {
    scene.remove(prev);
    prev.geometry.dispose();
    (prev.material as THREE.Material).dispose();
  }
  const { width, height, resolution, origin_x, origin_y, data } = map;
  const canvas = document.createElement("canvas");
  canvas.width = width; canvas.height = height;
  const ctx = canvas.getContext("2d")!;
  const imgData = ctx.createImageData(width, height);
  const px = imgData.data;
  for (let r = 0; r < height; r++) {
    for (let c = 0; c < width; c++) {
      const v = data[r * width + c];
      const i = ((height - 1 - r) * width + c) * 4;
      if (v === 0)    { px[i]=220; px[i+1]=230; px[i+2]=240; px[i+3]=255; }
      else if (v > 0) { px[i]=50;  px[i+1]=60;  px[i+2]=75;  px[i+3]=255; }
      else            { px[i]=100; px[i+1]=110;  px[i+2]=125; px[i+3]=255; }
    }
  }
  ctx.putImageData(imgData, 0, 0);
  const tex = new THREE.CanvasTexture(canvas);
  tex.magFilter = THREE.NearestFilter;
  const fw = width * resolution, fh = height * resolution;
  const geo = new THREE.PlaneGeometry(fw, fh);
  const mat = new THREE.MeshStandardMaterial({ map: tex, roughness: 1, metalness: 0 });
  const floorMesh = new THREE.Mesh(geo, mat);
  floorMesh.rotation.x = -Math.PI / 2;
  floorMesh.position.set(origin_x + fw / 2, 0, -(origin_y + fh / 2));
  floorMesh.receiveShadow = true;
  scene.add(floorMesh);
  return floorMesh;
}

// ── 컴포넌트 ─────────────────────────────────────────────────
export const Twin3DCanvas = forwardRef<Twin3DHandle, Props>(
  function Twin3DCanvas({ mapData, odomData, exploreData, pathData, height = 500 }, ref) {
    const canvasRef = useRef<HTMLCanvasElement>(null);

    // 애니메이션 루프에서 읽을 내부 refs
    const mapRef     = useRef<MapData3D | null>(null);
    const odomRef    = useRef<OdomData3D | null>(null);
    const exploreRef = useRef<ExploreData3D | null>(null);
    const pathRef    = useRef<{ x: number; y: number }[]>([]);

    // 외부 ref 명령용
    const resetCameraFn  = useRef<() => void>(() => {});
    const forceRebuildRef = useRef(false);

    // props → refs 동기화
    useEffect(() => { mapRef.current = mapData ?? null; }, [mapData]);
    useEffect(() => { odomRef.current = odomData; }, [odomData]);
    useEffect(() => { exploreRef.current = exploreData ?? null; }, [exploreData]);
    useEffect(() => { pathRef.current = pathData ?? []; }, [pathData]);

    useImperativeHandle(ref, () => ({
      resetCamera: () => resetCameraFn.current(),
      rebuild:     () => { forceRebuildRef.current = true; },
    }));

    // ── Three.js 씬 초기화 (마운트 시 한 번) ────────────────
    useEffect(() => {
      const canvas = canvasRef.current;
      if (!canvas) return;

      const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
      renderer.setPixelRatio(window.devicePixelRatio);
      renderer.shadowMap.enabled = true;
      renderer.shadowMap.type = THREE.PCFSoftShadowMap;

      const scene = new THREE.Scene();
      scene.background = new THREE.Color(0x1e293b);
      scene.fog = new THREE.Fog(0x1e293b, 15, 40);

      const defaultCamPos = new THREE.Vector3(0, 6, 8);
      const camera = new THREE.PerspectiveCamera(55, canvas.clientWidth / canvas.clientHeight, 0.05, 100);
      camera.position.copy(defaultCamPos);
      camera.lookAt(0, 0, 0);

      const controls = new OrbitControls(camera, renderer.domElement);
      controls.enableDamping = true;
      controls.dampingFactor = 0.08;
      controls.minDistance = 1;
      controls.maxDistance = 30;
      controls.maxPolarAngle = Math.PI / 2.05;

      const setDefaultCamera = () => {
        camera.position.copy(defaultCamPos);
        controls.target.set(0, 0, 0);
        controls.update();
      };
      resetCameraFn.current = setDefaultCamera;

      // 조명
      scene.add(new THREE.AmbientLight(0xddeeff, 0.5));
      const sun = new THREE.DirectionalLight(0xfff8e7, 1.2);
      sun.position.set(5, 12, 8);
      sun.castShadow = true;
      sun.shadow.mapSize.set(1024, 1024);
      sun.shadow.camera.near = 0.5; sun.shadow.camera.far = 50;
      sun.shadow.camera.left = -15; sun.shadow.camera.right = 15;
      sun.shadow.camera.top = 15;   sun.shadow.camera.bottom = -15;
      scene.add(sun);
      scene.add(new THREE.HemisphereLight(0x334455, 0x1e293b, 0.4));
      scene.add(new THREE.GridHelper(30, 60, 0x2d3f52, 0x243447));

      // 로봇
      const robotGroup = new THREE.Group();
      const body = new THREE.Mesh(
        new THREE.CylinderGeometry(0.105, 0.105, 0.06, 24),
        new THREE.MeshStandardMaterial({ color: 0xef4444, roughness: 0.4, metalness: 0.3 }),
      );
      body.position.y = 0.03; body.castShadow = true;
      const arrow = new THREE.Mesh(
        new THREE.ConeGeometry(0.05, 0.14, 8),
        new THREE.MeshStandardMaterial({ color: 0xfbbf24, roughness: 0.3 }),
      );
      arrow.rotation.z = -Math.PI / 2; arrow.position.set(0.15, 0.03, 0);
      const sensor = new THREE.Mesh(
        new THREE.CylinderGeometry(0.025, 0.025, 0.04, 8),
        new THREE.MeshStandardMaterial({ color: 0x60a5fa, emissive: 0x3b82f6, emissiveIntensity: 0.5 }),
      );
      sensor.position.y = 0.08;
      robotGroup.add(body, arrow, sensor);
      scene.add(robotGroup);

      // 목표 링
      const goalRing = new THREE.Mesh(
        new THREE.RingGeometry(0.12, 0.18, 24),
        new THREE.MeshBasicMaterial({ color: 0x22c55e, side: THREE.DoubleSide }),
      );
      goalRing.rotation.x = -Math.PI / 2;
      goalRing.position.y = 0.01;
      goalRing.visible = false;
      scene.add(goalRing);

      // 경로 라인 (Dead reckoning 대응용)
      const lineMat = new THREE.LineBasicMaterial({ color: 0x6366f1, opacity: 0.7, transparent: true });
      const lineGeo = new THREE.BufferGeometry();
      const line = new THREE.Line(lineGeo, lineMat);
      scene.add(line);

      // 동적 씬 오브젝트
      let wallMesh: THREE.InstancedMesh | null = null;
      let floorMesh: THREE.Mesh | null = null;
      let frontierMesh: THREE.InstancedMesh | null = null;
      let lastMapKey = "";
      let lastFrontierCount = -1;
      let prevPathLen = -1;

      const onResize = () => {
        const w = canvas.clientWidth, h = canvas.clientHeight;
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        renderer.setSize(w, h, false);
      };
      const ro = new ResizeObserver(onResize);
      ro.observe(canvas);
      onResize();

      let animId: number;
      const animate = () => {
        animId = requestAnimationFrame(animate);
        controls.update();

        const map     = mapRef.current;
        const odom    = odomRef.current;
        const explore = exploreRef.current;
        const path    = pathRef.current;

        // 맵 변경 또는 강제 재빌드
        if (map) {
          const mapKey = `${map.width}x${map.height}x${map.data.length}`;
          if (mapKey !== lastMapKey || forceRebuildRef.current) {
            lastMapKey = mapKey;
            forceRebuildRef.current = false;
            wallMesh  = buildWallMesh(scene, map, wallMesh);
            floorMesh = buildFloorMesh(scene, map, floorMesh);
            lastFrontierCount = -1;
            const cx = map.origin_x + (map.width  * map.resolution) / 2;
            const cy = map.origin_y + (map.height * map.resolution) / 2;
            controls.target.set(cx, 0, -cy);
            camera.position.set(cx, 6, -cy + 8);
            resetCameraFn.current = () => {
              camera.position.set(cx, 6, -cy + 8);
              controls.target.set(cx, 0, -cy);
              controls.update();
            };
          }
        } else if (lastMapKey !== "") {
          lastMapKey = "";
          if (wallMesh)     { scene.remove(wallMesh);     wallMesh.dispose();                                     wallMesh = null; }
          if (floorMesh)    { scene.remove(floorMesh);    (floorMesh.material as THREE.Material).dispose();      floorMesh = null; }
          if (frontierMesh) { scene.remove(frontierMesh); frontierMesh.dispose(); lastFrontierCount = -1;        frontierMesh = null; }
          goalRing.visible = false;
          robotGroup.position.set(0, 0, 0);
          controls.target.set(0, 0, 0);
          camera.position.copy(defaultCamPos);
          resetCameraFn.current = setDefaultCamera;
        }

        // 로봇
        if (odom) {
          robotGroup.position.set(odom.x, 0, -odom.y);
          robotGroup.rotation.y = odom.yaw;
          robotGroup.visible = true;
        } else {
          robotGroup.visible = false;
        }

        // 프론티어 + 목표
        if (explore) {
          const fc = explore.frontiers.length;
          if (fc !== lastFrontierCount) {
            lastFrontierCount = fc;
            if (frontierMesh) { scene.remove(frontierMesh); frontierMesh.dispose(); frontierMesh = null; }
            if (fc > 0) {
              const fGeo = new THREE.SphereGeometry(0.07, 8, 6);
              const fMat = new THREE.MeshStandardMaterial({ color: 0x6366f1, emissive: 0x4338ca, emissiveIntensity: 0.4 });
              frontierMesh = new THREE.InstancedMesh(fGeo, fMat, fc);
              const m4 = new THREE.Matrix4();
              explore.frontiers.forEach((f, i) => {
                m4.makeTranslation(f.x, 0.12, -f.y);
                frontierMesh!.setMatrixAt(i, m4);
              });
              frontierMesh.instanceMatrix.needsUpdate = true;
              scene.add(frontierMesh);
            }
          }
          if (explore.current_goal) {
            goalRing.position.set(explore.current_goal.x, 0.01, -explore.current_goal.y);
            goalRing.visible = true;
          } else {
            goalRing.visible = false;
          }
        } else {
          if (frontierMesh) { scene.remove(frontierMesh); frontierMesh.dispose(); frontierMesh = null; lastFrontierCount = -1; }
          goalRing.visible = false;
        }

        // 경로 라인 (Dead reckoning 용)
        if (path && path.length !== prevPathLen) {
          prevPathLen = path.length;
          if (path.length > 1) {
            const pts = path.map((p) => new THREE.Vector3(p.x, 0.02, -p.y));
            lineGeo.setFromPoints(pts);
            line.visible = true;
          } else {
            lineGeo.setFromPoints([]);
            line.visible = false;
          }
        } else if (!path) {
          line.visible = false;
        }

        renderer.render(scene, camera);
      };
      animate();

      return () => {
        cancelAnimationFrame(animId);
        ro.disconnect();
        controls.dispose();
        renderer.dispose();
        wallMesh?.dispose();
        (floorMesh?.material as THREE.Material)?.dispose();
        frontierMesh?.dispose();
        lineGeo.dispose();
        lineMat.dispose();
      };
    }, []);

    return (
      <div
        className="relative overflow-hidden rounded-xl border border-slate-700 bg-slate-900 shadow-md"
        style={{ height }}
      >
        <canvas ref={canvasRef} className="h-full w-full" />

        {/* 오버레이 버튼 */}
        <div className="absolute bottom-3 right-3 flex gap-1.5">
          <button
            onClick={() => resetCameraFn.current()}
            className="flex items-center gap-1.5 rounded-lg bg-black/40 px-2.5 py-1.5 text-[11px] text-white backdrop-blur hover:bg-black/60"
            title="카메라 위치를 기본값으로 되돌립니다"
          >
            <RotateCcw className="size-3" /> 카메라 초기화
          </button>
          {mapData && (
            <button
              onClick={() => { forceRebuildRef.current = true; }}
              className="flex items-center gap-1.5 rounded-lg bg-black/40 px-2.5 py-1.5 text-[11px] text-white backdrop-blur hover:bg-black/60"
              title="벽·바닥·프론티어를 현재 맵 데이터로 다시 빌드합니다"
            >
              <RefreshCw className="size-3" /> 씬 재빌드
            </button>
          )}
        </div>

        {/* 범례 */}
        <div className="absolute bottom-3 left-3 flex flex-col gap-1 rounded-lg bg-black/40 px-2.5 py-2 text-[10px] text-slate-300 backdrop-blur">
          {mapData ? (
            <>
              <LegItem color="#dcecf8" label="자유 공간" />
              <LegItem color="#475569" label="벽" />
              <LegItem color="#6366f1" label="프론티어" />
              <LegItem color="#22c55e" label="현재 목표" />
              <LegItem color="#ef4444" label="로봇" />
            </>
          ) : (
            <>
              <LegItem color="#ef4444" label="로봇" />
              <LegItem color="#6366f1" label="이동 경로" />
            </>
          )}
        </div>

        {/* 조작 안내 */}
        <div className="absolute top-3 right-3 rounded-lg bg-black/40 px-2.5 py-1.5 text-[10px] text-slate-400 backdrop-blur leading-5">
          🖱 드래그·우클릭·휠
        </div>

        {!pathData?.length && (
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
            <p className="text-slate-500 text-[13px]">탐색을 시작하면 3D 경로가 표시됩니다</p>
          </div>
        )}
      </div>
    );
  },
);

function LegItem({ color, label }: { color: string; label: string }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm" style={{ background: color }} />
      {label}
    </div>
  );
}
