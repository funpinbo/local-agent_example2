---
name: monthly-report
description: 한 달 동안의 메일·일정·결재를 모아 월간 업무 보고 초안을 쓴다
allowed-tools: [find_mails, find_events, find_approvals, read_approval]
---
# 월간 업무 보고 초안

대상 달(YYYY-MM)을 질문에서 정한다. 없으면 지난달이다. 그달 1일 ~ 말일을 since/until로 쓴다.

1. `find_approvals`로 그달 결재를 모은다(`since`, `until`). 반려가 있으면 `last_decision.comment`가 사유다.
2. `find_events`로 그달 일정을 모은다.
3. `find_mails`로 그달 메일을 모은다. `limit`은 30.
4. 아래 네 섹션으로 쓴다. 섹션마다 2~4문장, **문장마다** 끝에 근거 ref를 단다(예: `[mail:12]`, `[event:3]`, `[approval:4]`).
   - 주요 업무
   - 장애·이슈
   - 결재 (승인/반려와 사유)
   - 다음 달 계획 (메일·일정에 나온 것만)
5. 도구 결과에 없는 내용은 쓰지 않는다. 근거가 없는 섹션은 "해당 없음"이라고 쓴다.
