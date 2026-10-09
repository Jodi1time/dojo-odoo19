/** Attendance only. Queueing is not an offline authorization grant.
 * No names, contact details, credentials, billing or rank commands are stored.
 * Replay uses the original command; the server revalidates authority.
 */
import type {CommandEnvelope, CheckInPayload} from "@/contracts/kiosk-attendance";
type Command = CommandEnvelope<CheckInPayload>;
type Entry = {id:string; scope:string; fingerprint:string; queuedAt:number; iv:number[]; cipher:ArrayBuffer};
const DB = "dojang-attendance-queue-v1", TTL = 8*60*60*1000, MAX = 32;
let namespace:string|null = null;
let flushing = false;
let keyPromise:Promise<CryptoKey>|null = null;
const notify = () => {if(typeof window !== "undefined") window.dispatchEvent(new Event("dojang-queue-changed"));};
function open():Promise<IDBDatabase> {
  return new Promise((resolve,reject) => {
    const q = indexedDB.open(DB,1);
    q.onupgradeneeded = () => {q.result.createObjectStore("entries",{keyPath:"id"}); q.result.createObjectStore("meta");};
    q.onsuccess = () => resolve(q.result);
    q.onerror = () => reject(q.error);
  });
}
async function operation<T>(store:string,mode:IDBTransactionMode,run:(s:IDBObjectStore)=>IDBRequest<T>):Promise<T> {
  const db = await open();
  return new Promise((resolve,reject) => {
    const tx = db.transaction(store,mode), q = run(tx.objectStore(store));
    let value:T;
    q.onsuccess = () => {value = q.result;};
    tx.oncomplete = () => {db.close(); resolve(value);};
    tx.onabort = tx.onerror = () => {db.close(); reject(tx.error || q.error);};
  });
}
async function createOrReadKey():Promise<CryptoKey> {
  const candidate = await crypto.subtle.generateKey({name:"AES-GCM",length:256},false,["encrypt","decrypt"]);
  const db = await open();
  // One read/write transaction serializes key creation across tabs. A second
  // tab must not overwrite the key used to encrypt already queued requests.
  return new Promise((resolve,reject) => {
    const tx = db.transaction("meta","readwrite"), store = tx.objectStore("meta");
    const q = store.get("encryption");
    let selected:CryptoKey;
    q.onsuccess = () => {selected = q.result || candidate; if(!q.result) store.put(candidate,"encryption");};
    tx.oncomplete = () => {db.close(); resolve(selected);};
    tx.onabort = tx.onerror = () => {db.close(); reject(tx.error || q.error);};
  });
}
function encryptionKey():Promise<CryptoKey> {
  if(!keyPromise) keyPromise = createOrReadKey().catch(error => {keyPromise = null; throw error;});
  return keyPromise;
}
export function queueable(v:unknown):v is Command {
  if(!v || typeof v !== "object" || Array.isArray(v)) return false;
  const c = v as Command;
  const id = (x:unknown) => typeof x === "string" && /^[1-9][0-9]{0,9}$/.test(x) && Number(x) <= 2147483647;
  const key = (x:unknown) => typeof x === "string" && /^[A-Za-z0-9][A-Za-z0-9_.:-]{15,127}$/.test(x);
  return Object.keys(c).every(k => ["idempotencyKey","correlationId","expectedVersion","payload"].includes(k)) && key(c.idempotencyKey) && key(c.correlationId) && !!c.payload && Object.keys(c.payload).length === 2 && id(c.payload.memberId) && id(c.payload.sessionId) && (c.expectedVersion === undefined || (Number.isSafeInteger(c.expectedVersion) && c.expectedVersion >= 0));
}
export async function initializeQueue():Promise<boolean> {
  try {
    const r = await fetch("/api/integration/state",{cache:"no-store"});
    if(!r.ok) {namespace = null; return false;}
    const b = await r.json();
    if(b.mode !== "odoo-test" || b.offlineQueue !== true || typeof b.scope !== "string" || !/^[a-f0-9]{64}$/.test(b.scope)) {namespace = null; return false;}
    namespace = b.scope;
    await encryptionKey(); notify(); return true;
  } catch {return false;}
}
export async function queueState():Promise<{pending:number; review:number}> {
  try {
    const current = namespace;
    if(!current) return {pending:0,review:0};
    const entries = await operation<Entry[]>("entries","readonly",s => s.getAll());
    const review = await operation<number|undefined>("meta","readonly",s => s.get("review:"+current));
    return {pending:entries.filter(e => e.scope === current).length,review:review || 0};
  } catch {return {pending:0,review:0};}
}
async function storeEntry(entry:Entry):Promise<boolean> {
  const db = await open();
  return new Promise((resolve,reject) => {
    const tx = db.transaction("entries","readwrite"), store = tx.objectStore("entries"), q = store.getAll();
    let accepted = false;
    q.onsuccess = () => {
      const entries = q.result as Entry[], saved = entries.find(e => e.id === entry.id);
      if(saved) {accepted = saved.fingerprint === entry.fingerprint; return;}
      if(entries.length >= MAX) return;
      store.put(entry); accepted = true;
    };
    tx.oncomplete = () => {db.close(); resolve(accepted);};
    tx.onabort = tx.onerror = () => {db.close(); reject(tx.error || q.error);};
  });
}
export async function queueAttendance(command:Command):Promise<boolean> {
  const current = namespace;
  if(!current || !queueable(command) || typeof indexedDB === "undefined") return false;
  try {
    const canonical = JSON.stringify({memberId:command.payload.memberId,sessionId:command.payload.sessionId,expectedVersion:command.expectedVersion ?? null});
    const digest = await crypto.subtle.digest("SHA-256",new TextEncoder().encode(canonical));
    const fingerprint = Array.from(new Uint8Array(digest),b => b.toString(16).padStart(2,"0")).join("");
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const cipher = await crypto.subtle.encrypt({name:"AES-GCM",iv},await encryptionKey(),new TextEncoder().encode(JSON.stringify(command)));
    if(namespace !== current) return false;
    const accepted = await storeEntry({id:current+":"+command.idempotencyKey,scope:current,fingerprint,queuedAt:Date.now(),iv:Array.from(iv),cipher});
    notify(); return accepted;
  } catch {return false;}
}
async function review(entry:Entry):Promise<void> {
  const db = await open();
  await new Promise<void>((resolve,reject) => {
    const tx = db.transaction(["entries","meta"],"readwrite"), entries = tx.objectStore("entries"), meta = tx.objectStore("meta");
    const exists = entries.get(entry.id);
    exists.onsuccess = () => {
      if(!exists.result) return;
      entries.delete(entry.id);
      const q = meta.get("review:"+entry.scope);
      q.onsuccess = () => meta.put(Number(q.result || 0)+1,"review:"+entry.scope);
    };
    tx.oncomplete = () => {db.close(); resolve();};
    tx.onabort = tx.onerror = () => {db.close(); reject(tx.error);};
  });
  notify();
}
function confirmed(b:unknown,c:Command):boolean {
  if(!b || typeof b !== "object") return false;
  const x = b as {correlationId?:string; receipt?:{attendanceId?:string; memberId?:string; sessionId?:string; checkedInAt?:string; status?:string}};
  return x.correlationId === c.correlationId && !!x.receipt && typeof x.receipt.attendanceId === "string" && /^[1-9][0-9]*$/.test(x.receipt.attendanceId) && x.receipt.memberId === c.payload.memberId && x.receipt.sessionId === c.payload.sessionId && ["present","late"].includes(x.receipt.status || "") && Number.isFinite(Date.parse(x.receipt.checkedInAt || ""));
}
export async function flushAttendance():Promise<void> {
  if(flushing || typeof navigator === "undefined" || !navigator.onLine) return;
  flushing = true;
  try {
    if(!await initializeQueue() || !namespace) return;
    const current = namespace, entries = await operation<Entry[]>("entries","readonly",s => s.getAll());
    for(const e of entries) {
      if(namespace !== current) break;
      if(e.scope !== current) continue;
      if(Date.now()-e.queuedAt > TTL) {await review(e); continue;}
      let c:Command;
      try {
        const decrypted = await crypto.subtle.decrypt({name:"AES-GCM",iv:new Uint8Array(e.iv)},await encryptionKey(),e.cipher);
        c = JSON.parse(new TextDecoder().decode(decrypted));
        if(!queueable(c)) throw new Error("Invalid queue entry");
      } catch {await review(e); continue;}
      let r:Response;
      try {r = await fetch("/api/v2/attendance/check-ins",{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(c),cache:"no-store",signal:AbortSignal.timeout(15000)});} catch {break;}
      const body = await r.json().catch(() => null);
      if(r.ok && confirmed(body,c)) {await operation("entries","readwrite",s => s.delete(e.id)); notify(); continue;}
      if(r.status === 401 || r.status === 403 || r.status >= 500 || body?.code === "CONCURRENT_RETRY") break;
      if(!r.ok && ["INVALID_COMMAND","SESSION_UNAVAILABLE","SESSION_NOT_OPEN","MEMBER_UNAVAILABLE","NOT_ON_ROSTER","ELIGIBILITY_REVIEW_REQUIRED","ATTENDANCE_REVIEW_REQUIRED","VERSION_CONFLICT","IDEMPOTENCY_CONFLICT"].includes(body?.code)) await review(e);
      else break;
    }
  } finally {flushing = false; notify();}
}
