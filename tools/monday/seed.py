"""Odoo shell fixture. Only a new dojang_demo_* database; no secrets printed."""
import hashlib,json,os,secrets
from pathlib import Path
from datetime import timedelta
from odoo import fields
if 'env' not in globals() or not env.cr.dbname.startswith('dojang_demo_'):raise RuntimeError('Synthetic database required')
out=Path(os.environ['DOJANG_ENV_OUTPUT']).resolve()
if out.exists():raise RuntimeError('Refusing to overwrite credentials')
if env['dojo.kiosk.config'].sudo().search_count([('integration_tenant_ref','=','dojang-demo-school')]):raise RuntimeError('Fixture already exists')
e=env(context=dict(env.context,tracking_disable=True,mail_create_nosubscribe=True));company=e.company
keys={k:secrets.token_urlsafe(48) for k in ('DOJANG_COOKIE_SECRET','DOJANG_KIOSK_GATEWAY_KEY','DOJANG_STAFF_GATEWAY_KEY','DOJANG_KIOSK_PAIR_KEY','DOJANG_STAFF_PAIR_KEY')}
program=e['dojo.program'].create({'name':'Demo program','company_id':company.id})
members=[]
for name in ('Avery Demo','Blake Demo'):
 p=e['res.partner'].create({'name':name,'email':name.split()[0].lower()+'@example.invalid'})
 members.append(e['dojo.member'].create({'partner_id':p.id,'company_id':company.id,'membership_state':'active'}))
template=e['dojo.class.template'].create({'name':'Demo session','company_id':company.id,'program_id':program.id,'auto_enroll_members':False,'course_member_ids':[(6,0,[m.id for m in members])]})
now=fields.Datetime.now()
session=e['dojo.class.session'].create({'template_id':template.id,'company_id':company.id,'start_datetime':now,'end_datetime':now+timedelta(hours=8),'state':'open','capacity':2})
plan=e['dojo.subscription.plan'].create({'name':'Demo plan','company_id':company.id,'currency_id':company.currency_id.id,'price':0,'plan_type':'program','auto_send_invoice':False,'program_ids':[(6,0,[program.id])]})
stage=e['sale.subscription.stage'].create({'name':'Demo active','type':'in_progress','in_progress':True})
price=e['product.pricelist'].create({'name':'Demo pricelist','currency_id':company.currency_id.id})
for m in members:
 e['sale.subscription'].create({'partner_id':m.partner_id.id,'company_id':company.id,'template_id':plan.template_id.id,'pricelist_id':price.id,'stage_id':stage.id,'member_id':m.id,'plan_id':plan.id})
 e['dojo.class.enrollment'].create({'session_id':session.id,'member_id':m.id,'status':'registered','attendance_state':'pending'})
kiosk=e['dojo.kiosk.config'].create({'name':'Demo kiosk','pin_code':str(secrets.randbelow(900000)+100000),'company_id':company.id,'integration_enabled':True,'integration_tenant_ref':'dojang-demo-school','integration_key_hash':hashlib.sha256(keys['DOJANG_KIOSK_GATEWAY_KEY'].encode()).hexdigest(),'integration_staff_key_hash':hashlib.sha256(keys['DOJANG_STAFF_GATEWAY_KEY'].encode()).hexdigest(),'integration_member_ids':[(6,0,[m.id for m in members])],'integration_session_ids':[(6,0,[session.id])]})
values={'DOJANG_INTEGRATION_MODE':'odoo-test','NEXT_PUBLIC_DEMO_MODE':'false','DOJANG_PUBLIC_ORIGIN':os.environ.get('DOJANG_PUBLIC_ORIGIN','http://localhost:3000'),'DOJANG_ODOO_URL':'http://127.0.0.1:8069','DOJANG_KIOSK_TOKEN':kiosk.kiosk_token,**keys}
fd=os.open(out,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
with os.fdopen(fd,'w') as f:
 for k,v in values.items():
  if '\n' in v or '\r' in v:raise RuntimeError('Invalid environment value')
  f.write(k+'='+v+'\n')
(out.parent/'fixture.json').write_text(json.dumps({'memberIds':[str(m.id) for m in members],'sessionId':str(session.id)}))
env.cr.commit()
print('Synthetic fixture seeded. Credentials written privately, not logged.')
