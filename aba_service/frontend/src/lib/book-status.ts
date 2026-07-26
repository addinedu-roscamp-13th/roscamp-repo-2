/**
 * 도서 가용 상태 — 화면 어디서나 같은 기준으로 말하기 위한 단일 판정.
 *
 * DB 에는 두 플래그가 따로 있다.
 * - `in_stock`  : 지금 서가에 있는가 (false = 대출 중)
 * - `unavailable`: 훼손·분실로 사서가 대출 불가 처리했는가 (`in_stock` 과 별개)
 *
 * `unavailable` 이 우선이다 — 서가에 꽂혀 있어도 사서가 막아뒀으면 빌릴 수 없다.
 */
export type BookAvailability = "available" | "borrowed" | "blocked";

export function bookAvailability(book: {
  inStock: boolean;
  unavailable?: boolean;
}): BookAvailability {
  if (book.unavailable) return "blocked";
  return book.inStock ? "available" : "borrowed";
}

export const AVAILABILITY_LABEL: Record<BookAvailability, string> = {
  available: "배치중",
  borrowed: "대출 중",
  blocked: "대출 불가",
};

/** 상세 시트에서 쓰는 한 문장 설명. */
export function availabilitySentence(
  a: BookAvailability,
  zone: string,
  shelf: string,
): string {
  switch (a) {
    case "available":
      return `지금 ${zone} 서가 ${shelf}에 배치돼 있어요.`;
    case "borrowed":
      return "현재 대출 중이에요. 예약해 두면 반납될 때 알려드려요.";
    case "blocked":
      return "훼손·분실로 사서가 대출을 막아둔 도서예요.";
  }
}
