const {chromium}=require(process.env.CXPE_PLAYWRIGHT || 'playwright');
const fs=require('fs');
const base=process.env.CXPE_UI_BASE || 'http://127.0.0.1:8086';
(async()=>{
 const out=process.cwd()+'/docs/screens/'+(process.env.CXPE_UI_CAPTURE || 'ui-review');fs.mkdirSync(out,{recursive:true});
 const b=await chromium.launch({channel:'chrome',headless:true});
 const p=await b.newPage({viewport:{width:1600,height:1000}});const errors=[];p.on('pageerror',e=>errors.push(e.message));
 await p.goto(base);await p.waitForFunction(()=>document.querySelector('#sel-case').options.length>0);
 await p.screenshot({path:out+'/prep.png',fullPage:true});
 const results=[];
 for(const [name,expected] of [['preflight_warn',null],['pass','PASS'],['fail_start','FAIL'],['fail_temp','FAIL'],['fail_dropout','HOLD']]) {
   await p.locator('#nav-prep').evaluate(e=>e.click());
   await p.selectOption('#sel-case',name);await p.click('#btn-create');
   await p.waitForFunction(()=>S.ready && !S.busy,{},{timeout:180000});
   if(!await p.locator('#btn-approve').isDisabled())throw Error('Approval before review');
   await p.locator('#s-rules > summary').click();await p.check('#reviewed');
   if(!expected){if(!await p.locator('#btn-approve').isDisabled())throw Error('ERROR gate open');await p.locator('#s-rules > summary').click();await p.screenshot({path:out+'/blocked.png',fullPage:true});results.push({case:name,blocked:true});continue;}
   await p.click('#btn-approve');await p.waitForFunction(()=>S.approved && !S.busy);await p.selectOption('#sel-speed','0');await p.click('#btn-run');
   await p.waitForFunction(()=>S.overall && !S.es,{},{timeout:180000});
   const state=await p.evaluate(()=>({overall:S.overall,card:document.querySelector('#verdict-card').innerText,sid:S.sid,exceeded:S.exceeded}));
   if(state.overall!==expected)throw Error(name+' '+state.overall);
   if(!await p.locator('#btn-run').isDisabled())throw Error('Completed trial can be rerun');
   if(results.some(r=>r.sid===state.sid))throw Error('New trial reused a session');
   await p.waitForFunction(()=>document.querySelector('#report').textContent.includes('종합 판정:'));
   await p.screenshot({path:out+'/'+name+'.png',fullPage:true});
   await p.locator('#s-report > summary').click();
   if(!(await p.locator('#report').innerText()).trim())throw Error('Missing report');
   await p.locator('#s-report > summary').click();
   results.push({case:name,...state}); console.log('verified '+name);
 }
 for(const width of [1280,768,390]) {await p.setViewportSize({width,height:1000});await p.screenshot({path:out+'/run-'+width+'.png',fullPage:true});if(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1))throw Error('Run overflow '+width);await p.click('#nav-prep');if(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1))throw Error('Prep overflow '+width);await p.click('#nav-run');}
 const sid=results.at(-1).sid;await p.goto(base+'/?session='+sid);await p.waitForFunction(()=>S.overall==='HOLD' && !S.busy);
 if(!await p.locator('#btn-run').isDisabled() || !await p.locator('#btn-approve').isDisabled())throw Error('Reopened history is editable');
 await p.click('#nav-prep');
 await p.route('**/api/v1/sessions', route => route.request().method()==='POST' ? route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'UI verification: service unavailable'})}) : route.continue());
 await p.click('#btn-create'); await p.waitForFunction(()=>!S.busy && !document.querySelector('#ui-error').hidden);
 if(await p.locator('#btn-create').isDisabled() || !await p.locator('#btn-run').isDisabled()) throw Error('Failed creation did not recover');
 if(errors.length)throw Error(errors.join('\n'));
 fs.writeFileSync(out+'/results.json',JSON.stringify({results,errors,reopened:true,historyReadOnly:true,newTrialIsolation:true,creationFailureRecovered:true,responsive:[1600,1280,768,390]},null,2));
 console.log(JSON.stringify(results));await b.close();
})().catch(e=>{console.error(e);process.exit(1)});
