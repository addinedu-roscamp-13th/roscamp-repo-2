/**
 * JetCobot 로봇팔 3D 모형 — React Three Fiber
 *
 * 마우스로 관절 클릭 후 드래그 → 실제 팔 이동
 * 각 링크를 계층 구조로 구성해 FK 시각화
 */
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { OrbitControls, GizmoHelper, GizmoViewport } from "@react-three/drei";
import { useRef, useState, useCallback, useEffect } from "react";
import * as THREE from "three";

// ── 관절 정의 ─────────────────────────────────────────────────
const JOINT_COLORS = [
  "#6366f1", // J1 베이스 - indigo
  "#8b5cf6", // J2 숄더 - violet
  "#a855f7", // J3 엘보 - purple
  "#ec4899", // J4 리스트1 - pink
  "#f43f5e", // J5 리스트2 - rose
  "#ef4444", // J6 리스트3 - red
];

const JOINT_LIMITS = [
  [-168, 168], [-135, 135], [-150, 150],
  [-145, 145], [-165, 165], [-180, 180],
];

const LINK_LENGTHS = [0.8, 1.0, 0.9, 0.6, 0.5, 0.3]; // 상대 길이

interface ArmModel3DProps {
  angles: number[];          // J1~J6 (degrees)
  onAngleChange: (idx: number, deg: number) => void;
  selectedJoint: number | null;
  onSelectJoint: (idx: number | null) => void;
}

// ── 단일 링크 + 관절 메시 ──────────────────────────────────────
function ArmLink({
  jointIdx,
  angle,
  linkLength,
  color,
  selected,
  onSelect,
  children,
}: {
  jointIdx: number;
  angle: number;
  linkLength: number;
  color: string;
  selected: boolean;
  onSelect: () => void;
  children?: React.ReactNode;
}) {
  const groupRef = useRef<THREE.Group>(null);
  const rad = (angle * Math.PI) / 180;

  // 짝수 관절: Z축 회전, 홀수: X축 회전 (MyCobot 280 DH 근사)
  const rotAxis = jointIdx % 2 === 0
    ? new THREE.Euler(0, 0, rad)
    : new THREE.Euler(rad, 0, 0);

  return (
    <group ref={groupRef} rotation={rotAxis}>
      {/* 관절 구체 */}
      <mesh
        onClick={(e) => { e.stopPropagation(); onSelect(); }}
        position={[0, 0, 0]}
      >
        <sphereGeometry args={[0.12, 16, 16]} />
        <meshStandardMaterial
          color={selected ? "#fbbf24" : color}
          emissive={selected ? "#fbbf24" : color}
          emissiveIntensity={selected ? 0.4 : 0.1}
        />
      </mesh>

      {/* 링크 실린더 */}
      <mesh position={[0, linkLength / 2, 0]}>
        <cylinderGeometry args={[0.05, 0.07, linkLength, 12]} />
        <meshStandardMaterial color={selected ? "#fde68a" : "#cbd5e1"} />
      </mesh>

      {/* 자식 그룹 (다음 관절) */}
      <group position={[0, linkLength, 0]}>
        {children}
      </group>
    </group>
  );
}

// ── 드래그로 관절 각도 조작 ────────────────────────────────────
function DragController({
  selectedJoint,
  angles,
  onAngleChange,
}: {
  selectedJoint: number | null;
  angles: number[];
  onAngleChange: (idx: number, deg: number) => void;
}) {
  const { gl } = useThree();
  const dragRef = useRef<{ startY: number; startAngle: number } | null>(null);

  useEffect(() => {
    if (selectedJoint === null) return;

    const onMouseDown = (e: MouseEvent) => {
      dragRef.current = { startY: e.clientY, startAngle: angles[selectedJoint] };
    };
    const onMouseMove = (e: MouseEvent) => {
      if (!dragRef.current || selectedJoint === null) return;
      const dy = dragRef.current.startY - e.clientY; // 위로 드래그 = 양수
      const delta = dy * 0.8; // 픽셀 → 도
      const [min, max] = JOINT_LIMITS[selectedJoint];
      const next = Math.max(min, Math.min(max, dragRef.current.startAngle + delta));
      onAngleChange(selectedJoint, Math.round(next * 10) / 10);
    };
    const onMouseUp = () => { dragRef.current = null; };

    gl.domElement.addEventListener("mousedown", onMouseDown);
    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    return () => {
      gl.domElement.removeEventListener("mousedown", onMouseDown);
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    };
  }, [selectedJoint, angles, onAngleChange, gl]);

  return null;
}

// ── 전체 팔 모형 씬 ───────────────────────────────────────────
function ArmScene({
  angles,
  selectedJoint,
  onSelectJoint,
  onAngleChange,
}: ArmModel3DProps) {
  // 관절을 안쪽에서 바깥쪽으로 중첩
  const renderChain = (idx: number): React.ReactNode => {
    if (idx >= 6) {
      // 끝단: 그리퍼
      return (
        <mesh position={[0, 0, 0]}>
          <boxGeometry args={[0.15, 0.15, 0.15]} />
          <meshStandardMaterial color="#94a3b8" />
        </mesh>
      );
    }
    return (
      <ArmLink
        key={idx}
        jointIdx={idx}
        angle={angles[idx]}
        linkLength={LINK_LENGTHS[idx]}
        color={JOINT_COLORS[idx]}
        selected={selectedJoint === idx}
        onSelect={() => onSelectJoint(selectedJoint === idx ? null : idx)}
      >
        {renderChain(idx + 1)}
      </ArmLink>
    );
  };

  return (
    <>
      {/* 조명 */}
      <ambientLight intensity={0.6} />
      <directionalLight position={[5, 10, 5]} intensity={1} />
      <directionalLight position={[-5, 5, -5]} intensity={0.4} />

      {/* 바닥 그리드 */}
      <gridHelper args={[6, 12, "#e2e8f0", "#e2e8f0"]} position={[0, -0.1, 0]} />

      {/* 베이스 플레이트 */}
      <mesh position={[0, -0.05, 0]}>
        <cylinderGeometry args={[0.3, 0.3, 0.1, 32]} />
        <meshStandardMaterial color="#475569" />
      </mesh>

      {/* 팔 체인 */}
      <group position={[0, 0, 0]}>
        {renderChain(0)}
      </group>

      {/* 드래그 컨트롤러 */}
      <DragController
        selectedJoint={selectedJoint}
        angles={angles}
        onAngleChange={onAngleChange}
      />

      {/* 카메라 조작 (관절 선택 안 했을 때만) */}
      <OrbitControls
        enablePan={false}
        enabled={selectedJoint === null}
        minDistance={2}
        maxDistance={10}
      />

      <GizmoHelper alignment="bottom-right" margin={[60, 60]}>
        <GizmoViewport labelColor="white" axisHeadScale={1} />
      </GizmoHelper>
    </>
  );
}

// ── 공개 컴포넌트 ─────────────────────────────────────────────
export function ArmModel3D({ angles, onAngleChange, selectedJoint, onSelectJoint }: ArmModel3DProps) {
  return (
    <div className="w-full h-full">
      <Canvas
        camera={{ position: [4, 3, 4], fov: 45 }}
        style={{ background: "#0f172a", borderRadius: "0.5rem" }}
      >
        <ArmScene
          angles={angles}
          onAngleChange={onAngleChange}
          selectedJoint={selectedJoint}
          onSelectJoint={onSelectJoint}
        />
      </Canvas>
    </div>
  );
}
