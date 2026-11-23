import { useEffect, useMemo, useState } from "react";
import { api, type CalEvent } from "../api";
import { Modal, PeoplePicker, PersonName } from "../components/common";
import { fullTime, hhmm, nameOf, useDirectory, ymd } from "../util";

const WEEK = ["일", "월", "화", "수", "목", "금", "토"];

export function CalendarPage({ refreshKey, onChanged }: { refreshKey: number; onChanged: () => void }) {
  const dir = useDirectory();
  const [month, setMonth] = useState(() => {
    const d = new Date();
    return new Date(d.getFullYear(), d.getMonth(), 1);
  });
  const [events, setEvents] = useState<CalEvent[]>([]);
  const [open, setOpen] = useState<CalEvent | null>(null);
  const [create, setCreate] = useState<string | null>(null); // 날짜 YYYY-MM-DD

  // 달력 첫 칸(일요일)부터 6주
  const days = useMemo(() => {
    const first = new Date(month);
    first.setDate(1 - first.getDay());
    return Array.from({ length: 42 }, (_, i) => new Date(first.getFullYear(), first.getMonth(), first.getDate() + i));
  }, [month]);

  useEffect(() => {
    const since = ymd(days[0]);
    const until = ymd(new Date(days[41].getFullYear(), days[41].getMonth(), days[41].getDate() + 1));
    api.events(since, until).then(setEvents);
  }, [days, refreshKey, dir.me.id]);

  const byDay = useMemo(() => {
    const m: Record<string, CalEvent[]> = {};
    for (const e of events) {
      // 여러 날 일정은 걸친 날마다 보인다
      const s = new Date(e.start.slice(0, 10));
      const end = new Date(e.end.slice(0, 10));
      for (let d = s; d <= end; d = new Date(d.getFullYear(), d.getMonth(), d.getDate() + 1)) (m[ymd(d)] ??= []).push(e);
    }
    return m;
  }, [events]);

  const today = ymd(new Date());
  const move = (n: number) => setMonth(new Date(month.getFullYear(), month.getMonth() + n, 1));

  return (
    <div className="calendar">
      <div className="toolbar">
        <div className="seg">
          <button onClick={() => move(-1)} aria-label="이전 달">
            ‹
          </button>
          <button
            onClick={() => {
              const d = new Date();
              setMonth(new Date(d.getFullYear(), d.getMonth(), 1));
            }}
          >
            오늘
          </button>
          <button onClick={() => move(1)} aria-label="다음 달">
            ›
          </button>
        </div>
        <h1>
          {month.getFullYear()}년 {month.getMonth() + 1}월
        </h1>
        <button className="primary" onClick={() => setCreate(today)}>
          일정 만들기
        </button>
      </div>

      <div className="cal-grid" role="grid">
        {WEEK.map((w, i) => (
          <div key={w} className={"cal-head" + (i === 0 ? " sun" : i === 6 ? " sat" : "")}>
            {w}
          </div>
        ))}
        {days.map((d) => {
          const key = ymd(d);
          const list = byDay[key] ?? [];
          const other = d.getMonth() !== month.getMonth();
          return (
            <div
              key={key}
              className={"cal-day" + (other ? " other" : "") + (key === today ? " today" : "")}
              onDoubleClick={() => setCreate(key)}
            >
              <span className="cal-date">{d.getDate()}</span>
              {list.slice(0, 3).map((e) => (
                <button key={e.id} className={"cal-ev" + (e.organizer === dir.me.id ? " mine" : "")} onClick={() => setOpen(e)} title={e.title}>
                  {e.start.slice(0, 10) === key && <b>{hhmm(e.start)}</b>} {e.title}
                </button>
              ))}
              {list.length > 3 && <span className="cal-more">+{list.length - 3}개</span>}
            </div>
          );
        })}
      </div>
      <p className="hint">날짜 칸을 두 번 누르면 그날 일정을 만듭니다. 내가 만든 일정은 진한 색입니다.</p>

      {open && (
        <Modal title={open.title} onClose={() => setOpen(null)}>
          <div className="meta-grid">
            <span>시간</span>
            <span>
              {fullTime(open.start)} ~ {open.end.slice(0, 10) === open.start.slice(0, 10) ? hhmm(open.end) : fullTime(open.end)}
            </span>
            <span>장소</span>
            <span>{open.location || "-"}</span>
            <span>주최</span>
            <span>
              <PersonName id={open.organizer} />
            </span>
            <span>참석</span>
            <span>{open.attendees.length ? open.attendees.map((a) => nameOf(dir, a)).join(", ") : "-"}</span>
            {open.memo && (
              <>
                <span>메모</span>
                <span>{open.memo}</span>
              </>
            )}
          </div>
        </Modal>
      )}
      {create && (
        <CreateEvent
          date={create}
          onClose={() => setCreate(null)}
          onDone={() => {
            setCreate(null);
            onChanged();
          }}
        />
      )}
    </div>
  );
}

function CreateEvent({ date, onClose, onDone }: { date: string; onClose: () => void; onDone: () => void }) {
  const dir = useDirectory();
  const [title, setTitle] = useState("");
  const [day, setDay] = useState(date);
  const [start, setStart] = useState("10:00");
  const [end, setEnd] = useState("11:00");
  const [location, setLocation] = useState("");
  const [attendees, setAttendees] = useState<string[]>([]);
  const [memo, setMemo] = useState("");
  const [error, setError] = useState("");

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim()) return setError("제목을 쓰세요");
    if (end <= start) return setError("끝나는 시각이 시작보다 늦어야 합니다");
    try {
      await api.addEvent({ title, start: `${day}T${start}:00`, end: `${day}T${end}:00`, location, attendees, memo });
      onDone();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <Modal title="일정 만들기" onClose={onClose}>
      <form className="form" onSubmit={save}>
        <label>
          제목
          <input value={title} onChange={(e) => setTitle(e.target.value)} autoFocus />
        </label>
        <div className="row3">
          <label>
            날짜
            <input type="date" value={day} onChange={(e) => setDay(e.target.value)} />
          </label>
          <label>
            시작
            <input type="time" value={start} onChange={(e) => setStart(e.target.value)} />
          </label>
          <label>
            끝
            <input type="time" value={end} onChange={(e) => setEnd(e.target.value)} />
          </label>
        </div>
        <label>
          장소
          <input value={location} onChange={(e) => setLocation(e.target.value)} />
        </label>
        <label>
          참석자
          <PeoplePicker value={attendees} onChange={setAttendees} exclude={[dir.me.id]} />
        </label>
        <label>
          메모
          <textarea rows={3} value={memo} onChange={(e) => setMemo(e.target.value)} />
        </label>
        {error && <p className="error">{error}</p>}
        <div className="actions">
          <button type="button" onClick={onClose}>
            취소
          </button>
          <button className="primary">저장</button>
        </div>
      </form>
    </Modal>
  );
}
