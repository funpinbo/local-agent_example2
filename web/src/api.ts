// 그룹웨어 REST 호출. 실습용이라 로그인 대신 X-User 헤더로 사용자를 고른다.

export type User = { id: string; name: string; team: string; title: string };

export type Mail = {
  id: number;
  sender: string;
  recipients: string[];
  cc: string[];
  subject: string;
  body: string;
  sent_at: string;
  read: boolean | null;
};

export type CalEvent = {
  id: number;
  organizer: string;
  title: string;
  start: string;
  end: string;
  location: string;
  memo: string;
  attendees: string[];
};

export type Decision = { by: string; decision: "approve" | "reject"; comment: string; at: string };

export type Approval = {
  id: number;
  drafter: string;
  kind: string;
  title: string;
  body: string;
  status: "pending" | "approved" | "rejected";
  step: number;
  line: string[];
  history: Decision[];
  created_at: string;
  decided_at: string | null;
  attachment_name: string | null;
};

export type Summary = { unread_mails: number; my_turn_approvals: number; last_event_id: number | null };

export type Notice = {
  id: number;
  kind: "mails" | "events" | "approvals";
  ref_id: number;
  payload: Record<string, string>;
  created_at: string;
};

let currentUser = "";
export const setApiUser = (id: string) => {
  currentUser = id;
};

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function call<T>(method: string, path: string, body?: unknown, params?: Record<string, string | undefined>): Promise<T> {
  const qs = params
    ? "?" + new URLSearchParams(Object.entries(params).filter(([, v]) => v !== undefined && v !== "") as [string, string][])
    : "";
  const res = await fetch(`/api${path}${qs}`, {
    method,
    headers: { "X-User": currentUser, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      msg = (await res.json()).detail ?? msg;
    } catch {
      /* 본문 없음 */
    }
    throw new ApiError(res.status, typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return res.json() as Promise<T>;
}

export const api = {
  users: () => call<User[]>("GET", "/users"),
  summary: () => call<Summary>("GET", "/summary"),
  notifications: () => call<Notice[]>("GET", "/notifications"),

  mails: (box: "inbox" | "sent", q?: string) => call<Mail[]>("GET", "/mails", undefined, { box, q }),
  mail: (id: number) => call<Mail>("GET", `/mails/${id}`),
  markRead: (id: number) => call<{ ok: boolean }>("POST", `/mails/${id}/read`),
  sendMail: (m: { to: string[]; cc: string[]; subject: string; body: string }) => call<{ id: number }>("POST", "/mails", m),

  events: (since: string, until: string) => call<CalEvent[]>("GET", "/events", undefined, { since, until }),
  addEvent: (e: Omit<CalEvent, "id" | "organizer">) => call<{ id: number }>("POST", "/events", e),

  approvals: (role: "drafter" | "approver" | "todo", status?: string) =>
    call<Approval[]>("GET", "/approvals", undefined, { role, status }),
  approval: (id: number) => call<Approval>("GET", `/approvals/${id}`),
  submitApproval: (a: { kind: string; title: string; body: string; line: string[]; attachment_b64?: string; attachment_name?: string }) =>
    call<{ id: number }>("POST", "/approvals", a),
  decide: (id: number, decision: "approve" | "reject", comment: string) =>
    call<{ status: string }>("POST", `/approvals/${id}/decide`, { decision, comment }),

  // 첨부는 X-User 헤더가 필요해서 링크 대신 fetch로 받아 저장한다
  async downloadAttachment(a: Approval) {
    const res = await fetch(`/api/approvals/${a.id}/attachment`, { headers: { "X-User": currentUser } });
    if (!res.ok) throw new ApiError(res.status, "첨부를 받지 못했습니다");
    const url = URL.createObjectURL(await res.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = a.attachment_name ?? "attachment";
    link.click();
    URL.revokeObjectURL(url);
  },
};
