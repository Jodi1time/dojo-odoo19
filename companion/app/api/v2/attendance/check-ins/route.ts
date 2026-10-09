import {odooRoute,odooTestMode} from "@/server/odoo-runtime";
import {POST as mockPOST} from "@/integrations/mock/routes/checkins";
export const runtime="nodejs";
export async function POST(r:Request){return odooTestMode()?odooRoute(r,"checkin"):mockPOST(r);}
