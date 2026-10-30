import { requestSamples } from "./data/request-samples.js";

const legacyRecords = [
  { id: "CR-A-001", title: "상품 리뷰 기능 추가 (별점·텍스트·사진)", project: "자사몰 구축 · 모노마켓", requester: "이정민", channel: "이메일", date: "10-06", scope: "범위 밖", process: "PM 검토 필요", approval: "미요청", customer: "미요청", md: "미산정", price: "—", owner: "김", elapsed: "6일", api: true },
  { id: "CR-004", title: "멤버십 등급 기준 변경", project: "멤버십 앱 리뉴얼 · 다라리테일", requester: "최유진", channel: "이메일", date: "10-13", scope: "판단 보류", process: "PM 검토 필요", approval: "미요청", customer: "미요청", md: "미산정", price: "—", owner: "정", elapsed: "2일" },
  { id: "CR-005", title: "주문 엑셀 일괄 업로드", project: "B2B 주문 포털 · 마바유통", requester: "한도윤", channel: "이메일", date: "10-17", scope: "범위 밖", process: "내부 승인 대기", approval: "대기", customer: "미요청", md: "9.0 MD", price: "380만원 (예시)", owner: "김", elapsed: "3일" },
  { id: "CR-001", title: "상품 리뷰 기능 추가", project: "자사몰 구축 · 가나다커머스", requester: "이정민", channel: "이메일", date: "10-14", scope: "범위 밖 3 · 보류 2", process: "고객 협의 중", approval: "승인 v3 (예시)", customer: "응답 대기", md: "6.5 MD", price: "220만원 (예시)", owner: "김", elapsed: "6일" },
  { id: "CR-015", title: "상품 검색 필터 추가", project: "자사몰 구축 · 가나다커머스", requester: "박준호", channel: "회의 메모", date: "10-16", scope: "범위 밖 가능성", process: "PM 검토 필요", approval: "미요청", customer: "미요청", md: "5.0 MD", price: "AI 초안 (예시)", owner: "김", elapsed: "4일" },
  { id: "CR-010", title: "배송지 다중 등록", project: "B2B 주문 포털 · 마바유통", requester: "한도윤", channel: "이메일", date: "10-09", scope: "범위 밖", process: "내부 승인 대기", approval: "재승인 필요", customer: "미요청", md: "6.0 MD", price: "250만원 (예시)", owner: "김", elapsed: "1일" },
  { id: "CR-011", title: "회원가입 약관 문구 수정", project: "멤버십 앱 리뉴얼 · 다라리테일", requester: "최유진", channel: "메신저", date: "10-18", scope: "범위 밖", process: "내부 승인 대기", approval: "대기", customer: "미요청", md: "1.0 MD", price: "전략적 무상 (예시)", owner: "정", elapsed: "2일" },
  { id: "CR-013", title: "탈퇴 사유 수집 화면", project: "멤버십 앱 리뉴얼 · 다라리테일", requester: "최유진", channel: "이메일", date: "10-06", scope: "범위 밖", process: "고객 협의 중", approval: "승인 v1 (예시)", customer: "재협의", md: "10.0 MD", price: "420만원 (예시)", owner: "정", elapsed: "9일" },
  { id: "CR-002", title: "간편결제(네이버페이) 추가", project: "자사몰 구축 · 가나다커머스", requester: "이정민", channel: "회의 메모", date: "10-19", scope: "분석 중", process: "분석 중", approval: "미요청", customer: "미요청", md: "미산정", price: "—", owner: "김", elapsed: "1일" },
  { id: "CR-014", title: "배송 추적 API 연동", project: "B2B 주문 포털 · 마바유통", requester: "한도윤", channel: "전화", date: "10-20", scope: "미분석", process: "접수", approval: "미요청", customer: "미요청", md: "미산정", price: "—", owner: "김", elapsed: "0일" },
  { id: "CR-006", title: "관리자 권한 3단계 세분화", project: "B2B 주문 포털 · 마바유통", requester: "한도윤", channel: "회의 메모", date: "10-02", scope: "범위 밖", process: "실행 중", approval: "승인 v2 (예시)", customer: "증빙 확인", md: "5.0 MD", price: "200만원 (예시)", owner: "정", elapsed: "—" },
  { id: "CR-016", title: "상품 목록 정렬 기준 변경", project: "자사몰 구축 · 모노마켓", requester: "이정민", channel: "이메일", date: "10-21", scope: "판단 보류", process: "PM 검토 필요", approval: "미요청", customer: "미요청", md: "미산정", price: "—", owner: "김", elapsed: "1일" },
];

// Keep the existing demo records; CR-A-001 already represents the first dataset request.
export const records = [
  ...legacyRecords.filter(record => record.id === "CR-A-001").map(record => ({
    ...record, project: requestSamples[0].project_name, requester: requestSamples[0].sender_name,
  })),
  ...requestSamples.filter(item => item.case_id !== "CR-A-001").map(item => ({
    id: item.case_id, title: item.subject, project: item.project_name,
    requester: item.sender_name, requesterRole: item.sender_role, channel: item.channel_src,
    date: item.received_at.slice(5, 10), receivedAt: item.received_at,
    messageText: item.message_text, datasetSample: true,
    scope: "미분석", process: "접수", approval: "미요청", customer: "미요청",
    md: "미산정", price: "—", owner: "—", elapsed: "—", api: true,
  })),
  ...legacyRecords.filter(record => record.id !== "CR-A-001"),
];

export const sampleItems = [
  ["R1", "기능", "별점(1~5점) 리뷰 작성", "범위 밖 가능성", "범위 밖", "강", "3"],
  ["R2", "기능", "텍스트 리뷰 작성·조회", "범위 밖 가능성", "범위 밖", "강", "3"],
  ["R3", "기능", "사진 첨부 리뷰 (복수 장)", "범위 밖 가능성", "범위 밖", "강", "2"],
  ["R4", "제약", "구매 확정 고객만 작성", "판단 보류", "보류", "근거 없음", "0"],
  ["R5", "기한", "오픈일 유지", "판단 보류", "보류", "해석 필요", "2"],
  ["R6", "기능", "작성자 본인 리뷰 수정·삭제", "범위 밖 가능성", "범위 밖", "중", "1"],
];

export function scenarioInput(base, scenario) {
  const input = structuredClone(base);
  if (scenario === "missing-rate") input.rate_card_id = "RC-SYNTH-2026H2-noQA";
  if (scenario === "upstream") input.pm_review.status = "PENDING";
  if (scenario === "no-target") for (const item of input.pm_review.items) item.estimate = false;
  return input;
}
