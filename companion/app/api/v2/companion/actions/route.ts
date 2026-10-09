import {odooRoute} from "@/server/odoo-runtime";
export const runtime="nodejs";
export async function POST(r:Request){return odooRoute(r,"companion");}
