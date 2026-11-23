import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, setApiUser, type Notice, type Summary, type User } from "./api";
import { AssistantPanel } from "./assistant/AssistantPanel";
import { Avatar } from "./components/common";
import { ApprovalPage } from "./pages/ApprovalPage";
import { CalendarPage } from "./pages/CalendarPage";
import { MailPage } from "./pages/MailPage";
import { DirectoryContext, nameOf, shortTime, useDirectory, type Directory } from "./util";

type Page = "mail" | "calendar" | "approval";
const PAGES: [Page, string, string][] = [
  ["mail", "메일", "✉"],
  ["calendar", "일정", "▦"],
  ["approval", "전자결재", "✔"],
];

const pageFromHash = (): Page => {
  const h = location.hash.slice(1);
  return h === "calendar" || h === "approval" ? h : "mail";
};

function readStored(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
function writeStored(key: string, v: string) {
  try {
    localStorage.setItem(key, v);
  } catch {
    /* 저장 못 해도 동작에는 문제없다 */
  }
}

export function App() {
  const [users, setUsers] = useState<User[] | null>(null);
  const [meId, setMeId] = useState<string>(() => readStored("gw.user") ?? "jaehwi");
  const [page, setPage] = useState<Page>(pageFromHash);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [panelOpen, setPanelOpen] = useState(() => readStored("gw.panel") !== "closed");
  const [panelWide, setPanelWide] = useState(() => readStored("gw.panelWide") === "1");
  const lastEvent = useRef<number | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.users().then(setUsers, (e) => setError(`그룹웨어 서버에 붙지 못했습니다: ${e.message}`));
    const onHash = () => setPage(pageFromHash());
    addEventListener("hashchange", onHash);
    return () => removeEventListener("hashchange", onHash);
  }, []);

  const dir: Directory | null = useMemo(() => {
    if (!users) return null;
    const byId = Object.fromEntries(users.map((u) => [u.id, u]));
    const me = byId[meId] ?? users[0];
    return { me, users, byId };
  }, [users, meId]);

  setApiUser(dir?.me.id ?? meId);

  const bump = useCallback(() => setRefreshKey((k) => k + 1), []);

  // 배지와 "누가 바꿨나"를 10초마다 확인한다. 새 변경 이벤트가 있으면 화면을 다시 읽는다.
  // (4장에서 이 폴링을 SSE로 바꾼다)
  useEffect(() => {
    if (!dir) return;
    lastEvent.current = null;
    let alive = true;
    const tick = async () => {
      try {
        const s = await api.summary();
        if (!alive) return;
        setSummary(s);
        if (lastEvent.current !== null && s.last_event_id !== lastEvent.current) bump();
        lastEvent.current = s.last_event_id;
      } catch {
        /* 서버가 잠깐 꺼져도 다음 주기에 다시 */
      }
    };
    tick();
    const t = setInterval(tick, 10_000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [dir, refreshKey, bump]);

  if (error) return <div className="boot error">{error}</div>;
  if (!dir) return <div className="boot">불러오는 중…</div>;

  const go = (p: Page) => {
    location.hash = p;
    setPage(p);
  };
  const badge = (p: Page) => (p === "mail" ? summary?.unread_mails : p === "approval" ? summary?.my_turn_approvals : 0) || 0;

  return (
    <DirectoryContext.Provider value={dir}>
      <div className={"shell" + (panelOpen ? " with-panel" : "") + (panelOpen && panelWide ? " panel-wide" : "")}>
        <header className="topbar">
          <div className="brand">
            <span className="logo">놀</span> 놀러가자 그룹웨어
          </div>
          <div className="top-right">
            <Bell refreshKey={refreshKey} onOpen={(n) => go(n.kind === "events" ? "calendar" : n.kind === "mails" ? "mail" : "approval")} />
            <label className="user-switch" title="실습용: 로그인 대신 사람을 고른다">
              <Avatar id={dir.me.id} />
              <select
                value={dir.me.id}
                onChange={(e) => {
                  setMeId(e.target.value);
                  writeStored("gw.user", e.target.value);
                }}
                aria-label="사용자 바꾸기"
              >
                {dir.users.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.name} ({u.team} {u.title})
                  </option>
                ))}
              </select>
            </label>
            <button
              className={"panel-toggle" + (panelOpen ? " on" : "")}
              onClick={() => {
                setPanelOpen(!panelOpen);
                writeStored("gw.panel", panelOpen ? "closed" : "open");
              }}
            >
              비서
            </button>
          </div>
        </header>

        <nav className="sidebar">
          {PAGES.map(([p, label, icon]) => (
            <button key={p} className={page === p ? "on" : ""} onClick={() => go(p)}>
              <span className="nav-icon">{icon}</span>
              {label}
              {badge(p) > 0 && <span className="count">{badge(p)}</span>}
            </button>
          ))}
          <div className="me-card">
            <b>{dir.me.name}</b>
            <span>
              {dir.me.team} · {dir.me.title}
            </span>
          </div>
        </nav>

        <main className="content">
          {page === "mail" && <MailPage refreshKey={refreshKey} onChanged={bump} />}
          {page === "calendar" && <CalendarPage refreshKey={refreshKey} onChanged={bump} />}
          {page === "approval" && <ApprovalPage refreshKey={refreshKey} onChanged={bump} />}
        </main>

        {panelOpen && (
          <AssistantPanel
            wide={panelWide}
            onToggleWide={() => {
              setPanelWide(!panelWide);
              writeStored("gw.panelWide", panelWide ? "0" : "1");
            }}
            onDataChanged={bump}
          />
        )}
      </div>
    </DirectoryContext.Provider>
  );
}

function Bell({ refreshKey, onOpen }: { refreshKey: number; onOpen: (n: Notice) => void }) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<Notice[]>([]);
  useEffect(() => {
    if (open) api.notifications().then(setItems);
  }, [open, refreshKey]);
  return (
    <div className="bell">
      <button className="icon" onClick={() => setOpen(!open)} aria-label="알림">
        🔔
      </button>
      {open && (
        <div className="bell-menu" onMouseLeave={() => setOpen(false)}>
          <h3>최근 변경</h3>
          {items.length === 0 && <p className="muted">알림이 없습니다</p>}
          {items.map((n) => (
            <NoticeRow
              key={n.id}
              n={n}
              onClick={() => {
                setOpen(false);
                onOpen(n);
              }}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function NoticeRow({ n, onClick }: { n: Notice; onClick: () => void }) {
  const dir = useDirectory();
  const by = n.payload.by ? nameOf(dir, n.payload.by) : "";
  let text = "";
  if (n.kind === "mails") text = `${by}님의 메일: ${n.payload.subject ?? ""}`;
  else if (n.kind === "events") text = `${by}님이 일정을 만들었습니다: ${n.payload.title ?? ""}`;
  else {
    const what = n.payload.decision === "approve" ? "승인" : n.payload.decision === "reject" ? "반려" : "기안";
    const done = n.payload.status === "approved" ? " (최종 승인)" : n.payload.status === "rejected" ? " (반려로 끝남)" : "";
    text = `${by}님 ${what}${done}: ${n.payload.title ?? ""}`;
  }
  return (
    <button className="notice" onClick={onClick}>
      <span>{text}</span>
      <time>{shortTime(n.created_at)}</time>
    </button>
  );
}
