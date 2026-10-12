// Real Odoo staff identity, PIN, native HR attendance and correction round trips.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {randomUUID}=require('node:crypto');
const fixture=JSON.parse(fs.readFileSync(process.env.DOJANG_FIXTURE_OUTPUT,'utf8'));
const origin='http://localhost:3000',out=process.env.DOJANG_EVIDENCE_DIR;
const checks=[];let stage='sign in';
const pass=name=>{checks.push({name,result:'passed'});console.log('PASS '+name);};
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const context=await browser.newContext({viewport:{width:1280,height:900},...(out?{recordVideo:{dir:path.join(out,'instructor-video'),size:{width:1280,height:900}}}:{})});
  const page=await context.newPage();page.setDefaultTimeout(20000);
  await page.goto(origin+'/hub?view=instructor');
  await page.getByLabel('Login',{exact:true}).fill(fixture.hubLogin);await page.getByLabel('Password',{exact:true}).fill(fixture.hubPassword);
  await page.getByRole('button',{name:'Sign in',exact:true}).click();
  const panel=page.getByRole('region',{name:'Instructor workspace',exact:true});
  await panel.getByText('Not currently clocked in',{exact:true}).waitFor();
  const call=async(operation,payload,requestKey=randomUUID())=>{const r=await context.request.post(origin+'/api/hub',{headers:{origin},data:{operation,payload,requestKey}});return {status:r.status(),value:await r.json()};};
  const before=(await call('instructor_context',{})).value;
  assert.equal(before.instructor.name,'Demo Instructor');
  stage='incorrect PIN';
  const bad=await call('instructor_clock',{direction:'in',currentShiftId:null,pin:'000000'});
  assert.equal(bad.status,403);assert.equal(bad.value.error.code,'PIN_INCORRECT');
  assert.equal((await call('instructor_context',{})).value.current,null);
  pass('incorrect PIN fails without creating a native shift');
  stage='review clock-in';
  await panel.getByLabel('Instructor PIN',{exact:true}).fill(fixture.instructorPin);
  await panel.getByRole('button',{name:'Review clock in',exact:true}).click();
  assert.equal((await call('instructor_context',{})).value.current,null);
  const writes=[];let loseResponse=true;
  await page.route('**/api/hub',async route=>{
   const body=route.request().postDataJSON();
   if(body.operation==='instructor_clock'){
    // Record only nonsecret command identity in test memory/output.
    writes.push({key:body.requestKey,direction:body.payload.direction,current:body.payload.currentShiftId});
    const response=await route.fetch();
    if(loseResponse){loseResponse=false;return route.abort('connectionfailed');}
    return route.fulfill({response});
   }
   return route.continue();
  });
  await panel.getByRole('button',{name:'Confirm instructor action in Odoo',exact:true}).click();
  await panel.getByText(/Odoo has not confirmed/).waitFor();
  assert.equal(await panel.getByLabel('Instructor PIN').isDisabled(),true);
  await panel.getByRole('button',{name:'Check original shift action',exact:true}).click();
  await panel.getByText('Clock-in confirmed in Odoo.',{exact:true}).waitFor();
  await panel.getByText(/Clocked in since/).waitFor();
  assert.equal(writes.length,2);assert.deepEqual(writes[0],writes[1]);
  const started=(await call('instructor_context',{})).value;
  assert.ok(started.current?.id);assert.equal(started.shifts.length,before.shifts.length+1);
  pass('review prevents early writes; lost committed response retries one original key and creates one shift');
  stage='refresh and clock-out';
  await page.reload();await panel.getByText(/Clocked in since/).waitFor();
  await panel.getByLabel('Instructor PIN',{exact:true}).fill(fixture.instructorPin);
  await panel.getByRole('button',{name:'Review clock out',exact:true}).click();
  await panel.getByRole('button',{name:'Confirm instructor action in Odoo',exact:true}).click();
  await panel.getByText('Clock-out confirmed in Odoo.',{exact:true}).waitFor();
  await panel.getByText('Not currently clocked in',{exact:true}).waitFor();
  assert.equal((await call('instructor_context',{})).value.current,null);
  pass('clock-in persists across reload and clock-out closes the exact native shift');
  stage='manager correction';
  const prior=panel.getByRole('article',{name:`Shift ${fixture.priorShiftId}`,exact:true});
  await prior.getByText('Correct this timesheet',{exact:true}).click();
  const input=await prior.getByLabel('Corrected clock-in',{exact:true}).inputValue();
  const changed=new Date(input);changed.setMinutes(changed.getMinutes()-30);
  const formatted=new Date(changed.getTime()-changed.getTimezoneOffset()*60000).toISOString().slice(0,16);
  await prior.getByLabel('Corrected clock-in',{exact:true}).fill(formatted);
  await prior.getByLabel('Correction reason',{exact:true}).fill('Synthetic client walkthrough: reviewed missed clock-out');
  await prior.getByRole('button',{name:'Review timesheet correction',exact:true}).click();
  await panel.getByRole('group',{name:'Review instructor action',exact:true}).getByText(/Correct Demo Instructor/).waitFor();
  await panel.getByRole('button',{name:'Confirm instructor action in Odoo',exact:true}).click();
  await panel.getByText(/Timesheet correction confirmed in Odoo/).waitFor();
  await page.reload();
  const after=(await call('instructor_context',{})).value;
  const saved=after.shifts.find(s=>s.id===fixture.priorShiftId);
  assert.equal(saved.needsReview,false);assert.ok(saved.hours>2.4&&saved.hours<2.6);
  pass('manager-reviewed correction persists and clears automatic-close review without approving payroll');
  stage='responsive screenshot';
  await panel.getByRole('heading',{name:'Team timesheets',exact:true}).waitFor();
  if(out)await page.screenshot({path:path.join(out,'instructor-connected-desktop.png'),fullPage:true});
  for(const width of [390,768,1920]){
   await page.setViewportSize({width,height:900});
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
  }
  if(out)await page.screenshot({path:path.join(out,'instructor-connected-wide.png'),fullPage:true});
  pass('connected instructor workspace fits phone, tablet, desktop and wide layouts');
  await context.close();
  if(out)await page.video().saveAs(path.join(out,'walkthrough-instructor.webm'));
  if(out)fs.writeFileSync(path.join(out,'instructor-browser-results.json'),JSON.stringify({mode:'real Odoo native HR attendance with one lost response',checks},null,2));
 }finally{await browser.close();}
})().catch(error=>{console.error('Instructor rehearsal failed at '+stage+': '+(error.code||error.name||'Error'));process.exitCode=1;});
