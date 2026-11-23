import { useEffect, useState } from "react";
import { api, type Approval } from "../api";
import { Avatar, Empty, Modal, PeoplePicker, PersonName, StatusBadge } from "../components/common";
import { fileToBase64, fullTime, nameOf, shortTime, useDirectory } from "../util";

type Tab = "todo" | "drafter" | "approver";
const TABS: [Tab, string][] = [
  ["todo", "내 차례"],
  ["drafter", "올린 문서"],
  ["approver", "결재선에 든 문서"],
];
const KINDS = ["출장신청", "출장보고", "구매품의", "보안예외", "월간보고", "기타"];

export function ApprovalPage({ refreshKey, onChanged }: { refreshKey: number; onChanged: () => void }) {
  const dir = useDirectory();
  const [tab, setTab] = useState<Tab>("todo");
  const [list, setList] = useState<Approval[]>([]);
  const [open, setOpen] = useState<Approval | null>(null);
  const [writing, setWriting] = useState(false);

  useEffect(() => {
    api.approvals(tab).then((l) => setList([...l].reverse()));
  }, [tab, refreshKey, dir.me.id]);

  // 목록이 바뀌면 열어 둔 문서도 새 상태로
  useEffect(() => {
    if (open) api.approval(open.id).then(setOpen, () => setOpen(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshKey]);
  useEffect(() => setOpen(null), [tab, dir.me.id]);
  useEffect(() => setTab("todo"), [dir.me.id]); // 사람을 바꾸면 그 사람의 "내 차례"부터

  return (
    <div className="split">
      <section className="list-pane">
        <div className="toolbar">
          <div className="seg">
            {TABS.map(([k, label]) => (
              <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>
                {label}
              </button>
            ))}
          </div>
          <button className="primary" onClick={() => setWriting(true)}>
            기안하기
          </button>
        </div>
        {list.length === 0 ? (
          <Empty>{tab === "todo" ? "지금 결재할 문서가 없습니다" : "문서가 없습니다"}</Empty>
        ) : (
          <ul className="rows">
            {list.map((a) => (
              <li key={a.id}>
                <button className={"row" + (open?.id === a.id ? " sel" : "")} onClick={() => setOpen(a)}>
                  <Avatar id={a.drafter} />
                  <span className="row-main">
                    <span className="row-top">
                      <span className="row-who">
                        <span className="kind">{a.kind}</span> {nameOf(dir, a.drafter)}
                      </span>
                      <time>{shortTime(a.created_at)}</time>
                    </span>
                    <span className="row-title">
                      {a.title} <StatusBadge status={a.status} />
                    </span>
                    <span className="row-sub">
                      {a.line.map((p) => nameOf(dir, p)).join(" → ")}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="detail-pane">
        {open ? (
          <ApprovalDetail
            a={open}
            onDecided={() => {
              onChanged();
            }}
          />
        ) : (
          <Empty>문서를 고르면 결재선과 내용이 보입니다</Empty>
        )}
      </section>

      {writing && (
        <NewApproval
          onClose={() => setWriting(false)}
          onDone={() => {
            setWriting(false);
            setTab("drafter");
            onChanged();
          }}
        />
      )}
    </div>
  );
}

function ApprovalDetail({ a, onDecided }: { a: Approval; onDecided: () => void }) {
  const dir = useDirectory();
  const [comment, setComment] = useState("");
  const [error, setError] = useState("");
  const myTurn = a.status === "pending" && a.line[a.step] === dir.me.id;

  useEffect(() => {
    setComment("");
    setError("");
  }, [a.id]);

  async function decide(decision: "approve" | "reject") {
    if (decision === "reject" && !comment.trim()) return setError("반려할 때는 의견을 쓰세요");
    try {
      await api.decide(a.id, decision, comment);
      onDecided();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <article className="approval">
      <div className="approval-head">
        <span className="kind">{a.kind}</span>
        <StatusBadge status={a.status} />
        <span className="muted">문서 #{a.id}</span>
      </div>
      <h1>{a.title}</h1>

      <ol className="line" aria-label="결재선">
        <li className="done">
          <Avatar id={a.drafter} size={34} />
          <span>{nameOf(dir, a.drafter)}</span>
          <small>기안 · {fullTime(a.created_at).slice(5)}</small>
        </li>
        {a.line.map((p, i) => {
          const h = a.history[i];
          const state = h ? h.decision : a.status === "pending" && i === a.step ? "now" : "wait";
          return (
            <li key={i} className={state}>
              <Avatar id={p} size={34} />
              <span>{nameOf(dir, p)}</span>
              <small>
                {state === "approve" && `승인 · ${fullTime(h!.at).slice(5)}`}
                {state === "reject" && `반려 · ${fullTime(h!.at).slice(5)}`}
                {state === "now" && "결재 차례"}
                {state === "wait" && "대기"}
              </small>
            </li>
          );
        })}
      </ol>

      <div className="meta-grid">
        <span>기안자</span>
        <span>
          <PersonName id={a.drafter} />
        </span>
        {a.attachment_name && (
          <>
            <span>첨부</span>
            <span>
              <button className="link" onClick={() => api.downloadAttachment(a).catch((e) => setError(e.message))}>
                📎 {a.attachment_name}
              </button>
            </span>
          </>
        )}
      </div>
      <div className="mail-body">{a.body || <span className="muted">(본문 없음)</span>}</div>

      {a.history.some((h) => h.comment) && (
        <section className="comments">
          <h3>결재 의견</h3>
          {a.history
            .filter((h) => h.comment)
            .map((h, i) => (
              <p key={i}>
                <b>{nameOf(dir, h.by)}</b> ({h.decision === "approve" ? "승인" : "반려"}) {h.comment}
              </p>
            ))}
        </section>
      )}

      {myTurn && (
        <section className="decide">
          <textarea rows={2} placeholder="의견 (반려할 때는 꼭 쓰세요)" value={comment} onChange={(e) => setComment(e.target.value)} />
          <div className="actions">
            <button className="danger" onClick={() => decide("reject")}>
              반려
            </button>
            <button className="primary" onClick={() => decide("approve")}>
              승인
            </button>
          </div>
        </section>
      )}
      {error && <p className="error">{error}</p>}
    </article>
  );
}

function NewApproval({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const dir = useDirectory();
  const [kind, setKind] = useState(KINDS[0]);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [line, setLine] = useState<string[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!title.trim()) return setError("제목을 쓰세요");
    if (!line.length) return setError("결재선에 한 명 이상 넣으세요");
    try {
      await api.submitApproval({
        kind,
        title,
        body,
        line,
        ...(file ? { attachment_b64: await fileToBase64(file), attachment_name: file.name } : {}),
      });
      onDone();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  return (
    <Modal title="기안하기" onClose={onClose} wide>
      <form className="form" onSubmit={submit}>
        <div className="row2">
          <label>
            종류
            <select value={kind} onChange={(e) => setKind(e.target.value)}>
              {KINDS.map((k) => (
                <option key={k}>{k}</option>
              ))}
            </select>
          </label>
          <label>
            제목
            <input value={title} onChange={(e) => setTitle(e.target.value)} autoFocus />
          </label>
        </div>
        <label>
          결재선 (고른 순서대로 결재)
          <PeoplePicker value={line} onChange={setLine} exclude={[dir.me.id]} ordered placeholder="결재자를 순서대로 고르세요" />
        </label>
        <label>
          본문
          <textarea rows={8} value={body} onChange={(e) => setBody(e.target.value)} />
        </label>
        <label>
          첨부 (선택)
          <input type="file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </label>
        {error && <p className="error">{error}</p>}
        <div className="actions">
          <button type="button" onClick={onClose}>
            취소
          </button>
          <button className="primary">올리기</button>
        </div>
      </form>
    </Modal>
  );
}
