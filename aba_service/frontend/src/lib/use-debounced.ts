import { useEffect, useState } from "react";

/**
 * 값이 `ms` 동안 그대로일 때만 흘려보낸다.
 *
 * 검색창은 글자 하나마다 `/api/books` 를 때리고 있었다. 서제스트가 붙으면 그 부담이
 * 홈 화면까지 번지므로, 호출 자체를 늦춘다.
 */
export function useDebounced<T>(value: T, ms = 250): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return debounced;
}
