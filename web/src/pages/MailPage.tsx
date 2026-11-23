import { useEffect, useState } from "react";
import { api, type Mail } from "../api";
import { Avatar, Empty, Modal, PeoplePicker, PersonName } from "../components/common";
import { fullTime, nameOf, shortTime, useDirectory } from "../util";

export function MailPage({ refreshKey, onChanged }: { refreshKey: number; onChanged: () => void }) {
  const dir = useDirectory();
  const [box, setBox] = useState<"inbox" | "sent">("inbox");
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [mails, setMails] = useState<Mail[]>([]);
  const [open, setOpen] = useState<Mail | null>(null);
  const [compose, setCompose] = useState<null | { to: string[]; subject: string; body: string }>(null);

  useEffect(() => {
    api.mails(box, query || undefined).then((m) => setMails([...m].reverse()));
  }, [box, query, refreshKey, dir.me.id]);

  useEffect(() => setOpen(null), [box, dir.me.id]);

  async function openMail(m: Mail) {
    setOpen(m);
    if (box === "inbox" && m.read === false) {
      await api.markRead(m.id);
      setMails((all) => all.map((x) => (x.id === m.id ? { ...x, read: true } : x)));
      onChanged();
    }
  }

  return (
    <div className="split">
      <section className="list-pane">
        <div className="toolbar">
          <div className="seg">
            <button className={box === "inbox" ? "on" : ""} onClick={() => setBox("inbox")}>
              받은 메일
            </button>
            <button className={box === "sent" ? "on" : ""} onClick={() => setBox("sent")}>
              보낸 메일
            </button>
          </div>
          <button className="primary" onClick={() => setCompose({ to: [], subject: "", body: "" })}>
            메일 쓰기
          </button>
        </div>
        <form
          className="search"
          onSubmit={(e) => {
            e.preventDefault();
            setQuery(q.trim());
          }}
        >
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="제목·본문 검색" aria-label="메일 검색" />
        </form>
        {mails.length === 0 ? (
          <Empty>{query ? `"${query}"에 맞는 메일이 없습니다` : "메일이 없습니다"}</Empty>
        ) : (
          <ul className="rows">
            {mails.map((m) => {
              const who = box === "inbox" ? m.sender : m.recipients[0];
              return (
                <li key={m.id}>
                  <button className={"row" + (open?.id === m.id ? " sel" : "") + (m.read === false ? " unread" : "")} onClick={() => openMail(m)}>
                    <Avatar id={who} />
                    <span className="row-main">
                      <span className="row-top">
                        <span className="row-who">
                          {box === "sent" && "받는 사람: "}
                          {nameOf(dir, who)}
                          {box === "sent" && m.recipients.length > 1 && ` 외 ${m.recipients.length - 1}`}
                        </span>
                        <time>{shortTime(m.sent_at)}</time>
                      </span>
                      <span className="row-title">{m.subject}</span>
                      <span className="row-sub">{m.body}</span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section className="detail-pane">
        {open ? (
          <article className="mail">
            <h1>{open.subject}</h1>
            <div className="meta-grid">
              <span>보낸 사람</span>
              <span>
                <PersonName id={open.sender} />
              </span>
              <span>받는 사람</span>
              <span>{open.recipients.map((r) => nameOf(dir, r)).join(", ")}</span>
              {open.cc.length > 0 && (
                <>
                  <span>참조</span>
                  <span>{open.cc.map((r) => nameOf(dir, r)).join(", ")}</span>
                </>
              )}
              <span>보낸 시각</span>
              <span>{fullTime(open.sent_at)}</span>
            </div>
            <div className="mail-body">{open.body}</div>
            <div className="actions">
              <button
                onClick={() =>
                  setCompose({
                    to: [open.sender === dir.me.id ? open.recipients[0] : open.sender],
                    subject: open.subject.startsWith("RE:") ? open.subject : `RE: ${open.subject}`,
                    body: `\n\n----- 원문 (${fullTime(open.sent_at)}, ${nameOf(dir, open.sender)}) -----\n${open.body}`,
                  })
                }
              >
                답장
              </button>
            </div>
          </article>
        ) : (
          <Empty>메일을 고르면 여기에 본문이 보입니다</Empty>
        )}
      </section>

      {compose && (
        <ComposeMail
          initial={compose}
          onClose={() => setCompose(null)}
          onSent={() => {
            setCompose(null);
            setBox("sent");
            onChanged();
          }}
        />
      )}
    </div>
  );
}

function ComposeMail({ initial, onClose, onSent }: { initial: { to: string[]; subject: string; body: string }; onClose: () => void; onSent: () => void }) {
  const dir = useDirectory();
  const [to, setTo] = useState(initial.to);
  const [cc, setCc] = useState<string[]>([]);
  const [subject, setSubject] = useState(initial.subject);
  const [body, setBody] = useState(initial.body);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    if (!to.length) return setError("받는 사람을 한 명 이상 고르세요");
    if (!subject.trim()) return setError("제목을 쓰세요");
    setBusy(true);
    try {
      await api.sendMail({ to, cc, subject, body });
      onSent();
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <Modal title="메일 쓰기" onClose={onClose} wide>
      <form className="form" onSubmit={send}>
        <label>
          받는 사람
          <PeoplePicker value={to} onChange={setTo} exclude={[dir.me.id, ...cc]} />
        </label>
        <label>
          참조
          <PeoplePicker value={cc} onChange={setCc} exclude={[dir.me.id, ...to]} placeholder="(선택)" />
        </label>
        <label>
          제목
          <input value={subject} onChange={(e) => setSubject(e.target.value)} />
        </label>
        <label>
          본문
          <textarea rows={10} value={body} onChange={(e) => setBody(e.target.value)} />
        </label>
        {error && <p className="error">{error}</p>}
        <div className="actions">
          <button type="button" onClick={onClose}>
            취소
          </button>
          <button className="primary" disabled={busy}>
            보내기
          </button>
        </div>
      </form>
    </Modal>
  );
}
