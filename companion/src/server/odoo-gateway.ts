/** Server-only, scoped synthetic Odoo gateway. Browser values never select a tenant or backend. */
import { createHash, createHmac, timingSafeEqual, randomUUID } from "node:crypto";
import type { CheckInResult, ProblemCode, ProblemDetails } from "../contracts/kiosk-attendance";
export type Role = "kiosk" | "staff";
export interface GatewayConfig { origin:string; backend:string; token:string; cookieSecret:string; kioskKey:string; staffKey:string; kioskPairKey:string; staffPairKey:string }
export type Fetcher = typeof fetch;
const safeHeaders = { "cache-control":"private, no-store", "vary":"Cookie", "x-dojang-data-source":"odoo-test" };
const recordId = (v:unknown):v is string => typeof v === "string" && /^[1-9][0-9]{0,9}$/.test(v) && Number(v)<=2147483647;
const key = (v:unknown):v is string => typeof v === "string" && /^[A-Za-z0-9][A-Za-z0-9_.:-]{15,127}$/.test(v);
const hash = (v:string) => createHash("sha256").update(v).digest();
const equal = (a:string,b:string) => timingSafeEqual(hash(a),hash(b));
const object = (v:unknown):v is Record<string,unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const codes:ProblemCode[]=["INVALID_COMMAND","FORBIDDEN","SESSION_UNAVAILABLE","SESSION_NOT_OPEN","MEMBER_UNAVAILABLE","NOT_ON_ROSTER","ELIGIBILITY_REVIEW_REQUIRED","ATTENDANCE_REVIEW_REQUIRED","VERSION_CONFLICT","IDEMPOTENCY_CONFLICT","CONCURRENT_RETRY","CAPABILITY_DISABLED"];
const messages:Record<ProblemCode,string>={INVALID_COMMAND:"Invalid request",FORBIDDEN:"This device is not authorized",SESSION_UNAVAILABLE:"Session unavailable",SESSION_NOT_OPEN:"Session not open for check-in",MEMBER_UNAVAILABLE:"Member unavailable",NOT_ON_ROSTER:"Please see the front desk",ELIGIBILITY_REVIEW_REQUIRED:"Please see the front desk",ATTENDANCE_REVIEW_REQUIRED:"Please see the front desk",VERSION_CONFLICT:"The session changed. Please select it again",IDEMPOTENCY_CONFLICT:"The request does not match its original attempt",CONCURRENT_RETRY:"Please retry the same check-in",CAPABILITY_DISABLED:"The connection is unavailable"};
const cookieName=(role:Role)=>`dojang_${role}_test`;
const scope=(c:GatewayConfig,role:Role)=>hash(c.token+":"+(role==="staff"?c.staffKey:c.kioskKey)).toString("hex");
const sign=(data:string,secret:string)=>createHmac("sha256",secret).update(data).digest("base64url");
export function configFrom(env:NodeJS.ProcessEnv):GatewayConfig {
  if(env.DOJANG_INTEGRATION_MODE!=="odoo-test"||env.NEXT_PUBLIC_DEMO_MODE==="true") throw new Error("Integration disabled");
  const origin=new URL(env.DOJANG_PUBLIC_ORIGIN||""), backend=new URL(env.DOJANG_ODOO_URL||"");
  for(const u of [origin,backend]) { const local=["localhost","127.0.0.1","[::1]"].includes(u.hostname); if((u.protocol!=="https:"&&!(u.protocol==="http:"&&local))||u.username||u.password||u.search||u.hash||u.pathname!=="/") throw new Error("Invalid origin"); }
  const required=(name:string,min=32)=>{const v=env[name];if(!v||v.length<min||v.length>256||/[\r\n]/.test(v)) throw new Error("Missing configuration");return v;};
  const c={origin:origin.origin,backend:backend.origin,token:required("DOJANG_KIOSK_TOKEN",20),cookieSecret:required("DOJANG_COOKIE_SECRET"),kioskKey:required("DOJANG_KIOSK_GATEWAY_KEY"),staffKey:required("DOJANG_STAFF_GATEWAY_KEY"),kioskPairKey:required("DOJANG_KIOSK_PAIR_KEY"),staffPairKey:required("DOJANG_STAFF_PAIR_KEY")};
  if(equal(c.kioskKey,c.staffKey)||equal(c.kioskPairKey,c.staffPairKey))throw new Error("Separate credentials required");return c;
}
export function mintCookie(c:GatewayConfig,role:Role,now=Date.now()):string {const payload=Buffer.from(JSON.stringify({role,scope:scope(c,role),expiresAt:now+7200000})).toString("base64url");return `${payload}.${sign(payload,c.cookieSecret)}`;}
export function authorized(c:GatewayConfig,role:Role,cookieHeader:string,now=Date.now()):boolean {
  const matches=cookieHeader.split(";").map(v=>v.trim()).filter(v=>v.startsWith(cookieName(role)+"="));if(matches.length!==1)return false;
  const v=matches[0].slice(cookieName(role).length+1);if(v.length>2048)return false;
  const [p,s,extra]=v.split(".");if(!p||!s||extra||!equal(s,sign(p,c.cookieSecret)))return false;
  try {const x=JSON.parse(Buffer.from(p,"base64url").toString());return x.role===role&&x.scope===scope(c,role)&&Number.isSafeInteger(x.expiresAt)&&x.expiresAt>now&&x.expiresAt<=now+7200000;}catch{return false;}
}
export function fail(code:ProblemCode,correlationId=`gateway-${randomUUID()}`,status=503):Response {
  const title=messages[code];const value:ProblemDetails={type:`urn:dojang:problem:${code}`,code,status,title,detail:title,correlationId};return Response.json(value,{status,headers:{...safeHeaders,"content-type":"application/problem+json"}});
}
const uncertain=(op:string,c:string)=>["checkin","companion"].includes(op)?new Response(null,{status:503,headers:{...safeHeaders,"retry-after":"1"}}):fail("CAPABILITY_DISABLED",c);
async function readBody(r:Request):Promise<unknown> {const reader=r.body?.getReader();if(!reader)return null;let size=0;const chunks:Uint8Array[]=[];while(true){const {done,value}=await reader.read();if(done)break;size+=value.byteLength;if(size>8192){await reader.cancel();throw new Error("Body limit");}chunks.push(value);}const raw=Buffer.concat(chunks).toString();return r.headers.get("content-type")?.includes("application/x-www-form-urlencoded")?Object.fromEntries(new URLSearchParams(raw)):JSON.parse(raw);}
export async function pair(r:Request,c:GatewayConfig):Promise<Response> {
  if(r.headers.get("origin")!==c.origin)return fail("FORBIDDEN",undefined,403);
  let b;try{b=await readBody(r);}catch{return fail("INVALID_COMMAND",undefined,400);}
  if(!object(b)||!["kiosk","staff"].includes(String(b.role))||typeof b.pairKey!=="string")return fail("FORBIDDEN",undefined,403);
  const role=b.role as Role;if(!equal(b.pairKey,role==="staff"?c.staffPairKey:c.kioskPairKey))return fail("FORBIDDEN",undefined,403);
  return new Response(null,{status:303,headers:{...safeHeaders,location:role==="staff"?"/integration/members":"/kiosk","set-cookie":`${cookieName(role)}=${mintCookie(c,role)}; Path=/; HttpOnly; SameSite=Strict; Max-Age=7200${c.origin.startsWith("https:")?"; Secure":""}`}});
}
export function deviceState(r:Request,c:GatewayConfig):Response {
  if(!authorized(c,"kiosk",r.headers.get("cookie")||""))return fail("FORBIDDEN",undefined,403);
  return Response.json({scope:scope(c,"kiosk"),mode:"odoo-test",offlineQueue:true},{headers:safeHeaders});
}
const paths={sessions:"/kiosk/v2/sessions",roster:"/kiosk/v2/session/roster",checkin:"/kiosk/v2/attendance/check-ins",member:"/kiosk/v2/staff/member",members:"/kiosk/v2/staff/members",companion:"/kiosk/v2/staff/companion"} as const;
export type Operation=keyof typeof paths;
export function validCommand(v:unknown):v is Record<string,unknown> {if(!object(v)||Object.keys(v).some(k=>!["payload","idempotencyKey","correlationId","expectedVersion"].includes(k)))return false;const p=v.payload;return key(v.idempotencyKey)&&key(v.correlationId)&&object(p)&&Object.keys(p).length===2&&recordId(p.sessionId)&&recordId(p.memberId)&&(v.expectedVersion===undefined||(Number.isSafeInteger(v.expectedVersion)&&Number(v.expectedVersion)>=0));}
function validResult(v:unknown,command:Record<string,unknown>):v is CheckInResult {if(!object(v)||!object(v.receipt)||!object(command.payload))return false;const r=v.receipt;return v.correlationId===command.correlationId&&typeof v.replayed==="boolean"&&recordId(r.attendanceId)&&r.memberId===command.payload.memberId&&r.sessionId===command.payload.sessionId&&typeof r.checkedInAt==="string"&&/^\d{4}-\d\d-\d\dT.*Z$/.test(r.checkedInAt)&&Number.isFinite(Date.parse(r.checkedInAt))&&["present","late"].includes(String(r.status))&&typeof r.alreadyRecorded==="boolean"&&key(r.correlationId)&&(v.replayed===true||r.correlationId===command.correlationId);}
function publicPayload(op:Operation,v:Record<string,unknown>,id?:string):unknown {
  const text=(x:unknown):x is string=>typeof x==="string";
  const integer=(x:unknown)=>Number.isSafeInteger(x)&&Number(x)>=0;
  const time=(x:unknown):x is string=>text(x)&&/^\d{4}-\d\d-\d\dT.*Z$/.test(x)&&Number.isFinite(Date.parse(x));
  const invalid=():never=>{throw new Error("Invalid backend contract");};
  if(op==="checkin"){const r=v.receipt as Record<string,unknown>;return {receipt:{attendanceId:r.attendanceId,memberId:r.memberId,sessionId:r.sessionId,checkedInAt:r.checkedInAt,status:r.status,alreadyRecorded:r.alreadyRecorded,correlationId:r.correlationId},replayed:v.replayed,correlationId:v.correlationId};}
  if(op==="sessions"){if(!Array.isArray(v.sessions))return invalid();return v.sessions.map((s:unknown)=>{if(!object(s)||!recordId(s.sessionId)||!text(s.title)||!time(s.startsAt)||!time(s.endsAt)||!integer(s.version)||!integer(s.capacity)||!integer(s.seatsTaken))return invalid();return {sessionId:s.sessionId,title:s.title,startsAt:s.startsAt,endsAt:s.endsAt,version:s.version,capacity:s.capacity,seatsTaken:s.seatsTaken};});}
  if(op==="roster"){if(!Array.isArray(v.roster))return invalid();return v.roster.map((m:unknown)=>{if(!object(m)||!recordId(m.memberId)||!text(m.displayName)||m.enrollmentStatus!=="registered"||!["pending","present","absent","excused"].includes(String(m.attendanceState)))return invalid();return {memberId:m.memberId,displayName:m.displayName,enrollmentStatus:m.enrollmentStatus,attendanceState:m.attendanceState};});}
  if(op==="members"){if(!Array.isArray(v.members))return invalid();return v.members.map((m:unknown)=>{if(!object(m)||!recordId(m.id)||!text(m.name))return invalid();return {id:m.id,name:m.name};});}
  if(op==="companion") {
    if(v.mode!=="guided"||v.source!=="odoo-test"||!recordId(v.memberId))return invalid();
    if(v.capability==="task.create"){if(!recordId(v.taskId)||v.status!=="created"||v.messageSent!==false||typeof v.replayed!=="boolean"||!key(v.correlationId))return invalid();return {mode:v.mode,source:v.source,capability:v.capability,memberId:v.memberId,taskId:v.taskId,status:v.status,messageSent:false,replayed:v.replayed,correlationId:v.correlationId};}
    if(v.capability!=="member.attendance.read"||!object(v.attendance)||!integer(v.attendance.lastSevenDays)||!text(v.memberName))return invalid();
    const l=v.attendance.latest;if(l!==null&&(!object(l)||!text(l.sessionTitle)||!time(l.checkedInAt)||typeof l.late!=="boolean"))return invalid();
    return {mode:v.mode,source:v.source,capability:v.capability,memberId:v.memberId,memberName:v.memberName,attendance:{lastSevenDays:v.attendance.lastSevenDays,latest:l===null?null:{sessionTitle:(l as Record<string,unknown>).sessionTitle,checkedInAt:(l as Record<string,unknown>).checkedInAt,late:(l as Record<string,unknown>).late}}};
  }
  const m=v.member,a=v.attendance;if(!object(m)||m.id!==id||!text(m.tenantId)||!text(m.memberNumber)||!text(m.name)||!["lead","trial","active","paused","cancelled"].includes(String(m.membershipState))||!object(m.rank)||!text(m.rank.name)||!integer(m.rank.stripes)||typeof m.attendanceRate!=="number"||!Number.isFinite(m.attendanceRate)||m.attendanceRate<0||m.attendanceRate>1||!object(a)||!integer(a.lastSevenDays))return invalid();
  const l=a.latest;if(l!==null&&(!object(l)||!text(l.sessionTitle)||!time(l.checkedInAt)||typeof l.late!=="boolean"))return invalid();
  return {member:{id:m.id,tenantId:m.tenantId,memberNumber:m.memberNumber,name:m.name,membershipState:m.membershipState,rank:{name:m.rank.name,stripes:m.rank.stripes},attendanceRate:m.attendanceRate},attendance:{lastSevenDays:a.lastSevenDays,latest:l===null?null:{sessionTitle:(l as Record<string,unknown>).sessionTitle,checkedInAt:(l as Record<string,unknown>).checkedInAt,late:(l as Record<string,unknown>).late}}};
}
export async function handle(r:Request,c:GatewayConfig,op:Operation,id?:string,fetcher:Fetcher=fetch):Promise<Response> {
  const role:Role=["member","members","companion"].includes(op)?"staff":"kiosk";
  if(!authorized(c,role,r.headers.get("cookie")||""))return fail("FORBIDDEN",undefined,403);
  let correlation=`gateway-${randomUUID()}`;const params:Record<string,unknown>={token:c.token};
  if(op==="roster"||op==="member"){if(!recordId(id))return fail("INVALID_COMMAND",correlation,400);params[op==="roster"?"sessionId":"memberId"]=id;}
  if(op==="checkin"||op==="companion") {
    if(r.headers.get("origin")!==c.origin||!r.headers.get("content-type")?.includes("application/json"))return fail("FORBIDDEN",correlation,403);
    let b;try{b=await readBody(r);}catch{return fail("INVALID_COMMAND",correlation,400);}
    if(op==="checkin"){if(!validCommand(b))return fail("INVALID_COMMAND",correlation,400);correlation=b.correlationId as string;params.command=b;}
    else {if(!object(b)||Object.keys(b).some(k=>!["memberId","action","command"].includes(k))||!recordId(b.memberId)||!["attendance_summary","create_followup"].includes(String(b.action)))return fail("INVALID_COMMAND",correlation,400);if(b.action==="create_followup"&&(!object(b.command)||Object.keys(b.command).length!==3||b.command.approved!==true||!key(b.command.idempotencyKey)||!key(b.command.correlationId)))return fail("INVALID_COMMAND",correlation,400);Object.assign(params,b);if(object(b.command))correlation=b.command.correlationId as string;}
  }
  try {
    const response=await fetcher(c.backend+paths[op],{method:"POST",cache:"no-store",redirect:"error",headers:{"content-type":"application/json",authorization:`Bearer ${role==="staff"?c.staffKey:c.kioskKey}`},body:JSON.stringify({jsonrpc:"2.0",id:correlation,method:"call",params}),signal:AbortSignal.timeout(10000)});
    if(!response.ok)return uncertain(op,correlation);const envelope:unknown=await response.json();if(!object(envelope)||envelope.error||!object(envelope.result))return uncertain(op,correlation);const result=envelope.result;
    if(object(result.problem)){const p=result.problem;if(!codes.includes(p.code as ProblemCode)||typeof p.status!=="number"||p.status<400||p.status>599)return fail("CAPABILITY_DISABLED",correlation);return Response.json({type:`urn:dojang:problem:${p.code}`,code:p.code,status:p.status,title:messages[p.code as ProblemCode],detail:messages[p.code as ProblemCode],correlationId:correlation},{status:p.status,headers:{...safeHeaders,"content-type":"application/problem+json"}});}
    if(op==="checkin"&&!validResult(result,params.command as Record<string,unknown>))return uncertain(op,correlation);
    if(op==="companion"&&result.memberId!==params.memberId)return uncertain(op,correlation);
    const body=publicPayload(op,result,id);return Response.json(body,{status:op==="checkin"&&!result.replayed&&!(result.receipt as {alreadyRecorded?:boolean})?.alreadyRecorded?201:200,headers:safeHeaders});
  }catch{return uncertain(op,correlation);}
}
