(function(){
  if(!document.getElementById('tp-sim'))return;
  var K=.62;/* playback speed: every pause and every scene length below is multiplied by this */
  var reduce=window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches;
  var $=function(s,r){return (r||document).querySelector(s)};
  var IC={dashboard:'speedometer2',flows:'diagram-3',analytics:'pie-chart',transactions:'list-columns-reverse',expenses:'credit-card',income:'cash-coin',budgets:'wallet2',subs:'arrow-repeat',calendar:'calendar3',accounts:'bank',holdings:'pie-chart',family:'people',loans:'shield-lock',capital:'gem',goals:'bullseye'};
  var NAV=[['Overview',[['dashboard','Dashboard'],['flows','TMR Flows','n','New'],['analytics','Analytics']]],
   ['Cash flow',[['transactions','Transactions'],['expenses','Expenses'],['income','Income'],['budgets','Budgets'],['subs','Subscriptions','d','due soon'],['calendar','Calendar','c','2 this week']]],
   ['Net worth',[['accounts','Accounts'],['holdings','Holdings'],['family','Family','n','New'],['loans','Loans','n','1 active'],['capital','Capital Events'],['goals','Goals','n','2 active']]]];
  function bi(n,c){return '<i class="bi bi-'+n+(c?' '+c:'')+'"></i>'}

  function C(n,dec){return '<span data-count="'+n+'" data-dec="'+(dec||0)+'">0</span>'}
  var S={};
  function fg(l,tag,body){return '<div class="fg"><label>'+l+(tag?' <em>'+tag+'</em>':'')+'</label>'+body+'</div>'}
  function usual(){return '<div class="ush">'+bi('lightning-charge')+' Your usual</div><div class="usual"><span>'+bi('house-door-fill')+' 2BHK Rent in Pune <b>₹18,000</b></span><span>'+bi('car-front-fill')+' Uber/Auto Spends <b>₹619</b></span><span>'+bi('basket-fill')+' Zepto/Blinkit Orders <b>₹1,763</b></span></div>'}
  function addForm(){return fg('Amount','Auto','<div class="amt2"><span class="cur">₹ INR '+bi('chevron-down')+'</span><span class="num">320</span></div>')+
    '<div class="fg"><label>Date <small>Today</small></label><div class="dpills"><span class="on">Today</span><span>Yesterday</span><span>Pick date</span></div></div>'+
    fg('Description','Auto','<div class="fi">Swiggy</div>')+
    fg('Category','Auto','<div class="fi sel">Dining Out '+bi('chevron-down')+'</div>')+fg('Account','Last used','<div class="fi sel">Cash Wallet (₹70,823) '+bi('chevron-down')+'</div>')+
    '<div class="fg wide"><label>Payment method</label><div class="pm"><span>'+bi('cash-stack')+'Cash</span><span>'+bi('credit-card-2-front')+'Credit Card</span><span>'+bi('credit-card')+'Debit Card</span><span class="on">'+bi('qr-code')+'UPI</span><span>'+bi('bank')+'NetBanking</span></div></div>'}
  S.add={nav:'expenses',url:'/expenses/add/',dur:13500,t:'Type it. It\'s logged.',d:'Write what you spent the way you would say it. Amount, category and account are filled in, and it lands in your ledger.',
   h:function(){return '<div class="addbox"><div class="pgt">Add a new expense</div><div class="pgs">Type it, say it, or tap one of your usuals.</div><div style="height:12px"></div>'+
    '<div class="card ab"><div class="inp" id="tp-inp">'+bi('pencil','ic')+'<span class="tx" id="tp-tx"><span style="color:var(--ink-400)">What did you spend on? e.g. swiggy 320</span></span>'+bi('mic-fill','mic2')+'<span class="go" id="tp-go">Go '+bi('arrow-right')+'</span></div>'+
    '<div id="tp-usl">'+usual()+'<div class="hint">Press <kbd>Enter</kbd> to fill the details. You can edit everything before adding.</div><div class="manual">Enter details manually '+bi('chevron-down')+'</div></div>'+
    '<div class="form" id="tp-form"><div class="fnote">Filled from what you typed. Check each detail, then add.</div><div class="fgrid">'+addForm()+'</div></div></div>'+
    '<div class="fbtn" id="tp-fb"><span class="cn">Cancel</span><span class="sv">Save and add another</span><span class="ad" id="tp-addb">Add expense</span></div></div>'+
    '<div class="toast" id="tp-toast">✓ Added ₹320 · Dining Out</div>'},
   run:function(ctx){
     var txt='swiggy 320',el=$('#tp-tx');
     ctx.after(1100,function(){ctx.move($('#tp-inp'),-120,0)});
     ctx.after(2000,function(){var k=0;(function st(){if(!ctx.live())return;el.innerHTML=txt.slice(0,k++)+'<span class="caret"></span>';if(k<=txt.length)setTimeout(st,95*K)})()});
     ctx.after(3300,function(){ctx.move($('#tp-go'),0,0)});
     ctx.after(4300,function(){ctx.click();$('#tp-usl').style.display='none';$('#tp-form').classList.add('on');$('#tp-fb').classList.add('on')});
     ctx.after(6200,function(){ctx.move($('#tp-addb'),0,0)});
     ctx.after(7100,function(){ctx.click();$('#tp-toast').classList.add('on')});
     ctx.after(8200,function(){ctx.nav('transactions',function(){ctx.render(txRows(),'/transactions/')})});
   }};
  function txRows(){var R=[['29 Oct 2026','e','Swiggy','Dining Out','Cash Wallet','− ₹320',1],['29 Oct 2026','e','Uber to Baner','Transport','Cash Wallet','− ₹450'],['29 Oct 2026','e','Swiggy Dinner Party','Dining Out','Cash Wallet','− ₹3,800'],['28 Oct 2026','i','Q2 Performance Bonus','Salary','HDFC Bank (Main)','+ ₹1L'],['28 Oct 2026','e','Lunch at Blue Frog','Dining Out','Cash Wallet','− ₹2,200'],['28 Oct 2026','e','Zepto – Weekend stock','Groceries','Cash Wallet','− ₹850'],['25 Oct 2026','e','Uber/Auto Spends','Transport','Cash Wallet','− ₹617']];
    return '<div style="display:flex;justify-content:space-between;align-items:flex-start"><div><div class="pgt">All Transactions</div><div class="pgs">36 transactions · 23 expenses · <b class="dn">₹54,830</b> · 3 incomes · <b class="up">₹1.6L</b> received</div></div><span class="btn-g">'+bi('plus-lg')+' Add '+bi('caret-down-fill')+'</span></div>'+
     '<table class="tbl"><tr><th>Date</th><th>Type</th><th>Description</th><th>Category</th><th>Account</th><th class="amt">Amount</th></tr>'+
     R.map(function(r,i){return '<tr class="rw'+(r[6]?' nrw':'')+'" style="animation-delay:'+(.1+i*.12)+'s"><td>'+r[0]+'</td><td><span class="ty '+r[1]+'">'+(r[1]=='e'?'EXPENSE':'INCOME')+'</span></td><td>'+r[2]+'</td><td><span class="cat'+(r[1]=='i'?' g':'')+'">'+r[3]+'</span></td><td style="color:var(--ink-600)">'+r[4]+'</td><td class="amt '+(r[1]=='e'?'dn':'up')+'">'+r[5]+'</td></tr>'}).join('')+'</table>'}

  S.nw={nav:'analytics',url:'/analytics/net-worth/',dur:9000,t:'Watch your net worth grow',d:'One real-time number across savings, SIPs, loans and cards, shown as a trajectory you can tap into.',
   h:function(){var pts=[[0,67],[1,64],[2,82],[3,81.7],[4,80.4],[5,80.3],[6,79.4],[7,79.2],[8,82.6],[9,81.8]],x=function(i){return 56+i*(560/9)},y=function(v){return 12+(85-v)/25*170};
    var d='M'+pts.map(function(p){return x(p[0]).toFixed(1)+' '+y(p[1]).toFixed(1)}).join(' L');
    var sm=smooth(pts.map(function(p){return[x(p[0]),y(p[1])]}));
    return '<div style="display:flex;justify-content:space-between"><div><div class="pgt">Analytics</div><div class="pgs">Where your money comes from, where it goes, and what it adds up to.</div></div><span class="btn-g" style="background:var(--teal-800)">'+bi('stars')+' View 2026 Wrapped</span></div>'+
    '<div class="seg"><span>'+bi('clock')+' Spending</span><span>'+bi('diagram-3')+' Cashflow</span><span class="on">'+bi('graph-up')+' Net worth</span></div><div class="nwf"><span class="selb">'+bi('calendar3')+' This year '+bi('chevron-down')+'</span><span class="selb dash">'+bi('plus-lg')+' Filter</span></div><div class="pgs" style="margin-top:8px">1 Jan – 29 Oct 2026</div>'+
    '<div class="card chart" style="position:relative;padding:0"><div class="hdr" style="padding:12px 16px;border-bottom:1px solid var(--surface-2)"><div style="font-weight:700;font-size:13.5px">'+bi('graph-up','mut')+' Net worth <span style="font-weight:400;color:var(--ink-600)">· At the end of each step in the period.</span></div><div style="text-align:right"><div style="font-size:17px;font-weight:700">₹'+C(8174822)+'</div><div class="up" style="font-size:11px;font-weight:700">▲ ₹14,83,616</div></div></div>'+
    '<svg viewBox="0 0 640 215" style="padding:6px 10px 0">'+[85,80,75,70,65,60].map(function(v){return '<line x1="56" x2="620" y1="'+y(v)+'" y2="'+y(v)+'" stroke="#e8e5de"/><text x="8" y="'+(y(v)+3)+'">₹'+v+'L</text>'}).join('')+
    ['31 Jan','31 Mar','31 May','31 Jul','30 Sep'].map(function(l,i){return '<text x="'+(x(i*2)-14)+'" y="205">'+l+'</text>'}).join('')+
    '<path class="ar" d="'+sm+' L'+x(9)+' '+y(60)+' L'+x(0)+' '+y(60)+'Z"/><path class="ln" pathLength="1" d="'+sm+'"/>'+
    pts.map(function(p,i){return '<circle class="pt" cx="'+x(p[0]).toFixed(1)+'" cy="'+y(p[1]).toFixed(1)+'" r="3.6" style="animation-delay:'+(.5+i*.2)+'s"/>'}).join('')+'</svg>'+
    '<div style="font-size:11px;color:var(--ink-600);padding:0 16px 12px">Click a point to see what changed up to it.</div><div class="tip" id="tp-tip" style="right:34px;top:92px">29 Oct · ₹81.7L · up ₹1L this month</div></div>'},
   run:function(ctx){ctx.after(3200,function(){ctx.move($('#tp-page .pt:last-of-type'),0,0)});ctx.after(4300,function(){ctx.click();var t=$('#tp-tip');if(t)t.classList.add('on')})}};
  function smooth(p){var d='M'+p[0][0].toFixed(1)+' '+p[0][1].toFixed(1);for(var i=1;i<p.length;i++){var a=p[i-1],b=p[i],cx=(a[0]+b[0])/2;d+=' C'+cx.toFixed(1)+' '+a[1].toFixed(1)+' '+cx.toFixed(1)+' '+b[1].toFixed(1)+' '+b[0].toFixed(1)+' '+b[1].toFixed(1)}return d}


  /* ---- the screens, in the order of the story: capture it, automate it, see it, understand it, share it ---- */
  function th(h,cls){return '<th'+(cls?' class="'+cls+'"':'')+'>'+h+'</th>'}
  function ph2(t,sub,right){return '<div class="p2h"><div><div class="pgt">'+t+'</div>'+(sub?'<div class="pgs">'+sub+'</div>':'')+'</div>'+(right||'')+'</div>'}
  function tbar(items,right){return '<div class="btl">'+items+(right||'')+'</div>'}
  var REC=[['01 Nov 2026','In 3d','Salary credited','Income','HDFC Bank (Main)','Monthly','+₹1,00,000','i'],['01 Nov 2026','In 3d','2BHK Rent in Pune','Housing','HDFC Bank (Main)','Monthly','−₹18,000'],['02 Nov 2026','In 4d','Mutual fund SIP','Investments','HDFC Bank (Main)','Monthly','−₹10,000','b'],['05 Nov 2026','In 7d','Airtel Broadband','Utilities','HDFC Bank (Main)','Monthly','−₹1,179'],['08 Nov 2026','In 10d','Netflix','Entertainment','Axis Credit Card','Monthly','−₹649'],['15 Nov 2026','In 17d','Term insurance premium','Insurance','HDFC Bank (Main)','Yearly','−₹14,200']];
  S.rec={nav:'subs',url:'/recurring/',dur:9500,t:'Set it once. It runs itself.',d:'Salary, rent, SIPs and premiums are posted on their due dates, with a reminder before each one. Nothing to remember.',
   h:function(){return ph2('Subscriptions '+bi('info-circle','mut'),'6 items · <b class="up">₹31,011</b> monthly · ₹1,020 daily · ₹3,72,132 yearly','<span class="btn-g">'+bi('plus-lg')+' Add Subscription</span>')+
    tbar('<span class="srch2">'+bi('search')+' Search subscriptions…</span><span class="selb dash">'+bi('plus-lg')+' Filter</span>','<span class="selb ml">'+bi('arrow-down-up')+' Renewal, soonest '+bi('chevron-down')+'</span>')+
    '<div class="pl"><span class="on">All 6</span><span>Renewing Soon 3</span><span>Cancelled 0</span></div>'+
    '<table class="tbl rec"><tr>'+th('Next Renewal')+th('Subscription')+th('Category')+th('Frequency')+th('Account')+th('Amount','amt')+th('Actions','act')+'</tr>'+
    REC.map(function(r,i){return '<tr class="rw'+(i==0?' nrw':'')+'" style="animation-delay:'+(.15+i*.14)+'s"><td><b>'+r[0]+'</b><small>'+r[1]+'</small></td><td>'+r[2]+'</td><td><span class="cat n'+(r[7]=='i'?' g':'')+'">'+r[3]+'</span></td><td>'+r[5]+'</td><td class="mutc">'+r[4]+'</td><td class="amt '+(r[7]=='i'?'up':(r[7]=='b'?'bl':'dn'))+'">'+r[6]+'</td><td class="act">'+bi('three-dots-vertical')+'</td></tr>'}).join('')+'</table>'+
    '<div class="toast" id="tp-toast">'+bi('check-circle-fill')+' Posted ₹1,00,000 · Salary credited</div>'},
   run:function(ctx){ctx.after(3600,function(){ctx.move($('#tp-page tr.nrw td:nth-child(2)'),0,0)});ctx.after(5200,function(){ctx.click();var t=$('#tp-toast');if(t)t.classList.add('on')})}};

  function tile(c,l,v,sub,cls){return '<div class="tl"><div class="tl-l"><i style="background:'+c+'"></i>'+l+'</div><div class="tl-v '+(cls||'')+'">'+v+'</div><div class="tl-s">'+sub+'</div></div>'}
  var ACC=[['2BHK Flat, Baner','Real Estate','','₹65,00,000'],['Zerodha Demat','Demat Account (Direct Equity)','','₹9,51,229'],['EPF · Provident Fund','Employee Provident Fund (EPF)','','₹4,18,600'],['SBI Fixed Deposit (5Y)','Fixed Deposit (FD) · Matures 28 Oct 2029','','₹2,00,000'],['Gold · Sovereign Gold Bond','Sovereign Gold Bond (SGB)','','₹1,32,400']];
  S.acc={nav:'accounts',url:'/accounts/list/',dur:10000,t:'Every Indian account in one place',d:'Savings, FDs, PPF, EPF, NPS, demat, gold, property and cards, grouped and added up into one net worth.',
   h:function(){return ph2('Accounts <small class="mut2">10 accounts</small>','','<div class="p2r"><span class="seg2"><span class="on">Me</span><span>Family</span></span><span class="selb">'+bi('clock-history')+' History</span><span class="btn-g">'+bi('plus-lg')+' Add account</span></div>')+
    tbar('<span class="srch2">'+bi('search')+' Search accounts…</span><span class="selb dash">'+bi('plus-lg')+' Filter</span>','<span class="selb ml">'+bi('arrow-down-up')+' Needs update first '+bi('chevron-down')+'</span>')+
    '<div class="card nwh"><div><div class="lbl">Net worth</div><div class="big2">₹'+C(8160792)+'</div><div class="dn sm">'+bi('caret-down-fill')+' −₹81,016 (1.0%) vs last month</div></div><div class="nwr"><div class="cbar"><i style="flex:6;background:#4F7BE0"></i><i style="flex:4;background:#C98A3B"></i><i style="flex:12;background:#7A5BE0"></i><i style="flex:79;background:#3FAE8C"></i></div><div class="legend" style="justify-content:flex-start"><span><i style="background:#4F7BE0"></i>Cash &amp; Bank <b>6%</b></span><span><i style="background:#C98A3B"></i>Fixed-Income <b>4%</b></span><span><i style="background:#7A5BE0"></i>Investments <b>12%</b></span><span><i style="background:#3FAE8C"></i>Physical Assets <b>79%</b></span></div><div class="sm mutc">₹82,25,109 in assets, minus ₹64,317 owed on cards</div></div></div>'+
    '<div class="tls">'+tile('#4F7BE0','Cash &amp; Bank','₹4,73,880','3 accounts · 6%')+tile('#C98A3B','Fixed-Income','₹3,00,000','2 accounts · 4%')+tile('#7A5BE0','Investments','₹9,51,229','1 account · 12%')+tile('#E0698A','Owed on cards','−₹64,317','2 of 2 cards','dn')+tile('#E0583F','Owed on loans','₹0','1 account')+tile('#3FAE8C','Physical Assets','₹65,00,000','1 account · 79%')+'</div>'+
    '<div class="pl" style="margin-top:12px"><span class="on">All</span><span>Pinned 0</span><span>Needs update 0</span></div>'+
    '<table class="tbl accs"><tr>'+th('Account')+th('Last updated')+th('Balance','amt')+th('','act')+'</tr>'+
    ACC.slice(0,3).map(function(r,i){return '<tr class="rw" style="animation-delay:'+(.4+i*.16)+'s"><td><b>'+r[0]+'</b><small>'+r[1]+'</small></td><td class="mutc">Updated today</td><td class="amt">'+r[3]+'</td><td class="act">'+bi('three-dots-vertical')+'</td></tr>'}).join('')+'</table>'}};

  function fcard(ic,t,d,tags,time,dly){return '<div class="card fl" style="animation-delay:'+dly+'s"><div class="fl-i">'+bi(ic)+'</div><b>'+t+'</b><span class="fl-d">'+d+'</span><div class="fl-t">'+tags.map(function(x){return '<em>'+x+'</em>'}).join('')+'</div><div class="fl-f"><small>'+time+'</small><span class="btn-g">Open setup</span></div></div>'}
  function chk(l,done,extra){return '<div class="ck'+(done?' done':'')+'"'+(extra||'')+'><span>'+bi(done?'check-circle-fill':'circle')+'<s>'+l+'</s></span><em>'+(done?'Done':'Set up →')+'</em></div>'}
  S.flow={nav:'flows',url:'/flows/landing/',dur:10500,t:'Tell it what happened in your life',d:'"I started a new job." "I took a loan." One short form sets up the account, the recurring entry and the reminders linked behind it.',
   h:function(){return '<div class="pgt" style="font-size:24px">TMR Flows</div><div class="pgs">Tell us what happened in your life. We\'ll set up everything linked behind it, in one form.</div>'+
    '<div class="pl"><span class="on">All</span><span>Debt</span><span>Income</span><span>Bills</span><span>Savings &amp; investments</span><span>Assets</span></div>'+
    '<div class="flw"><div><div class="eyb" style="margin:10px 0 8px">Set up a new flow</div><div class="g2">'+
     fcard('car-front','I bought a car','Cash or financed, either way it lands in your net worth.',['Vehicle','Optional loan'],'About 2 min',.2)+
     fcard('gem','I bought gold','Physical jewelry or digital/SGB, tracked either way.',['Physical or digital','Net worth'],'Under 1 min',.35)+
     fcard('briefcase','I started a new job','Salary, pay day and the salary cycle for your month view.',['Income source','Monthly salary'],'About 1 min',.5)+
     fcard('shield-check','I bought insurance','Policy details and the premium that renews on its own.',['Policy','Premium reminder'],'About 1 min',.65)+'</div></div>'+
    '<div class="side"><div class="card"><div class="ring2"><svg viewBox="0 0 36 36"><circle cx="18" cy="18" r="15" class="bgc"/><circle id="tp-rg" cx="18" cy="18" r="15" class="fgc" style="stroke-dasharray:'+(5/11*94.2).toFixed(1)+' 94.2"/></svg><b id="tp-rn">5/11</b></div><b class="stt2">Your setup</b><div class="pgs" style="margin-bottom:6px">The more you add, the sharper your insights.</div>'+chk('Loan',1)+chk('Credit Card',1)+'<div id="tp-sal">'+chk('Salary',0)+'</div>'+chk('SIP / RD',0)+chk('Rent / Bill',1)+'<div class="all">Show all (11)</div></div>'+
    '<div class="dark"><div class="eyb">What one form creates</div><b id="tp-dt">I took a loan</b><ul id="tp-dl"><li>Loan account with balance and rate</li><li>Repayment schedule</li><li>Recurring EMI transaction</li></ul><small>Always shown on Review before anything is saved.</small></div></div></div>'},
   run:function(ctx){
     ctx.after(2800,function(){ctx.move($('#tp-page .fl:nth-child(3) .btn-g'),0,0)});
     ctx.after(3800,function(){ctx.click();var t=$('#tp-dt'),l=$('#tp-dl');if(t)t.textContent='I started a new job';if(l)l.innerHTML='<li>Recurring income entry</li><li>Salary date on your profile</li><li>Updated month-cycle view</li>'});
     ctx.after(5600,function(){var e=$('#tp-sal');if(e)e.innerHTML=chk('Salary',1);var n=$('#tp-rn');if(n)n.textContent='6/11';var g=$('#tp-rg');if(g)g.style.strokeDasharray=(6/11*94.2).toFixed(1)+' 94.2'});
   }};

  function ftabs(on){return '<div class="ftabs">'+[['graph-up-arrow','Net worth'],['wallet2','Who holds what'],['share','What I share'],['life-preserver','If something happens to me'],['people','Members']].map(function(t,i){return '<span class="'+(i==on?'on':'')+'">'+bi(t[0])+' '+t[1]+'</span>'}).join('')+'</div>'}
  S.fam={demo:false,nav:'family',url:'/family/net-worth/',dur:9500,t:'One family. One true net worth.',d:'Each of you keeps your own account and shares only what you choose. The family sees the combined picture of what was shared, never a transaction.',
   h:function(){var pts=[[0,15.9],[1,16.1],[2,16.4],[3,16.35],[4,16.6]],x=function(i){return 40+i*(540/4)},y=function(v){return 14+(17-v)/1.4*120},sm=smooth(pts.map(function(p){return[x(p[0]),y(p[1])]}));
    return ftabs(0)+'<div class="pgt" style="margin-top:14px">Net worth</div><div class="pgs">Family view: the combined net worth of what your family has shared.</div>'+
    '<div class="note2">'+bi('info-circle-fill')+'<span><b>Based on what is shared.</b> The same for every member. Savings rate and the health check are not shown here.</span></div>'+
    '<div class="card fnw"><div class="fnw-h"><div><div class="lbl">Family net worth</div><div class="big3">₹'+C(1660792)+'</div><div class="pgs">8 shared items from 2 members</div></div><div class="pl" style="margin:0"><span>3M</span><span class="on2">Since sharing</span></div></div>'+
    '<div class="fnw-t"><div><small>What you own</small><b>₹17,25,109</b></div><div><small>What you owe</small><b class="dn">₹64,317</b></div></div>'+
    '<div class="chart" style="margin-top:6px"><svg viewBox="0 0 620 170"><line x1="40" x2="590" y1="140" y2="140" stroke="#e8e5de"/>'+
    '<path class="ar" d="'+sm+' L'+x(4)+' 140 L'+x(0)+' 140Z"/><path class="ln" pathLength="1" d="'+sm+'"/>'+pts.map(function(p,i){return '<circle class="pt" cx="'+x(p[0]).toFixed(1)+'" cy="'+y(p[1]).toFixed(1)+'" r="3.4" style="animation-delay:'+(.5+i*.25)+'s"/>'}).join('')+
    ['Start','Jul','Aug','Sep','Now'].map(function(l,i){return '<text x="'+x(i)+'" y="160" text-anchor="middle">'+l+'</text>'}).join('')+'</svg></div></div>'}};

  function sw(on){return '<span class="sw'+(on?' on':'')+'"><i></i></span>'}
  function shr(n,t,amt,on,id){return '<div class="shr" '+(id?'id="'+id+'" ':'')+'>'+sw(on)+'<div class="shr-n"><b>'+n+'</b><small>'+t+'</small></div><span class="shr-b'+(on?'':' off')+'">'+(on?'Amount counts':'Not shared')+'</span><span class="shr-a">'+amt+'</span><span class="selb">Edit sharing</span></div>'}
  S.priv={demo:false,nav:'family',url:'/family/share/',dur:11000,t:'Private by default. Shared by choice.',d:'No SMS reading, no bank login. In a family, nothing is shared until you switch it on, and you can preview exactly what they will see.',
   h:function(){return ftabs(2)+'<div class="p2h" style="margin-top:14px"><div><div class="pgt">What I share</div><div class="pgs">Switch on the accounts, cards, policies and loans your family should see. Everything else stays private.</div></div><span class="selb pv">Preview as a family member</span></div>'+
    '<div class="note2 grn">'+bi('lock-fill')+'<span><b id="tp-cnt">Sharing 8 of your 10 items.</b> Family sees names, types and amounts. They never see transactions, categories, notes or account numbers, and they cannot tell a private item exists.</span></div>'+
    '<div class="eyb" style="margin:12px 0 6px">Your accounts</div><div class="card shg"><div class="shg-h"><div><b>Bank and cash</b><small>2 of 2 shared</small></div><span class="selb">Stop sharing all</span></div>'+
    shr('Cash Wallet','Cash wallet','₹70,823',1)+shr('HDFC Bank (Main)','Savings account','₹2,78,056.96',1)+'</div>'+
    '<div class="card shg" style="margin-top:12px"><div class="shg-h"><div><b>Credit cards</b><small id="tp-gc">2 of 2 shared</small></div></div>'+shr('HDFC Regalia Credit Card','Credit card','−₹41,200',1,'tp-row1')+shr('Axis Credit Card','Credit card','−₹23,117',1)+'</div>'},
   run:function(ctx){ctx.after(2600,function(){ctx.move($('#tp-row1 .sw'),0,0)});
     ctx.after(3600,function(){ctx.click();var r=$('#tp-row1');if(r){r.querySelector('.sw').classList.remove('on');var b=r.querySelector('.shr-b');b.textContent='Not shared';b.classList.add('off')}var c=$('#tp-cnt');if(c)c.textContent='Sharing 7 of your 10 items.';var g=$('#tp-gc');if(g)g.textContent='1 of 2 shared'})}};

  var ORDER=['add','rec','acc','nw','flow','fam','priv'],LABEL={add:'Quick add',rec:'Recurring',acc:'Accounts',nw:'Net worth',flow:'TMR Flows',fam:'Family',priv:'Privacy'};

  /* ---- phone scenes ---- */
  var P={};
  P.add={url:'/expenses/add/',nav:'fab',dur:14000,t:'Type it. It\'s logged.',d:'Write what you spent the way you would say it. Amount, category and account are filled in, and it lands in your ledger.',
   h:function(){return '<div class="ph-t">Add a new expense</div><div class="pgs" style="margin:2px 0 12px">Type it, say it, or tap one of your usuals.</div>'+
    '<div class="card"><div class="inp" id="tp-inp">'+bi('pencil','ic')+'<span class="tx" id="tp-tx"><span style="color:var(--ink-400)">e.g. swiggy 320</span></span>'+bi('mic-fill','mic2')+'<span class="go" id="tp-pgo">'+bi('arrow-right')+'</span></div>'+
    '<div id="tp-usl">'+usual()+'<div class="manual">Enter details manually '+bi('chevron-down')+'</div></div>'+
    '<div class="form" id="tp-form"><div class="fnote">Filled from what you typed. Check each detail, then add.</div><div class="fgrid">'+addForm()+'</div><div class="fbtn on" style="margin-top:12px"><span class="ad" id="tp-addb" style="flex:1;text-align:center">Add expense</span></div></div></div>'
    },
   run:function(ctx){
     var txt='swiggy 320',el=$('#tp-tx');
     ctx.after(900,function(){ctx.move($('#tp-inp'),-60,0)});
     ctx.after(1700,function(){ctx.click();var k=0;(function st(){if(!ctx.live())return;el.innerHTML=txt.slice(0,k++)+'<span class="caret"></span>';if(k<=txt.length)setTimeout(st,95*K)})()});
     ctx.after(3000,function(){ctx.move($('#tp-pgo'),0,0)});
     ctx.after(3900,function(){ctx.click();$('#tp-usl').style.display='none';$('#tp-form').classList.add('on');ctx.scroll(150)});
     ctx.after(6000,function(){ctx.move($('#tp-addb'),0,0)});
     ctx.after(7000,function(){ctx.click();ctx.toast('✓ Added ₹320 · Dining Out')});
     ctx.after(8400,function(){ctx.nav('tx',function(){ctx.render(ptx(),'/transactions/')})});
   }};
  function ptx(){function r(i,t,a,am,d,n,cl){return '<div class="txc'+(n?' nrw':'')+'" style="animation-delay:'+d+'s"><div class="ti">'+bi(i)+'</div><div class="tm"><b>'+t+'</b><span>'+a+'</span></div><div class="ta '+cl+'">'+am+'</div></div>'}
    return '<div class="ph-h"><span class="ph-t">All Transactions</span><span class="btn-g">'+bi('plus-lg')+' Add '+bi('caret-down-fill')+'</span></div><div class="stt"><div><small>Income</small><b class="up">₹1.6L</b></div><div><small>Expenses</small><b class="dn">₹54,830</b></div><div><small>Transfers</small><b class="bl">₹2.1L</b></div></div><div class="srch">'+bi('search')+' Search transactions…</div>'+
     '<div class="dg"><span>29 Oct, 2026</span><b>− ₹4,570</b></div>'+r('cup-hot','Swiggy','Cash Wallet','− ₹320',.15,1,'dn')+r('car-front','Uber to Baner','Cash Wallet','− ₹450',.3,0,'dn')+r('cup-hot','Swiggy Dinner Party','Cash Wallet','− ₹3,800',.45,0,'dn')+
     '<div class="dg"><span>28 Oct, 2026</span><b class="up">+ ₹1L</b></div>'+r('box-arrow-in-down-left','Q2 Performance Bonus','HDFC Bank (Main)','+ ₹1L',.6,0,'up')+r('cup-hot','Lunch at Blue Frog','Cash Wallet','− ₹2,200',.75,0,'dn')}

  P.nw={url:'/analytics/net-worth/',nav:'menu',dur:10000,t:'Watch your net worth grow',d:'One real-time number across savings, SIPs, loans and cards, shown as a trajectory you can tap into.',
   h:function(){var pts=[[0,67],[1,64],[2,82],[3,81.7],[4,80.4],[5,80.3],[6,79.4],[7,79.2],[8,82.6],[9,81.8]],x=function(i){return 46+i*(272/9)},y=function(v){return 14+(85-v)/25*210},sm=smooth(pts.map(function(p){return[x(p[0]),y(p[1])]}));
    return '<div class="ph-h"><span class="ph-t">Analytics</span><span class="btn-g" style="padding:8px 11px">'+bi('stars')+' 2026 Wrapped</span></div><div class="seg"><span>Spending</span><span>Cashflow</span><span class="on">Net worth</span></div><div style="margin:12px 0 4px;display:flex;gap:8px"><span class="selb">'+bi('calendar3')+' This year '+bi('chevron-down')+'</span><span class="selb dash">'+bi('plus-lg')+' Filter</span></div><div class="pgs">1 Jan – 29 Oct 2026</div>'+
    '<div class="card chart" style="position:relative;padding:0;margin-top:10px"><div class="hdr" style="padding:12px 14px;border-bottom:1px solid var(--surface-2)"><div style="font-weight:700;font-size:13.5px">Net worth</div><div style="text-align:right"><div style="font-size:16px;font-weight:700">₹'+C(8174822)+'</div><div class="up" style="font-size:11px;font-weight:700">▲ ₹14,83,616</div></div></div>'+
    '<svg viewBox="0 0 330 262" style="padding:4px 4px 0">'+[85,80,75,70,65,60].map(function(v){return '<line x1="46" x2="324" y1="'+y(v)+'" y2="'+y(v)+'" stroke="#e8e5de"/><text x="6" y="'+(y(v)+3)+'">₹'+v+'L</text>'}).join('')+['31 Jan','31 Mar','31 May','31 Jul','30 Sep'].map(function(l,i){return '<text x="'+(x(i*2)-14)+'" y="256">'+l+'</text>'}).join('')+
    '<path class="ar" d="'+sm+' L'+x(9)+' '+y(60)+' L'+x(0)+' '+y(60)+'Z"/><path class="ln" pathLength="1" d="'+sm+'"/>'+pts.map(function(p,i){return '<circle class="pt" cx="'+x(p[0]).toFixed(1)+'" cy="'+y(p[1]).toFixed(1)+'" r="3.6" style="animation-delay:'+(.5+i*.2)+'s"/>'}).join('')+'</svg>'+
    '<div style="font-size:11px;color:var(--ink-600);padding:0 14px 12px">Tap a point to see what changed up to it.</div><div class="tip" id="tp-tip" style="right:20px;top:120px">29 Oct · ₹81.7L · up ₹1L</div></div>'},
   run:function(ctx){ctx.after(3300,function(){ctx.move($('#tp-page .pt:last-of-type'),0,0)});ctx.after(4300,function(){ctx.click();var t=$('#tp-tip');if(t)t.classList.add('on')})}};

  function prow(i,t,sub,am,cl,d){return '<div class="txc" style="animation-delay:'+d+'s"><div class="ti">'+bi(i)+'</div><div class="tm"><b>'+t+'</b><span>'+sub+'</span></div><div class="ta '+cl+'">'+am+'</div></div>'}
  P.rec={url:'/recurring/',nav:'menu',dur:10000,t:S.rec.t,d:S.rec.d,
   h:function(){return '<div class="ph-h"><span class="ph-t">Subscriptions '+bi('info-circle','mut')+'</span><span class="btn-g">'+bi('plus-lg')+' Add</span></div><div class="pgs">6 items · <b class="up">₹31,011</b> monthly · ₹3,72,132 yearly</div><div class="srch">'+bi('search')+' Search subscriptions…</div><div class="pl"><span class="on">All 6</span><span>Renewing Soon 3</span></div><div style="height:6px"></div>'+
    prow('cash-coin','Salary credited','1 Nov · In 3d · Monthly','+₹1,00,000','up',.2)+prow('house-door','2BHK Rent in Pune','1 Nov · In 3d · Monthly','−₹18,000','dn',.35)+prow('graph-up-arrow','Mutual fund SIP','2 Nov · In 4d · Monthly','−₹10,000','bl',.5)+prow('wifi','Airtel Broadband','5 Nov · In 7d · Monthly','−₹1,179','dn',.65)+prow('play-circle','Netflix','8 Nov · In 10d · Monthly','−₹649','dn',.8)+prow('shield-check','Term insurance premium','15 Nov · In 17d · Yearly','−₹14,200','dn',.95)},
   run:function(ctx){ctx.after(3800,function(){ctx.scroll(120)})}};
  P.acc={url:'/accounts/list/',nav:'acc',dur:11000,t:S.acc.t,d:S.acc.d,
   h:function(){return '<div class="ph-h"><span class="ph-t">Accounts</span><span class="btn-g">'+bi('plus-lg')+' Add</span></div><div class="card nwh"><div><div class="lbl">Net worth</div><div class="big2">₹'+C(8160792)+'</div><div class="dn sm">'+bi('caret-down-fill')+' −₹81,016 (1.0%) vs last month</div></div><div class="cbar" style="margin-top:10px"><i style="flex:6;background:#4F7BE0"></i><i style="flex:4;background:#C98A3B"></i><i style="flex:12;background:#7A5BE0"></i><i style="flex:79;background:#3FAE8C"></i></div></div>'+
    '<div class="tls">'+tile('#4F7BE0','Cash &amp; Bank','₹4,73,880','3 accounts · 6%')+tile('#C98A3B','Fixed-Income','₹3,00,000','2 accounts · 4%')+tile('#7A5BE0','Investments','₹9,51,229','1 account · 12%')+tile('#E0698A','Owed on cards','−₹64,317','2 of 2 cards','dn')+'</div><div style="height:6px"></div>'+
    prow('building','2BHK Flat, Baner','Real Estate','₹65,00,000','',.3)+prow('graph-up-arrow','Zerodha Demat','Demat Account (Direct Equity)','₹9,51,229','',.45)+prow('piggy-bank','EPF · Provident Fund','Employee Provident Fund (EPF)','₹4,18,600','',.6)+prow('bank','SBI Fixed Deposit (5Y)','Fixed Deposit · Matures 28 Oct 2029','₹2,00,000','',.75)+prow('gem','Gold · Sovereign Gold Bond','Sovereign Gold Bond (SGB)','₹1,32,400','',.9)},
   run:function(ctx){ctx.after(3600,function(){ctx.scroll(300)})}};
  P.flow={url:'/flows/landing/',nav:'menu',dur:10500,t:S.flow.t,d:S.flow.d,
   h:function(){return '<div class="ph-t">TMR Flows</div><div class="pgs" style="margin:2px 0 8px">Tell us what happened in your life. We\'ll set up everything linked behind it, in one form.</div><div class="pl"><span class="on">All</span><span>Debt</span><span>Income</span></div><div class="eyb" style="margin:12px 0 8px">Set up a new flow</div>'+
    fcard('briefcase','I started a new job','Salary, pay day and the salary cycle for your month view.',['Income source','Monthly salary'],'About 1 min',.2)+fcard('car-front','I bought a car','Cash or financed, either way it lands in your net worth.',['Vehicle','Optional loan'],'About 2 min',.35)+fcard('shield-check','I bought insurance','Policy details and the premium that renews on its own.',['Policy','Premium reminder'],'About 1 min',.5)+
    '<div class="card" style="margin-top:4px"><div class="ring2"><svg viewBox="0 0 36 36"><circle cx="18" cy="18" r="15" class="bgc"/><circle cx="18" cy="18" r="15" class="fgc" style="stroke-dasharray:'+(5/11*94.2).toFixed(1)+' 94.2"/></svg><b>5/11</b></div><b class="stt2">Your setup</b><div class="pgs" style="margin-bottom:6px">The more you add, the sharper your insights.</div>'+chk('Loan',1)+chk('Salary',0)+chk('SIP / RD',0)+chk('Rent / Bill',1)+'</div>'},
   run:function(ctx){ctx.after(3600,function(){ctx.scroll(380)})}};
  P.fam={demo:false,url:'/family/net-worth/',nav:'menu',dur:10000,t:S.fam.t,d:S.fam.d,
   h:function(){return '<div class="ftabs">'+[['graph-up-arrow','Net worth',1],['wallet2','Who holds what'],['share','What I share'],['life-preserver','If something happens']].map(function(t){return '<span class="'+(t[2]?'on':'')+'">'+bi(t[0])+' '+t[1]+'</span>'}).join('')+'</div><div class="ph-t" style="margin-top:12px">Net worth</div><div class="pgs">Family view: the combined net worth of what your family has shared.</div><div class="note2" style="margin-top:10px">'+bi('info-circle-fill')+'<span><b>Based on what is shared.</b> The same for every member.</span></div>'+
    '<div class="card fnw"><div class="lbl">Family net worth</div><div class="big3">₹'+C(1660792)+'</div><div class="pgs">8 shared items from 2 members</div><div class="fnw-t" style="margin-top:12px"><div><small>What you own</small><b>₹17,25,109</b></div><div><small>What you owe</small><b class="dn">₹64,317</b></div></div></div>'+
    '<div class="card"><div class="ttl"><span class="tt">'+bi('people')+' Members</span></div>'+prow('person-circle','Riya (you)','Owner · Pro','5 items','',.4)+prow('person-circle','Asha','Member · Pro','3 items','',.6)+'</div>'},
   run:function(ctx){ctx.after(3600,function(){ctx.scroll(200)})}};
  P.priv={demo:false,url:'/family/share/',nav:'menu',dur:11000,t:S.priv.t,d:S.priv.d,
   h:function(){return '<div class="ph-t">What I share</div><div class="pgs" style="margin:2px 0 10px">Switch on the accounts your family should see. Everything else stays private.</div><div class="note2 grn">'+bi('lock-fill')+'<span><b id="tp-cnt">Sharing 8 of your 10 items.</b> They never see transactions, notes or account numbers.</span></div>'+
    '<div class="card shg" style="margin-top:12px"><div class="shg-h"><div><b>Bank and cash</b><small>3 of 3 shared</small></div></div>'+shr('Cash Wallet','Cash wallet','₹70,823',1)+shr('HDFC Bank (Main)','Savings account','₹2,78,057',1)+shr('SBI Fixed Deposit','Fixed deposit','₹2,00,000',1)+'</div>'+
    '<div class="card shg"><div class="shg-h"><div><b>Credit cards</b><small id="tp-gc">2 of 2 shared</small></div></div>'+shr('HDFC Regalia Credit Card','Credit card','−₹41,200',1,'tp-row1')+shr('Axis Credit Card','Credit card','−₹23,117',1)+'</div>'},
   run:S.priv.run};

  var SETS={desk:S,phone:P};
  /* the words come from the page (translated there); the scene text above is only the fallback */
  [].forEach.call(document.querySelectorAll('#tp-captions [data-k]'),function(el){var k=el.getAttribute('data-k');
    if(LABEL[k]===undefined)return;LABEL[k]=el.getAttribute('data-l')||LABEL[k];
    [S,P].forEach(function(set){if(set[k]){set[k].t=el.getAttribute('data-t')||set[k].t;set[k].d=el.getAttribute('data-d')||set[k].d}})});
  var PNAV=[['home','house-door','house-door-fill'],['tx','file-text','file-text-fill'],['acc','bank','bank2'],['menu','grid','grid']];

  /* ---- player ---- */
  var fitW=0,sim=$('#tp-sim'),vp=$('#tp-vp'),frame=$('#tp-frame'),wrap=$('#tp-stage'),box=$('#tp-box'),pills=$('#tp-pills'),cap=$('#tp-cap'),cursor,tok=0,cur=-1,hover=false,userPaused=false,inView=true,mode='desk',CW=1200,CH=800,scale=1,timers=[],userPick=false;
  function buildChrome(){
    if(mode==='phone'){
      sim.innerHTML='<div class="isl"></div><div class="ph-status"><span>9:41</span><span class="sg">'+bi('reception-4')+bi('wifi')+bi('battery-full')+'</span></div><div class="ph-scroll"><div id="tp-page"></div></div><div class="ph-fade"></div>'+
       '<div class="ph-nav">'+PNAV.map(function(n){return '<div class="pn" data-k="'+n[0]+'">'+bi(n[1])+(n[0]==='menu'?'<span class="nd"></span>':'')+'</div>'}).join('')+'</div><div class="fab" data-k="fab">'+bi('plus-lg')+'</div>'+
       '<div class="cursor" id="tp-cursor"></div>';
    }else{
      var nav='';NAV.forEach(function(s){nav+='<div class="sec">'+s[0]+'</div>';s[1].forEach(function(i){nav+='<div class="ni" data-k="'+i[0]+'">'+bi(IC[i[0]])+i[1]+(i[2]?'<b class="'+i[2]+'">'+i[3]+'</b>':'')+'</div>'})});
      sim.innerHTML='<div class="sb"><div class="brand"><svg viewBox="0 0 32 32" fill="none"><rect width="32" height="32" rx="6" fill="#1D9E75"/><path d="M6 23L12 17L16 21L26 9" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/><circle cx="26" cy="9" r="2.5" fill="#fff"/></svg>TrackMyRupee</div><div class="find"><span>'+bi('search')+' Find a page</span><kbd>Ctrl K</kbd></div><div class="nvs">'+nav+'</div><div class="usr"><span class="av">DE</span><div><b>Riya</b><small>Pro plan</small></div>'+bi('chevron-down','cv')+'</div></div>'+
       '<div class="mn"><div class="hd"><span>Good morning, <strong>Riya</strong></span><span class="r"><span class="add">'+bi('plus-circle-fill')+' Add '+bi('caret-down-fill','cr')+'</span><span class="bell">'+bi('bell')+'<span class="bd"></span></span></span></div><div class="pg-panel"><div id="tp-page"></div></div></div>'+
       '<svg class="cursor" id="tp-cursor" viewBox="0 0 22 22"><path d="M3 2l14 7-6 2-2 6z" fill="#1a1a19" stroke="#fff" stroke-width="1.4" stroke-linejoin="round"/></svg>';
    }
    cursor=$('#tp-cursor');
  }
  function fit(){
    vp.style.height='';
    var w=vp.clientWidth;fitW=w;
    if(mode==='phone'){CW=390;CH=800}else{CW=1200;CH=700}
    scale=w/CW;sim.style.width=CW+'px';sim.style.height=CH+'px';sim.style.transform='scale('+scale+')';sim.classList.toggle('tp-phone',mode==='phone');vp.style.height=(CH*scale)+'px';
  }
  function pos(el,dx,dy){var r=el.getBoundingClientRect(),s=sim.getBoundingClientRect();return[(r.left-s.left)/scale+r.width/2+(dx||0),(r.top-s.top)/scale+r.height/2+(dy||0)]}
  function ctxFor(my){
    return {live:function(){return my===tok},
     after:function(ms,fn){if(reduce)return;var t=setTimeout(function(){if(my===tok)fn()},ms*K);timers.push(t)},
     move:function(el,dx,dy){if(!el)return;var p=pos(el,dx,dy);cursor.classList.add('on');cursor.style.transform='translate('+p[0]+'px,'+p[1]+'px)'},
     click:function(){cursor.classList.remove('click');void cursor.getBoundingClientRect();cursor.classList.add('click')},
     scroll:function(y){var s=$('#tp-sc');if(!s)return;s.style.transition='transform 1.5s cubic-bezier(.4,0,.2,1)';s.style.transform='translateY('+(-y)+'px)'},
     toast:function(m){var t=document.createElement('div');t.className='toast on';t.textContent=m;t.style.cssText='position:absolute;left:50%;right:auto;bottom:96px;transform:translateX(-50%);white-space:nowrap;z-index:9';$('.ph-scroll').appendChild(t)},
     render:function(h,url){if(url)setUrl(url);var pg=$('#tp-page');pg.style.animation='none';void pg.offsetWidth;pg.style.animation='';pg.innerHTML=mode==='phone'?'<div class="sc" id="tp-sc">'+h+'</div>':h;runCounts()},
     nav:function(k,cb){var ctx=this,el=sim.querySelector((mode==='phone'?'.pn[data-k="':'.ni[data-k="')+k+'"]')||sim.querySelector('.fab[data-k="'+k+'"]');
       if(!el){setActive(k);cb&&cb();return}
       ctx.move(el,mode==='phone'?0:-20,0);ctx.after(950,function(){ctx.click();setActive(k);cb&&cb()})}};
  }
  function setUrl(u){var el=document.querySelector('#tp-frame .tp-bar span');if(el)el.textContent='trackmyrupee.com'+u}
  function setActive(k){if(k==='fab')return;if(k==='menu')k='';[].forEach.call(sim.querySelectorAll('.ni,.pn'),function(n){var on=n.getAttribute('data-k')===k;n.classList.toggle('on',on);var ic=n.querySelector('.bi'),d=mode==='phone'&&PNAV.filter(function(q){return q[0]===n.getAttribute('data-k')})[0];if(ic&&d)ic.className='bi bi-'+(on?d[2]:d[1])})}
  function count(el){var to=+el.getAttribute('data-count'),dec=+el.getAttribute('data-dec')||0,t0=null,d=reduce?1:1300;
    function f(ts){if(!t0)t0=ts;var p=Math.min(1,(ts-t0)/d),e=1-Math.pow(1-p,3);el.textContent=(to*e).toLocaleString('en-IN',{minimumFractionDigits:dec,maximumFractionDigits:dec});if(p<1)requestAnimationFrame(f)}
    requestAnimationFrame(f)}
  function runCounts(){[].forEach.call(sim.querySelectorAll('[data-count]'),count)}

  function show(i){
    var my=++tok;timers.forEach(clearTimeout);timers=[];cur=i;var key=ORDER[i],sc=SETS[mode][key],ctx=ctxFor(my);
    [].forEach.call(pills.children,function(p,j){if(j===i&&pills.scrollWidth>pills.clientWidth)pills.scrollTo({left:p.offsetLeft-(pills.clientWidth-p.offsetWidth)/2,behavior:reduce?'auto':'smooth'});p.setAttribute('aria-selected',j===i?'true':'false');var g=p.querySelector('.tp-pg');g.style.animation='none';void g.offsetWidth;g.style.animation='';if(j===i)p.style.setProperty('--dur',(sc.dur*K/1000)+'s')});
    var dl=box.getAttribute('data-demo'),tr=box.getAttribute('data-try');cap.innerHTML='<div class="tp-in"><h3>'+sc.t+'</h3><p>'+sc.d+'</p>'+(dl&&tr&&sc.demo!==false?'<a class="tp-try" href="'+dl+'?next='+encodeURIComponent(sc.url)+'">'+tr+' &rarr;</a>':'')+'</div>';
    var first=!sim.querySelector('.ni.on,.pn.on');
    if(first||reduce){setActive(sc.nav);ctx.render(sc.h(),sc.url);sc.run&&sc.run(ctx)}
    else{ctx.nav(sc.nav,function(){ctx.render(sc.h(),sc.url);sc.run&&sc.run(ctx)})}
    if(!reduce){var tick=function(){if(my!==tok)return;if(hover||userPaused||!inView||document.hidden){timers.push(setTimeout(tick,800));return}show((cur+1)%ORDER.length)};timers.push(setTimeout(tick,sc.dur*K))}
  }
  function setMode(m,fromUser){
    mode=m;if(fromUser)userPick=true;frame.classList.toggle('tp-ph',m==='phone');wrap.classList.toggle('tp-phm',m==='phone');
    [].forEach.call(document.querySelectorAll('#tp-dev [data-m]'),function(b){b.setAttribute('aria-pressed',b.getAttribute('data-m')===m?'true':'false')});
    buildChrome();fit();show(cur<0?0:cur);
  }
  ORDER.forEach(function(k,i){var b=document.createElement('button');b.className='tp-pill';b.setAttribute('role','tab');b.innerHTML=LABEL[k]+'<span class="tp-pg"></span>';b.addEventListener('click',function(){show(i)});pills.appendChild(b)});
  [].forEach.call(document.querySelectorAll('#tp-dev [data-m]'),function(b){b.addEventListener('click',function(){setMode(b.getAttribute('data-m'),true)})});
  function syncPaused(){box.classList.toggle('tp-paused',hover||userPaused||!inView)}
  box.addEventListener('mouseenter',function(){hover=true;syncPaused()});
  box.addEventListener('mouseleave',function(){hover=false;syncPaused()});
  var pauseBtn=$('#tp-pause');
  if(pauseBtn){if(reduce){pauseBtn.hidden=true}pauseBtn.addEventListener('click',function(){userPaused=!userPaused;pauseBtn.setAttribute('aria-pressed',userPaused?'true':'false');pauseBtn.textContent=userPaused?pauseBtn.getAttribute('data-play'):pauseBtn.getAttribute('data-pause');syncPaused()})}
  if(window.IntersectionObserver){new IntersectionObserver(function(es){inView=es[0].isIntersecting;syncPaused()},{threshold:.2}).observe(box)}
  document.addEventListener('visibilitychange',syncPaused);
  /* the stylesheet is loaded without blocking the page: start once it has applied */
  function whenStyled(go,tries){if(getComputedStyle(sim).position==='absolute'||tries>100){go();return}setTimeout(function(){whenStyled(go,(tries||0)+1)},50)}
  whenStyled(function(){
    var small=box.clientWidth<860;
    mode=small?'phone':'desk';
    setMode(mode,false);
    if(window.ResizeObserver){var ro=new ResizeObserver(function(){var n=box.clientWidth<860;if(n&&mode==='desk')setMode('phone',false);else if(!n&&mode==='phone'&&!userPick)setMode('desk',false);else if(vp.clientWidth!==fitW)fit()});ro.observe(box);ro.observe(vp)}
    else window.addEventListener('resize',fit);
  });
})();
