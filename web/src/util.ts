import { createContext, useContext } from "react";
import type { User } from "./api";

export type Directory = { me: User; users: User[]; byId: Record<string, User> };
export const DirectoryContext = createContext<Directory | null>(null);

export function useDirectory(): Directory {
  const d = useContext(DirectoryContext);
  if (!d) throw new Error("DirectoryContext 없음");
  return d;
}

export function nameOf(dir: Directory, id: string): string {
  return dir.byId[id]?.name ?? id;
}

const pad = (n: number) => String(n).padStart(2, "0");

/** 'YYYY-MM-DD' (로컬 시각 기준) */
export const ymd = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

/** 목록용 짧은 시각: 오늘이면 HH:MM, 아니면 M/D */
export function shortTime(iso: string): string {
  const d = new Date(iso);
  const today = ymd(new Date());
  return iso.slice(0, 10) === today ? `${pad(d.getHours())}:${pad(d.getMinutes())}` : `${d.getMonth() + 1}/${d.getDate()}`;
}

/** 'YYYY-MM-DD HH:MM' */
export const fullTime = (iso: string) => iso.replace("T", " ").slice(0, 16);

export const hhmm = (iso: string) => iso.slice(11, 16);

export function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",")[1] ?? "");
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}
