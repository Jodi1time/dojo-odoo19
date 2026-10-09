import "server-only";
import {configFrom,fail,handle,pair,deviceState,type Operation} from "./odoo-gateway";
export const odooTestMode=()=>process.env.DOJANG_INTEGRATION_MODE==="odoo-test";
export async function odooRoute(r:Request,op:Operation,id?:string):Promise<Response>{try{return await handle(r,configFrom(process.env),op,id);}catch{return fail("CAPABILITY_DISABLED");}}
export async function pairingRoute(r:Request):Promise<Response>{try{return await pair(r,configFrom(process.env));}catch{return fail("CAPABILITY_DISABLED");}}
export function stateRoute(r:Request):Response{try{return deviceState(r,configFrom(process.env));}catch{return fail("CAPABILITY_DISABLED");}}
