import {odooRoute} from "@/server/odoo-runtime";
export const dynamic="force-dynamic";
export async function GET(r:Request,c:{params:Promise<{memberId:string}>}){return odooRoute(r,"member",(await c.params).memberId);}
