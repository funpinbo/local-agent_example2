"""보고서 양식의 칸 정의. 칸 목록·검사·저장은 코드가 맡고, 칸에 들어갈 내용만 LLM이 정한다."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    key: str          # docx 양식의 {{ key }}
    label: str        # 사람이 읽는 이름
    guide: str        # LLM에게 주는 작성 기준
    min_len: int = 5  # 너무 짧으면 빈 칸으로 본다


@dataclass(frozen=True)
class TemplateSpec:
    name: str
    title: str
    fields: tuple[Field, ...]

    def keys(self) -> list[str]:
        return [f.key for f in self.fields]

    def field(self, key: str) -> Field | None:
        return next((f for f in self.fields if f.key == key), None)


WEEKLY = TemplateSpec("weekly", "주간 업무 보고", (
    Field("period", "보고 기간", "메모에 적힌 기간을 그대로. 예: 2026-11-09 ~ 2026-11-13", 5),
    Field("done", "이번 주 한 일", "한 일을 항목별로. 메모에 없는 일은 쓰지 않는다", 10),
    Field("issues", "문제와 대응", "생긴 문제와 어떻게 대응했는지. 없으면 '특이 사항 없음'", 5),
    Field("next_plan", "다음 주 계획", "메모에 적힌 다음 주 계획만. 없으면 빈칸으로 두지 말고 '메모에 계획 없음'이라고 쓴다", 5),
    Field("notes", "비고", "요청·공유할 것. 없으면 '없음'", 2),
))

TRIP = TemplateSpec("trip", "출장 결과 보고", (
    Field("destination", "출장지", "기관·장소 이름", 2),
    Field("period", "출장 기간", "날짜 범위", 5),
    Field("purpose", "출장 목적", "한두 문장", 5),
    Field("content", "주요 내용", "회의·견학에서 확인한 것을 항목별로", 10),
    Field("result", "결과", "얻은 것, 정해진 것", 5),
    Field("follow_up", "후속 조치", "메모의 후속 항목을 모두, 항목마다 누가 · 언제까지 · 무엇을. 담당자나 기한이 없으면 그 자리에 '미정'", 5),
))

SPECS: dict[str, TemplateSpec] = {s.name: s for s in (WEEKLY, TRIP)}
