import { useState, type ReactNode } from "react";
import type { User } from "../api";
import { useDirectory } from "../util";

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  return (
    <div className="modal-back" onMouseDown={onClose}>
      <div className={"modal" + (wide ? " wide" : "")} onMouseDown={(e) => e.stopPropagation()} role="dialog" aria-label={title}>
        <header>
          <h2>{title}</h2>
          <button className="icon" onClick={onClose} aria-label="닫기">
            ✕
          </button>
        </header>
        {children}
      </div>
    </div>
  );
}

export function Avatar({ id, size = 28 }: { id: string; size?: number }) {
  const dir = useDirectory();
  const name = dir.byId[id]?.name ?? id;
  // 사람마다 고정된 색
  const hue = [...id].reduce((h, c) => (h * 31 + c.charCodeAt(0)) % 360, 7);
  return (
    <span className="avatar" style={{ width: size, height: size, fontSize: size * 0.42, background: `hsl(${hue} 55% 46%)` }} title={name}>
      {name.slice(-2)}
    </span>
  );
}

export function PersonName({ id }: { id: string }) {
  const dir = useDirectory();
  const u = dir.byId[id];
  return (
    <span className="person" title={u ? `${u.team} ${u.title}` : id}>
      {u?.name ?? id}
      {u && <small> {u.title}</small>}
    </span>
  );
}

/** 사람 여러 명 고르기. ordered면 고른 순서가 의미 있다(결재선). */
export function PeoplePicker({ value, onChange, exclude = [], ordered, placeholder }: {
  value: string[];
  onChange: (v: string[]) => void;
  exclude?: string[];
  ordered?: boolean;
  placeholder?: string;
}) {
  const dir = useDirectory();
  const [q, setQ] = useState("");
  const options = dir.users.filter(
    (u: User) => !value.includes(u.id) && !exclude.includes(u.id) && (q === "" || u.name.includes(q) || u.team.includes(q) || u.id.includes(q)),
  );
  return (
    <div className="picker">
      <div className="chips">
        {value.map((id, i) => (
          <span className="chip" key={id}>
            {ordered && <b>{i + 1}</b>}
            {dir.byId[id]?.name ?? id}
            <button type="button" onClick={() => onChange(value.filter((v) => v !== id))} aria-label="빼기">
              ✕
            </button>
          </span>
        ))}
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={value.length ? "" : placeholder ?? "이름·팀으로 찾기"} />
      </div>
      {q !== "" && options.length > 0 && (
        <ul className="picker-options">
          {options.slice(0, 6).map((u) => (
            <li key={u.id}>
              <button
                type="button"
                onClick={() => {
                  onChange([...value, u.id]);
                  setQ("");
                }}
              >
                {u.name} <small>{u.team} · {u.title}</small>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function StatusBadge({ status }: { status: "pending" | "approved" | "rejected" }) {
  const label = { pending: "진행 중", approved: "승인", rejected: "반려" }[status];
  return <span className={`badge ${status}`}>{label}</span>;
}
