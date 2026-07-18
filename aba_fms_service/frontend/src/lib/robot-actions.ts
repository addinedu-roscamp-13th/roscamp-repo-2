import { getToken } from "./admin-api";

// 구역/마커 도착 액션 카탈로그. 백엔드 app/routers/marker_actions.py 의 VALID_ACTIONS 와
// 반드시 일치해야 한다.
export type RobotActionType =
  | "none"
  | "dock"
  | "rotate"
  | "move"
  | "lcd_emotion"
  | "lcd_text"
  | "lcd_image";

export const ACTION_LABELS: Record<RobotActionType, string> = {
  none: "없음",
  dock: "도킹(접근)",
  rotate: "회전",
  move: "이동",
  lcd_emotion: "LCD 표정",
  lcd_text: "LCD 텍스트",
  lcd_image: "LCD 이미지",
};

// app/routers/robot.py 의 VALID_EMOTIONS 와 일치.
export const ROBOT_EMOTIONS = [
  "basic",
  "happy",
  "sad",
  "angry",
  "bored",
  "hello",
  "interest",
  "fun",
] as const;

// 기본 번들 폰트(관리자가 업로드한 폰트도 파일명으로 선택 가능).
export const LCD_FONTS = ["MaruBuri-Bold.ttf", "MaruBuri-SemiBold.ttf"] as const;

export interface LocationActionParams {
  [key: string]: unknown;
}

export interface RunnableAction {
  action_type: RobotActionType;
  params: LocationActionParams;
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function toNumber(value: string | undefined, fallback: number): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

// 폼(문자열 입력값) → 액션 타입별 저장/실행용 params 로 변환.
export function buildParams(
  type: RobotActionType,
  p: Record<string, string>,
): LocationActionParams {
  switch (type) {
    case "rotate":
      return {
        angle: toNumber(p.angle, 90),
        speed: clamp(toNumber(p.speed, 0.3), 0, 1),
      };
    case "move":
      return {
        direction: p.direction || "forward",
        distance: clamp(toNumber(p.distance, 0.3), 0, 5),
      };
    case "lcd_emotion":
      return { emotion: p.emotion || "happy" };
    case "lcd_text":
      return {
        text: p.text || "",
        font_name: p.font_name || "MaruBuri-Bold.ttf",
        font_size: Math.round(clamp(toNumber(p.font_size, 24), 8, 96)),
        color: p.color || "#ffffff",
        bg_color: p.bg_color || "#000000",
        align: p.align || "center",
        duration: Math.round(clamp(toNumber(p.duration, 0), 0, 3600)),
      };
    case "lcd_image":
      return { filename: p.filename || "" };
    case "dock":
      return { target_size: clamp(toNumber(p.target_size, 0.4), 0.05, 0.95) };
    case "none":
    default:
      return {};
  }
}

async function postRobot(
  robotBase: string,
  path: string,
  body: Record<string, unknown>,
): Promise<void> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${robotBase.replace(/\/$/, "")}${path}`, {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = `요청에 실패했습니다 (${res.status})`;
    try {
      const data = await res.json();
      if (data?.detail) detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch {
      /* non-JSON error body */
    }
    throw new Error(detail);
  }
}

// "회전"/"이동" 고레벨 액션 → /api/robot/motor/move (left/right: -100~100, duration: 0.05~3s).
// 예전 프로덕션 빌드(2026-07-14 dist.bak 번들 확인 결과)는 로봇에 /api/robot/rotate,
// /api/robot/move 를 직접 호출해 각도/속도를 그대로 넘겼었지만, 그 두 엔드포인트는 현재
// robot.py 에서 삭제되고 /api/robot/motor/move(left/right/duration) 만 남아 있다. 그래서
// 여기서는 app/robot_dispatch.py 의 relative_move / relative_turn 공식(속도 기본값 35,
// cm/12s, deg/90*1.1s)을 프론트에서 재현해 저레벨 모터 호출로 변환한다.
function rotateToMotor(params: LocationActionParams): { left: number; right: number; duration: number } {
  const angle = Number(params.angle ?? 90);
  const speedPct = Math.round(clamp(Number(params.speed ?? 0.3), 0, 1) * 100);
  const deg = clamp(Math.abs(angle), 5, 180);
  const duration = clamp((deg / 90) * 1.1, 0.05, 3);
  // 각도 부호: +좌(반시계) / -우(시계)
  return angle >= 0
    ? { left: -speedPct, right: speedPct, duration }
    : { left: speedPct, right: -speedPct, duration };
}

function moveToMotor(params: LocationActionParams): { left: number; right: number; duration: number } {
  const distanceM = Number(params.distance ?? 0.3);
  const direction = String(params.direction ?? "forward");
  const speed = 35; // robot_dispatch.py 의 relative_move 기본 속도와 동일
  const distanceCm = clamp(Math.abs(distanceM) * 100, 1, 40);
  const duration = clamp(distanceCm / 12, 0.05, 3);
  if (direction === "backward") return { left: -speed, right: -speed, duration };
  if (direction === "left") return { left: -speed, right: speed, duration };
  if (direction === "right") return { left: speed, right: -speed, duration };
  return { left: speed, right: speed, duration };
}

// 구역/마커 도착 시 실제로 로봇(robotBase)에 액션을 실행시킨다.
export async function runAction(robotBase: string, action: RunnableAction): Promise<void> {
  const { action_type, params } = action;
  switch (action_type) {
    case "none":
      return;
    case "dock":
      return postRobot(robotBase, "/api/robot/dock/start", { target_size: params.target_size ?? 0.4 });
    case "rotate":
      return postRobot(robotBase, "/api/robot/motor/move", rotateToMotor(params));
    case "move":
      return postRobot(robotBase, "/api/robot/motor/move", moveToMotor(params));
    case "lcd_emotion":
      return postRobot(robotBase, "/api/robot/lcd/emotion", { emotion: params.emotion ?? "happy" });
    case "lcd_image":
      return postRobot(robotBase, "/api/robot/lcd/image/select", { filename: params.filename ?? "" });
    case "lcd_text": {
      await postRobot(robotBase, "/api/robot/lcd/text", {
        text: params.text ?? "",
        font_name: params.font_name ?? "MaruBuri-Bold.ttf",
        font_size: params.font_size ?? 24,
        color: params.color ?? "#ffffff",
        bg_color: params.bg_color ?? "#000000",
        align: params.align ?? "center",
        scroll: false,
        scroll_speed: 3,
      });
      // duration>0 이면 그 시간(초) 뒤 자동으로 LCD를 지운다 (원본 동작).
      const duration = Number(params.duration ?? 0);
      if (duration > 0) {
        setTimeout(() => {
          void postRobot(robotBase, "/api/robot/lcd/stop", {}).catch(() => {});
        }, duration * 1000);
      }
      return;
    }
    default:
      throw new Error(`지원하지 않는 action_type: ${action_type}`);
  }
}
