/**
 * WebSocket 카메라 프레임(base64 JPEG)을 Blob URL로 변환하고
 * 이전 프레임 URL을 즉시 해제해 메모리 누수를 방지한다.
 */
import { useEffect, useRef, useState } from "react";

export function useCameraFrame() {
  const [frameUrl, setFrameUrl] = useState<string | null>(null);
  const prevUrlRef = useRef<string | null>(null);

  const pushFrame = (base64: string) => {
    try {
      const binary = atob(base64);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
      const blob = new Blob([bytes], { type: "image/jpeg" });
      const url = URL.createObjectURL(blob);

      // 이전 프레임 메모리 해제
      if (prevUrlRef.current) URL.revokeObjectURL(prevUrlRef.current);
      prevUrlRef.current = url;
      setFrameUrl(url);
    } catch {
      // base64 디코딩 실패 시 무시
    }
  };

  // 언마운트 시 마지막 프레임도 해제
  useEffect(() => {
    return () => {
      if (prevUrlRef.current) URL.revokeObjectURL(prevUrlRef.current);
    };
  }, []);

  return { frameUrl, pushFrame };
}
