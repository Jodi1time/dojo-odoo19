const {test}=require('node:test');
const assert=require('node:assert/strict');
const {indexedDB}=require('fake-indexeddb');
global.indexedDB=indexedDB;
global.window=new EventTarget();
Object.defineProperty(global,'navigator',{value:{onLine:true},configurable:true});
const path='../.integration-build/integrations/offline-attendance.js';
const q=require(path);
delete require.cache[require.resolve(path)];
const otherTab=require(path);
let scope='a'.repeat(64), allowed=true, failure=null, calls=[];
global.fetch=async(url,init)=>{
 if(url==='/api/integration/state')return allowed?Response.json({mode:'odoo-test',offlineQueue:true,scope}):new Response(null,{status:403});
 const c=JSON.parse(init.body);calls.push(c);
 if(failure==='network')return new Response(null,{status:503});
 if(failure==='version')return Response.json({code:'VERSION_CONFLICT'},{status:409});
 return Response.json({receipt:{attendanceId:'10',memberId:c.payload.memberId,sessionId:c.payload.sessionId,checkedInAt:'2026-10-09T01:00:00Z',status:'present',alreadyRecorded:false,correlationId:c.correlationId},replayed:false,correlationId:c.correlationId});
};
const command=i=>({idempotencyKey:`queue-request-${String(i).padStart(8,'0')}`,correlationId:`queue-correlation-${String(i).padStart(8,'0')}`,expectedVersion:1,payload:{memberId:'1',sessionId:'2'}});
function read(store,key){return new Promise((resolve,reject)=>{const op=indexedDB.open('dojang-attendance-queue-v1',1);op.onsuccess=()=>{const db=op.result,tx=db.transaction(store),r=key===undefined?tx.objectStore(store).getAll():tx.objectStore(store).get(key);r.onsuccess=()=>resolve(r.result);tx.oncomplete=()=>db.close();tx.onerror=()=>reject(tx.error);};op.onerror=()=>reject(op.error);});}
test('offline attendance boundaries',async t=>{
 await t.test('not paired means no queue',async()=>{assert.equal(await q.queueAttendance(command(1)),false);});
 await t.test('concurrent tabs share one nonextractable encryption key',async()=>{await Promise.all([q.initializeQueue(),otherTab.initializeQueue()]);await Promise.all([q.queueAttendance(command(2)),otherTab.queueAttendance(command(3))]);assert.equal((await read('meta','encryption')).extractable,false);const entries=await read('entries');assert.equal(entries.length,2);assert.ok(entries.every(e=>e.cipher instanceof ArrayBuffer && !('payload' in e)));await q.flushAttendance();assert.equal((await q.queueState()).pending,0);assert.equal((await q.queueState()).review,0);});
 await t.test('a conflicting command cannot replace a queued key',async()=>{await q.queueAttendance(command(4));assert.equal(await q.queueAttendance({...command(4),payload:{memberId:'7',sessionId:'2'}}),false);await q.flushAttendance();assert.equal(calls.at(-1).payload.memberId,'1');});
 await t.test('lost reply retains command and original request key',async()=>{await q.queueAttendance(command(5));failure='network';await q.flushAttendance();assert.equal((await q.queueState()).pending,1);failure=null;await q.flushAttendance();assert.deepEqual(calls.at(-1),command(5));assert.equal((await q.queueState()).pending,0);});
 await t.test('expired authorization pauses replay without deleting attendance',async()=>{await q.queueAttendance(command(6));const n=calls.length;allowed=false;await q.flushAttendance();assert.equal(calls.length,n);assert.equal((await read('entries')).length,1);allowed=true;await q.flushAttendance();assert.equal((await q.queueState()).pending,0);});
 await t.test('another device scope cannot replay stored requests',async()=>{await q.queueAttendance(command(7));const n=calls.length;scope='b'.repeat(64);await q.flushAttendance();assert.equal(calls.length,n);assert.equal((await read('entries')).length,1);scope='a'.repeat(64);await q.flushAttendance();assert.equal((await q.queueState()).pending,0);});
 await t.test('capacity is enforced atomically across concurrent writers',async()=>{const results=await Promise.all(Array.from({length:35},(_,i)=>(i%2?q:otherTab).queueAttendance(command(100+i))));assert.equal(results.filter(Boolean).length,32);assert.equal((await read('entries')).length,32);await q.flushAttendance();assert.equal((await q.queueState()).pending,0);});
 await t.test('a terminal backend rejection becomes staff review, never success',async()=>{await q.queueAttendance(command(200));failure='version';await q.flushAttendance();assert.deepEqual(await q.queueState(),{pending:0,review:1});failure=null;});
});
