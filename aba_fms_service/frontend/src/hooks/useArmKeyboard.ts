/**
 * 키보드로 로봇팔 관절을 조작하는 훅.
 *
 * 키 매핑:
 *   ← → : J1 (베이스 좌/우)
 *   ↑ ↓ : J2 (숄더 앞/뒤)
 *   W / S : J3 (엘보 위/아래)
 *   A / D : J4 (리스트1 좌/우)
 *   Q / E : J5 (리스트2 위/아래)
 *   Z / X : J6 (리스트3 CCW/CW)
 *   O / P : 그리퍼 열기/닫기
 *   H     : 홈 포지션
 */
import { useEffect, useRef } from "react";

const STEP = 3;        // 한 틱당 이동 각도 (도)
const INTERVAL_MS = 120; // 키 누르고 있을 때 반복 주기 (ms)

const LIMITS = [
  [-168, 168], [-135, 135], [-150, 150],
  [-145, 145], [-165, 165], [-180, 180],
];

const clamp = (v: number, idx: number) =>
  Math.max(LIMITS[idx][0], Math.min(LIMITS[idx][1], v));

export function useArmKeyboard(enabled = true) {
  const anglesRef  = useRef<number[]>([0, 0, 0, 0, 0, 0]);
  const heldRef    = useRef<Set<string>>(new Set());
  const timerRef   = useRef<ReturnType<typeof setInterval> | null>(null);
  const sendingRef = useRef(false);

  const syncAngles = (joints: number[]) => {
    // 키 안 누르고 있을 때만 동기화 (누르는 중엔 로컬 값이 기준)
    if (heldRef.current.size === 0) {
      anglesRef.current = joints.slice(0, 6).map((v) => Math.round(v * 10) / 10);
    }
  };

  const sendAngles = async (angles: number[]) => {
    if (sendingRef.current) return;
    sendingRef.current = true;
    try {
      await fetch("/api/arm/angles", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ angles, speed: 20 }),
      });
    } finally {
      sendingRef.current = false;
    }
  };

  const sendStop = () =>
    fetch("/api/arm/jog-stop", { method: "POST" });

  const sendGripper = (value: number) =>
    fetch("/api/arm/gripper", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ value, speed: 30 }),
    });

  // 현재 눌린 키들을 바탕으로 각도 한 틱 계산 후 전송
  const tick = () => {
    const held = heldRef.current;
    if (held.size === 0) return;

    const JOINT_KEYS: Record<string, [number, number]> = {
      "ArrowLeft":  [0, -STEP], "ArrowRight": [0, +STEP],
      "ArrowUp":    [1, +STEP], "ArrowDown":  [1, -STEP],
      "w": [2, +STEP], "s": [2, -STEP],
      "a": [3, -STEP], "d": [3, +STEP],
      "q": [4, +STEP], "e": [4, -STEP],
      "z": [5, -STEP], "x": [5, +STEP],
    };

    let changed = false;
    const next = [...anglesRef.current];
    for (const key of held) {
      const mapping = JOINT_KEYS[key] ?? JOINT_KEYS[key.toLowerCase()];
      if (mapping) {
        const [idx, delta] = mapping;
        next[idx] = clamp(next[idx] + delta, idx);
        changed = true;
      }
    }
    if (changed) {
      anglesRef.current = next;
      sendAngles(next);
    }
  };

  const startLoop = () => {
    if (timerRef.current) return;
    tick(); // 즉시 한 번
    timerRef.current = setInterval(tick, INTERVAL_MS);
  };

  const stopLoop = () => {
    if (timerRef.current) {
      clearInterval(timerRef.current);
      timerRef.current = null;
    }
    if (heldRef.current.size === 0) {
      sendStop();
    }
  };

  useEffect(() => {
    if (!enabled) return;

    const onKeyDown = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;

      const key = e.key;

      // 그리퍼 / 홈 — 단발성
      if ((key === "o" || key === "O") && !heldRef.current.has(key)) {
        sendGripper(100); return;
      }
      if ((key === "p" || key === "P") && !heldRef.current.has(key)) {
        sendGripper(0); return;
      }
      if ((key === "h" || key === "H") && !heldRef.current.has(key)) {
        anglesRef.current = [0, 0, 0, 0, 0, 0];
        sendAngles([0, 0, 0, 0, 0, 0]);
        return;
      }

      const isJointKey = [
        "ArrowLeft","ArrowRight","ArrowUp","ArrowDown",
        "w","s","a","d","q","e","z","x",
        "W","S","A","D","Q","E","Z","X",
      ].includes(key);

      if (!isJointKey) return;
      if (["ArrowLeft","ArrowRight","ArrowUp","ArrowDown"].includes(key)) e.preventDefault();

      const normalized = key.toLowerCase() === key ? key : key.toLowerCase();
      const wasEmpty = heldRef.current.size === 0;
      heldRef.current.add(normalized === key ? key : key); // 원래 key 저장

      // ArrowKey는 대소문자 없으므로 그대로, 나머지는 소문자로 통일
      heldRef.current.add(key);

      if (wasEmpty) startLoop();
    };

    const onKeyUp = (e: KeyboardEvent) => {
      heldRef.current.delete(e.key);
      heldRef.current.delete(e.key.toLowerCase());
      heldRef.current.delete(e.key.toUpperCase());
      if (heldRef.current.size === 0) {
        stopLoop();
      }
    };

    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup",   onKeyUp);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup",   onKeyUp);
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [enabled]);

  return { syncAngles };
}
