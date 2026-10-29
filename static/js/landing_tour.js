(function(){
  if(!document.getElementById('tp-sim'))return;
  var reduce=window.matchMedia&&matchMedia('(prefers-reduced-motion: reduce)').matches;
  var $=function(s,r){return (r||document).querySelector(s)};
  var IC={dashboard:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/>',
   flows:'<circle cx="12" cy="5" r="2"/><circle cx="5" cy="19" r="2"/><circle cx="19" cy="19" r="2"/><path d="M12 7v5M12 12l-6 5M12 12l6 5"/>',
   analytics:'<circle cx="12" cy="12" r="9"/><path d="M12 3v9l7 4"/>',
   transactions:'<path d="M4 6h16M4 10h16M4 14h10M4 18h10"/>',
   expenses:'<rect x="3" y="6" width="18" height="12" rx="2"/><path d="M3 10h18"/>',
   income:'<rect x="3" y="6" width="18" height="12" rx="2"/><circle cx="12" cy="12" r="2.5"/>',
   budgets:'<path d="M3 8a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
   subs:'<path d="M4 12a8 8 0 0 1 14-5l2 2M20 12a8 8 0 0 1-14 5l-2-2M20 4v5h-5M4 20v-5h5"/>',
   calendar:'<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/>',
   accounts:'<path d="M3 10l9-6 9 6M5 10v8M9.5 10v8M14.5 10v8M19 10v8M3 20h18"/>',
   holdings:'<circle cx="12" cy="12" r="9"/><path d="M12 3v9l7 4"/>',
   loans:'<path d="M12 3l8 3v6c0 4.5-3.2 7.8-8 9-4.8-1.2-8-4.5-8-9V6z"/>',
   goals:'<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.5"/>'};
  var NAV=[['Overview',[['dashboard','Dashboard'],['flows','TMR Flows','n','New'],['analytics','Analytics']]],
   ['Cash flow',[['transactions','Transactions'],['expenses','Expenses'],['income','Income'],['budgets','Budgets'],['subs','Subscriptions','d','due soon'],['calendar','Calendar','c','2 this week']]],
   ['Net worth',[['accounts','Accounts'],['holdings','Holdings'],['loans','Loans','n','1 active'],['goals','Goals','n','2 active']]]];

  function sparkPath(v){var w=100,h=34,mx=Math.max.apply(null,v),mn=Math.min.apply(null,v),n=v.length-1,p=v.map(function(y,i){return[i*w/n,h-4-(y-mn)/(mx-mn||1)*(h-10)]});
    var d='M'+p[0][0]+' '+p[0][1];for(var i=1;i<p.length;i++){var a=p[i-1],b=p[i],cx=(a[0]+b[0])/2;d+=' C'+cx+' '+a[1]+' '+cx+' '+b[1]+' '+b[0]+' '+b[1]}return d}
  function spark(v,c){return '<svg class="sp" viewBox="0 0 100 34" preserveAspectRatio="none"><path pathLength="1" vector-effect="non-scaling-stroke" stroke="'+c+'" d="'+sparkPath(v)+'"/></svg>'}
  function C(n,dec){return '<span data-count="'+n+'" data-dec="'+(dec||0)+'">0</span>'}
  function donut(segs){var off=0,s='<svg class="donut" viewBox="0 0 42 42"><circle cx="21" cy="21" r="15.9155" style="stroke:var(--surface-2);stroke-dasharray:100 0;animation:none"/>';
    segs.forEach(function(g,i){s+='<circle cx="21" cy="21" r="15.9155" stroke="'+g[1]+'" stroke-dashoffset="'+(-off)+'" style="--s:'+(g[0]-.6)+';animation-delay:'+(.3+i*.15)+'s"/>';off+=g[0]});return s+'</svg>'}

  var S={};
  S.dash={nav:'dashboard',dur:9000,t:'Your month at a glance',d:'Net worth, income, spending and savings rate on one screen, with calm insights beside them.',
   h:function(){return '<div class="grid2"><div class="k4">'+
    '<div class="card kpi"><div class="lbl">Net Worth →</div><div class="val">₹'+C(81.7,1)+'L</div><div class="dl up">▲ ₹1L <i>+1.2%</i> this month</div>'+spark([5,6,5.5,6,5.8,6,6.4,9],'#1D9E75')+'</div>'+
    '<div class="card kpi"><div class="lbl">Income</div><div class="val up">₹'+C(1.6,1)+'L</div><div class="dl up">▲ ₹1L <i>+185%</i> vs last month</div>'+spark([2,2,2,2.1,2,2,2.2,9],'#1D9E75')+'</div>'+
    '<div class="card kpi"><div class="lbl">Spent</div><div class="val dn">₹'+C(57670)+'</div><div class="dl dn">▲ ₹6,922 <i style="background:var(--coral-50)">+13.6%</i> vs last month</div>'+spark([3,3,4,8,4,3,3,3,3.4,3],'#C23E27')+'</div>'+
    '<div class="card kpi"><div class="lbl">Invested</div><div class="val bl">₹'+C(1.6,1)+'L</div><div class="dl bl">▲ ₹1.5L <i style="background:var(--blue-50)">10x+</i> vs last month</div>'+spark([2,2,2,2,2,2,2.2,8],'#185FA5')+'</div></div>'+
    '<div class="card"><div class="ttl">Smart Insights (October)</div>'+
     '<div class="ins-i" style="animation-delay:.5s"><div class="t">🏆</div><div><b>You crossed 50L in net worth!</b><span>That\'s years of discipline showing up as a number.</span></div></div>'+
     '<div class="ins-i" style="animation-delay:1.3s"><div class="t">✨</div><div><b>High five! You\'re saving more than usual this month.</b><span>Keep this momentum and you could save 12.1L this year.</span></div></div>'+
     '<div class="ins-i" style="animation-delay:2.1s"><div class="t">🍽</div><div><b>You spent 25% on Dining Out this month.</b><span>Cut 20% to save ₹2,943.</span></div></div></div></div>'+
    '<div class="g3">'+
     '<div class="card"><div class="ttl">Spending Pace <span class="badge r">Over budget</span></div><div class="val">₹'+C(1989)+'<small style="font-size:12px;font-weight:500;color:var(--ink-600)"> /day</small></div><div class="dl dn">Burn rate ↑ 18% vs last month</div><div class="pbar" style="margin-top:10px"><div style="--w:80%"></div></div><div class="mini"><div><small>Projected total spend</small><b>₹61,648</b></div><div><small>Projected under budget</small><b class="dn">₹10,352</b></div></div></div>'+
     '<div class="card"><div class="ttl">Asset Allocation</div>'+donut([[79,'#9a5b3c'],[11.6,'#185FA5'],[5.7,'#0F7657'],[3.7,'#5DCAA5']])+'<div class="legend"><span><i style="background:#9a5b3c"></i>Physical 79%</span><span><i style="background:#185FA5"></i>Invest 11.6%</span><span><i style="background:#0F7657"></i>Cash 5.7%</span></div></div>'+
     '<div class="card"><div class="ttl">Financial Health (October) <span class="badge g">Excellent</span></div><div class="val" style="font-size:32px">'+C(63.6,1)+'<small style="font-size:16px">%</small></div><div class="lbl">Savings rate</div><div class="pbar" style="margin-top:10px"><div style="--w:63.6%"></div></div><div class="mini"><div><small>Income</small><b class="up">₹1.6L</b></div><div><small>Spent</small><b class="dn">₹57,670</b></div></div></div></div>'}};

  S.add={nav:'expenses',dur:13500,t:'Type it. It\'s logged.',d:'Write what you spent the way you would say it. Amount, category and account are filled in, and it lands in your ledger.',
   h:function(){return '<div class="addbox"><div class="pgt">Add a new expense</div><div class="pgs">Type it, say it, or tap one of your usuals.</div><div style="height:12px"></div>'+
    '<div class="inp" id="tp-inp"><span style="color:var(--teal-400)">✎</span><span class="tx" id="tp-tx"><span class="caret"></span></span><span class="go" id="tp-go">Go →</span></div>'+
    '<div class="usual"><span>🏠 2BHK Rent in Pune <b>₹18,000</b></span><span>🛒 Zepto/Blinkit Orders <b>₹1,763</b></span><span>🍽 Weekend Swiggy/Dining <b>₹2,158</b></span></div>'+
    '<div class="form" id="tp-form"><div class="frow"><div class="fld"><small>Amount</small><b>₹320</b></div><div class="fld"><small>Description</small><b>Swiggy</b></div></div>'+
    '<div class="frow"><div class="fld"><small>Category</small><span class="chipc">🍽 Dining Out</span></div><div class="fld"><small>Account</small><b>Cash Wallet</b></div></div>'+
    '<div class="frow"><div class="fld"><small>Date</small><b>Today</b></div><div style="display:flex;align-items:center;justify-content:flex-end"><span class="btn-g" id="tp-addb">+ Add expense</span></div></div></div></div>'+
    '<div class="toast" id="tp-toast">✓ Added ₹320 · Dining Out</div>'},
   run:function(ctx){
     var txt='swiggy 320',el=$('#tp-tx');
     ctx.after(1100,function(){ctx.move($('#tp-inp'),-120,0)});
     ctx.after(2000,function(){var k=0;(function st(){if(!ctx.live())return;el.innerHTML=txt.slice(0,k++)+'<span class="caret"></span>';if(k<=txt.length)setTimeout(st,95)})()});
     ctx.after(3300,function(){ctx.move($('#tp-go'),0,0)});
     ctx.after(4300,function(){ctx.click();$('#tp-form').classList.add('on')});
     ctx.after(6200,function(){ctx.move($('#tp-addb'),0,0)});
     ctx.after(7100,function(){ctx.click();$('#tp-toast').classList.add('on')});
     ctx.after(8200,function(){ctx.nav('transactions',function(){ctx.render(txRows());})});
   }};
  function txRows(){var R=[['29 Oct 2026','e','Swiggy','Dining Out','Cash Wallet','− ₹320',1],['29 Oct 2026','e','Uber to Baner','Transport','Cash Wallet','− ₹450'],['29 Oct 2026','e','Swiggy Dinner Party','Dining Out','Cash Wallet','− ₹3,800'],['28 Oct 2026','i','Q2 Performance Bonus','Salary','HDFC Bank (Main)','+ ₹1L'],['28 Oct 2026','e','Lunch at Blue Frog','Dining Out','Cash Wallet','− ₹2,200'],['28 Oct 2026','e','Zepto – Weekend stock','Groceries','Cash Wallet','− ₹850'],['25 Oct 2026','e','Uber/Auto Spends','Transport','Cash Wallet','− ₹617']];
    return '<div style="display:flex;justify-content:space-between;align-items:flex-start"><div><div class="pgt">All Transactions</div><div class="pgs">36 transactions · 23 expenses · <b class="dn">₹54,830</b> · 3 incomes · <b class="up">₹1.6L</b> received</div></div><span class="btn-g">+ Add ▾</span></div>'+
     '<table class="tbl"><tr><th>Date</th><th>Type</th><th>Description</th><th>Category</th><th>Account</th><th class="amt">Amount</th></tr>'+
     R.map(function(r,i){return '<tr class="rw'+(r[6]?' nrw':'')+'" style="animation-delay:'+(.1+i*.12)+'s"><td>'+r[0]+'</td><td><span class="ty '+r[1]+'">'+(r[1]=='e'?'EXPENSE':'INCOME')+'</span></td><td>'+r[2]+'</td><td><span class="cat'+(r[1]=='i'?' g':'')+'">'+r[3]+'</span></td><td style="color:var(--ink-600)">'+r[4]+'</td><td class="amt '+(r[1]=='e'?'dn':'up')+'">'+r[5]+'</td></tr>'}).join('')+'</table>'}

  S.bud={nav:'budgets',dur:9000,t:'Know what is safe to spend today',d:'A daily number based on your budget, your pace and the days left in the month. Overspent categories surface first.',
   h:function(){return '<div style="display:flex;justify-content:space-between"><div class="pgt">Budget</div><span class="btn-g">+ New budget</span></div>'+
    '<div class="card" style="margin-top:12px"><div class="safe"><div><div class="eyb">Safe to spend today</div><div><span class="big">₹'+C(6880)+'</span> <span style="color:var(--ink-600)">per day · 3 days to go</span></div><div style="font-size:12px;margin:4px 0">You\'re <b class="up">₹15,995 under</b> expected pace for day 29. ₹20,640 of your ₹72,000 budget is still free.</div>'+
    '<div class="pace"><div class="pbar"><div style="--w:71.3%"></div></div><i></i><small><span>₹51,360 spent · 71.3%</span><span style="color:var(--amber-400);font-weight:700">Expected pace · ₹67,355</span><span>₹72,000</span></small></div></div>'+
    '<div class="ring"><svg viewBox="0 0 42 42"><circle class="bgc" cx="21" cy="21" r="15.9155"/><circle class="fgc" cx="21" cy="21" r="15.9155" style="--s:71"/></svg><div><b>'+C(71)+'%</b>of budget used</div></div></div></div>'+
    '<div class="chips"><span>🔴 over budget<em>2</em></span><span>⚠ at limit<em>1</em></span><span>✅ on track<em>7</em></span><span>⊖ no limit set<em>3</em></span></div>'+
    '<div style="font-weight:700;margin:10px 0 8px">Needs attention</div><div class="g3" style="margin-top:0">'+
    [['D','Dining Out','287% used','red','Over by ₹9,360','r',100],['G','Groceries','124% used','red','Over by ₹1,442','r',100],['R','Rent','100% used','amb','Limit reached · ₹0 left','a',100]].map(function(b,i){return '<div class="card bc"><div class="h"><span class="l"><i>'+b[0]+'</i>'+b[1]+'</span><span class="badge '+b[5]+'">'+b[2]+'</span></div><div class="pbar"><div class="'+b[3]+'" style="--w:'+b[6]+'%;animation-delay:'+(.5+i*.2)+'s"></div></div><div class="f"><span>'+b[4]+'</span><a>Adjust limit</a></div></div>'}).join('')+'</div>'}};

  S.nw={nav:'analytics',dur:9000,t:'Watch your net worth grow',d:'One real-time number across savings, SIPs, loans and cards, shown as a trajectory you can tap into.',
   h:function(){var pts=[[0,67],[1,64],[2,82],[3,81.7],[4,80.4],[5,80.3],[6,79.4],[7,79.2],[8,82.6],[9,81.8]],x=function(i){return 56+i*(560/9)},y=function(v){return 12+(85-v)/25*190};
    var d='M'+pts.map(function(p){return x(p[0]).toFixed(1)+' '+y(p[1]).toFixed(1)}).join(' L');
    var sm=smooth(pts.map(function(p){return[x(p[0]),y(p[1])]}));
    return '<div style="display:flex;justify-content:space-between"><div><div class="pgt">Analytics</div><div class="pgs">Where your money comes from, where it goes, and what it adds up to.</div></div><span class="btn-g" style="background:var(--teal-800)">✦ View 2026 Wrapped</span></div>'+
    '<div class="seg"><span>Spending</span><span>Cashflow</span><span class="on">Net worth</span></div>'+
    '<div class="card chart" style="position:relative;padding:0"><div class="hdr" style="padding:12px 16px;border-bottom:1px solid var(--surface-2)"><div style="font-weight:700;font-size:13.5px">Net worth <span style="font-weight:400;color:var(--ink-600)">· At the end of each step in the period.</span></div><div style="text-align:right"><div style="font-size:17px;font-weight:700">₹'+C(8174822)+'</div><div class="up" style="font-size:11px;font-weight:700">▲ ₹14,83,616</div></div></div>'+
    '<svg viewBox="0 0 640 250" style="padding:6px 10px 0">'+[85,80,75,70,65,60].map(function(v){return '<line x1="56" x2="620" y1="'+y(v)+'" y2="'+y(v)+'" stroke="#e8e5de"/><text x="8" y="'+(y(v)+3)+'">₹'+v+'L</text>'}).join('')+
    ['31 Jan','31 Mar','31 May','31 Jul','30 Sep'].map(function(l,i){return '<text x="'+(x(i*2)-14)+'" y="244">'+l+'</text>'}).join('')+
    '<path class="ar" d="'+sm+' L'+x(9)+' '+y(60)+' L'+x(0)+' '+y(60)+'Z"/><path class="ln" pathLength="1" d="'+sm+'"/>'+
    pts.map(function(p,i){return '<circle class="pt" cx="'+x(p[0]).toFixed(1)+'" cy="'+y(p[1]).toFixed(1)+'" r="3.6" style="animation-delay:'+(.5+i*.2)+'s"/>'}).join('')+'</svg>'+
    '<div style="font-size:11px;color:var(--ink-600);padding:0 16px 12px">Click a point to see what changed up to it.</div><div class="tip" id="tp-tip" style="right:34px;top:92px">29 Oct · ₹81.7L · up ₹1L this month</div></div>'},
   run:function(ctx){ctx.after(3200,function(){ctx.move($('#tp-page .pt:last-of-type'),0,0)});ctx.after(4300,function(){ctx.click();var t=$('#tp-tip');if(t)t.classList.add('on')})}};
  function smooth(p){var d='M'+p[0][0].toFixed(1)+' '+p[0][1].toFixed(1);for(var i=1;i<p.length;i++){var a=p[i-1],b=p[i],cx=(a[0]+b[0])/2;d+=' C'+cx.toFixed(1)+' '+a[1].toFixed(1)+' '+cx.toFixed(1)+' '+b[1].toFixed(1)+' '+b[0].toFixed(1)+' '+b[1].toFixed(1)}return d}

  S.cal={nav:'calendar',dur:9000,t:'Every day, at a glance',d:'Income, spending and investments by day, with salary days starred and busy days shaded darker.',
   h:function(){var data={1:['+₹45,000',null,3,'1 txn',1],4:['−₹4,217',null,1,'3 txns'],5:['−₹29,590','₹10,000',2,'6 txns'],7:['−₹6,264',null,1,'1 txn'],11:['−₹4,251',null,1,'3 txns'],12:['−₹5,733',null,1,'1 txn'],14:['−₹35,000',null,1,'1 txn'],18:['−₹4,519',null,1,'3 txns'],19:['₹1.5L',null,3,'1 txn'],20:['+₹10,000',null,1,'1 txn',1],25:['−₹4,633',null,1,'3 txns'],28:['+₹1L',null,2,'2 txns',1],29:['−₹4,250',null,0,'2 txns']},cells='',n=0;
    ['SUN','MON','TUE','WED','THU','FRI','SAT'].forEach(function(w){cells+='<div class="dw">'+w+'</div>'});
    for(var i=0;i<4;i++)cells+='<div></div>';
    for(var day=1;day<=31;day++){var q=data[day],neg=q&&q[0].charAt(0)=='−',inc=q&&q[0].charAt(0)=='+';
      cells+='<div class="cd'+(q&&q[2]?' h'+q[2]:'')+(day==29?' is':'')+'" style="animation-delay:'+(.2+day*.035)+'s"><span>'+(day==29?'<span class="today">29</span>':day)+(q&&q[4]?'<span class="star">★</span>':'')+'</span>'+(q?'<div class="a"><span class="'+(neg?'dn':(inc?'up':'bl'))+'">'+q[0]+(q[1]?'<br><span class="bl">'+q[1]+'</span>':'')+'</span><small>'+q[3]+'</small></div>':'')+'</div>'}
    return '<div style="display:flex;justify-content:space-between;align-items:center"><div class="pgt" style="font-size:24px">‹ October 2026 ›</div><span class="fld" style="min-width:220px;color:var(--ink-600);font-size:12px">⌕ Search transactions…</span></div><div class="lg"><span><i style="background:#0F7657"></i>Income</span><span><i style="background:#C23E27"></i>Expense</span><span><i style="background:#185FA5"></i>Investments</span><span><i style="background:#555"></i>Pending</span><span class="star">★ Salary day</span></div><div class="cal">'+cells+'</div>'}};

  S.loan={nav:'loans',dur:9000,t:'Loans with the full picture',d:'See how much is principal, how much is interest and how much is left, for every loan you carry.',
   h:function(){return '<div style="display:flex;justify-content:space-between;align-items:flex-start"><div><div class="pgt">Loans &amp; Liabilities</div><div class="pgs">Track your borrowings and repayment progress.</div></div><div class="stat"><small>Total Remaining Debt</small><b class="dn">₹'+C(64021)+'</b></div></div>'+
    '<div style="display:flex;justify-content:space-between;align-items:center"><div class="pl"><span class="on">Active 1</span><span>Paid Off 1</span><span>All Loans 2</span></div><span class="btn-g">+ New Loan</span></div>'+
    '<div class="card" style="margin-top:14px;padding:16px 20px"><div class="ttl" style="font-size:15px;margin-bottom:0">Portfolio Debt Breakdown</div><div class="pgs" style="margin-bottom:6px">Distribution of Principal Paid vs. Interest Paid vs. Debt Remaining</div><div class="lb"><div style="position:relative">'+donut([[46.6,'#1D9E75'],[10.6,'#F59E0B'],[42.8,'#EF4444']])+'<div style="position:absolute;inset:0;display:grid;place-content:center;text-align:center;font-size:9px;color:var(--ink-600)"><b style="font-size:20px;color:var(--ink-900)">'+C(46.6,1)+'%</b>PAID OFF</div></div>'+
    '<div><div class="lrow"><div class="t"><span>Principal Paid</span><span>₹55,979</span></div><div class="pbar" style="margin-top:6px"><div style="--w:46.6%"></div></div><small>46.6% of principal</small></div><div class="lrow" style="--c:#F59E0B"><div class="t"><span>Interest Paid</span><span style="color:var(--amber-400)">₹12,820</span></div></div><div class="lrow" style="--c:#EF4444"><div class="t"><span>Remaining Debt</span><span class="dn">₹64,021</span></div><div class="pbar" style="margin-top:6px"><div class="red" style="--w:53.4%"></div></div><small>53.4% remaining</small></div></div></div></div>'}};

  S.goal={nav:'goals',dur:9000,t:'Goals with a finish date',d:'Set a target, move money towards it and see the date you will actually reach it at your real saving pace.',
   h:function(){function g(ic,n,t,p,s,tot,est,c,dly){return '<div class="card gl"><div class="h"><i>'+ic+'</i><div><b>'+n+'</b><span>▦ Target: '+t+'</span></div></div><div class="row"><div><span>PROGRESS</span><div class="pc" style="color:'+c+'">'+C(p,1)+'%</div></div><div style="text-align:right"><span>SAVED</span><div><b>₹'+s+'</b> / ₹'+tot+'</div></div></div><div class="pbar"><div class="'+(c=='var(--teal-400)'?'':'blu')+'" style="--w:'+p+'%;animation-delay:'+dly+'s"></div></div><div style="font-size:11px;color:var(--ink-600);margin:10px 0">▢ Est. completion: '+est+'</div><div style="border-top:1px solid var(--surface-2);padding-top:12px"><span class="btn-g">👁 View Goal</span></div></div>'}
    return '<div style="display:flex;justify-content:space-between;align-items:flex-start"><div><div class="pgt">Savings Goals</div><div class="pgs">Track your progress towards your financial dreams.</div></div><div class="stat"><small>Total Saved</small><b class="up">₹'+C(75000)+'</b></div></div>'+
     '<div style="display:flex;justify-content:space-between;align-items:center;margin-top:14px"><b style="font-size:15px">Your Active Goals</b><span class="btn-g">+ New Goal</span></div><div class="banner"><b>How We Track Savings</b>Savings contributions are deducted directly from your selected account balance, so your available funds always stay accurate.</div>'+
     '<div class="k4" style="margin-top:14px;max-width:660px">'+g('🛡','Emergency Fund','Sep 13, 2027',60,'60,000','1,00,000','Dec 2026','var(--teal-400)',.4)+g('📱','New iPad Pro','Jan 16, 2028',18.8,'15,000','80,000','Aug 2027','var(--blue-400)',.6)+'</div>'}};

  var ORDER=['dash','add','bud','nw','cal','loan','goal'],LABEL={dash:'Dashboard',add:'Quick add',bud:'Budgets',nw:'Net worth',cal:'Calendar',loan:'Loans',goal:'Goals'};

  /* ---- phone scenes ---- */
  var P={};
  function kpi(l,v,cls,dl,pill,pc,sp,c){return '<div class="card kpi"><div class="lbl">'+l+'</div><div class="val '+cls+'">'+v+'</div><div class="dl '+cls+'">'+dl+' <i style="background:'+pc+'">'+pill+'</i></div>'+spark(sp,c)+'</div>'}
  function insI(i,t,b,d){return '<div class="ins-i" style="animation-delay:'+d+'s"><div class="t">'+i+'</div><div><b>'+t+'</b><span>'+b+'</span></div></div>'}
  P.dash={nav:'home',dur:12500,t:'Your month at a glance',d:'Net worth, income, spending and savings rate on one screen, with calm insights beneath.',
   h:function(){return '<div class="ph-h"><span class="mo">‹ &nbsp;Oct 2026&nbsp; ›</span><span class="selb">▦ This month ⌄</span></div>'+
    '<div class="k4">'+kpi('Net Worth →','₹'+C(81.7,1)+'L','','▲ ₹1L','+1.2%','var(--teal-50)',[5,6,5.5,6,5.8,6,6.4,9],'#1D9E75')+kpi('Income','₹'+C(1.6,1)+'L','up','▲ ₹1L','+187%','var(--teal-50)',[2,2,2,2.1,2,2,2.2,9],'#1D9E75')+kpi('Spent','₹'+C(57670),'dn','▲ ₹6,922','+13.6%','var(--coral-50)',[3,3,4,8,4,3,3,3,3.4,3],'#C23E27')+kpi('Invested','₹'+C(1.6,1)+'L','bl','▲ ₹1.5L','10x+','var(--blue-50)',[2,2,2,2,2,2,2.2,8],'#185FA5')+'</div>'+
    '<div class="card"><div class="ttl">Smart Insights (October)</div>'+insI('🏆','You crossed 50L in net worth!','That\'s years of discipline showing up as a number.',.6)+insI('✨','High five! You\'re saving more than usual this month.','Keep this momentum and you could save 12.1L this year.',1.4)+insI('🍽','You spent 25% on Dining Out this month.','Cut 20% to save ₹2,943.',2.2)+'</div>'+
    '<div class="card"><div class="ttl">Spending Pace <span class="badge r">Over budget</span></div><div class="val">₹'+C(1989)+'<small style="font-size:12px;font-weight:500;color:var(--ink-600)"> /day</small></div><div class="dl dn">Burn rate ↑ 18% vs last month</div><div class="pbar" style="margin-top:10px"><div style="--w:80%;animation-delay:3.4s"></div></div><div class="mini"><div><small>Projected total spend</small><b>₹61,648</b></div><div><small>Projected under budget</small><b class="dn">₹10,352</b></div></div></div>'+
    '<div class="card"><div class="ttl">Asset Allocation</div>'+donut([[79,'#9a5b3c'],[11.6,'#185FA5'],[5.7,'#0F7657'],[3.7,'#5DCAA5']])+'<div class="legend"><span><i style="background:#9a5b3c"></i>Physical 79%</span><span><i style="background:#185FA5"></i>Invest 11.6%</span><span><i style="background:#0F7657"></i>Cash 5.7%</span></div></div>'+
    '<div class="card"><div class="ttl">Financial Health <span class="badge g">Excellent</span></div><div class="val" style="font-size:30px">'+C(63.6,1)+'<small style="font-size:15px">%</small></div><div class="lbl">Savings rate</div><div class="pbar" style="margin-top:10px"><div style="--w:63.6%"></div></div></div>'},
   run:function(ctx){ctx.after(3200,function(){ctx.scroll(330)});ctx.after(6600,function(){ctx.scroll(760)});ctx.after(10200,function(){ctx.scroll(0)})}};

  P.add={nav:'fab',dur:14000,t:'Type it. It\'s logged.',d:'Write what you spent the way you would say it. Amount, category and account are filled in, and it lands in your ledger.',
   h:function(){return '<div class="ph-t">Add a new expense</div><div class="pgs" style="margin:2px 0 12px">Type it, say it, or tap one of your usuals.</div>'+
    '<div class="card"><div style="font-weight:700;margin-bottom:8px">What did you spend on?</div><div class="inp" id="tp-inp"><span style="color:var(--teal-400)">✎</span><span class="tx" id="tp-tx"><span style="color:var(--ink-400)">e.g. swiggy 320</span></span><span class="mic">🎙</span><span class="go" id="tp-pgo">→</span></div>'+
    '<div style="font-weight:700;margin:14px 0 4px">⚡ Your usual</div><div class="usual"><span>🏠 2BHK Rent in Pune <b>₹18,000</b></span><span>🛒 Zepto/Blinkit Orders <b>₹1,763</b></span><span>🍽 Weekend Swiggy/Dining <b>₹2,158</b></span></div>'+
    '<div class="form" id="tp-form"><div class="frow"><div class="fld"><small>Amount</small><b>₹320</b></div><div class="fld"><small>Description</small><b>Swiggy</b></div><div class="fld"><small>Category</small><span class="chipc">🍽 Dining Out</span></div><div class="fld"><small>Account</small><b>Cash Wallet</b></div></div><div style="text-align:center"><span class="btn-g" id="tp-addb" style="width:100%;justify-content:center;padding:11px">+ Add expense</span></div></div></div>'+
    ''},
   run:function(ctx){
     var txt='swiggy 320',el=$('#tp-tx');
     ctx.after(900,function(){ctx.move($('#tp-inp'),-60,0)});
     ctx.after(1700,function(){ctx.click();var k=0;(function st(){if(!ctx.live())return;el.innerHTML=txt.slice(0,k++)+'<span class="caret"></span>';if(k<=txt.length)setTimeout(st,95)})()});
     ctx.after(3000,function(){ctx.move($('#tp-pgo'),0,0)});
     ctx.after(3900,function(){ctx.click();$('#tp-form').classList.add('on');ctx.scroll(150)});
     ctx.after(6000,function(){ctx.move($('#tp-addb'),0,0)});
     ctx.after(7000,function(){ctx.click();ctx.toast('✓ Added ₹320 · Dining Out')});
     ctx.after(8400,function(){ctx.nav('tx',function(){ctx.render(ptx())})});
   }};
  function ptx(){function r(i,t,a,am,d,n,cl){return '<div class="txc'+(n?' nrw':'')+'" style="animation-delay:'+d+'s"><div class="ti">'+i+'</div><div class="tm"><b>'+t+'</b><span>'+a+'</span></div><div class="ta '+cl+'">'+am+'</div></div>'}
    return '<div class="ph-h"><span class="ph-t">All Transactions</span><span class="btn-g">+ Add ▾</span></div><div class="stt"><div><small>Income</small><b class="up">₹1.6L</b></div><div><small>Expenses</small><b class="dn">₹54,830</b></div><div><small>Transfers</small><b class="bl">₹2.1L</b></div></div><div class="srch">⌕ Search transactions…</div>'+
     '<div class="dg"><span>29 Oct, 2026</span><b>− ₹4,570</b></div>'+r('🍽','Swiggy','Cash Wallet','− ₹320',.15,1,'dn')+r('🚗','Uber to Baner','Cash Wallet','− ₹450',.3,0,'dn')+r('🍽','Swiggy Dinner Party','Cash Wallet','− ₹3,800',.45,0,'dn')+
     '<div class="dg"><span>28 Oct, 2026</span><b class="up">+ ₹1L</b></div>'+r('⊕','Q2 Performance Bonus','HDFC Bank (Main)','+ ₹1L',.6,0,'up')+r('🍽','Lunch at Blue Frog','Cash Wallet','− ₹2,200',.75,0,'dn')}

  P.bud={nav:'menu',dur:11000,t:'Know what is safe to spend today',d:'A daily number based on your budget, your pace and the days left in the month. Overspent categories surface first.',
   h:function(){return '<div class="ph-h"><span class="ph-t">Budget</span><span class="btn-g">+ New</span></div>'+
    '<div class="card"><div class="eyb">Safe to spend today</div><div><span class="big">₹'+C(6880)+'</span> <span style="color:var(--ink-600);font-size:12px">per day · 3 days to go</span></div><div style="font-size:12.5px;margin:6px 0">You\'re <b class="up">₹15,995 under</b> expected pace for day 29. ₹20,640 of your ₹72,000 budget is still free.</div>'+
    '<div class="pace"><div class="pbar"><div style="--w:71.3%"></div></div><i style="left:93.5%"></i><small><span>₹51,360 spent · 71.3%</span><span>₹72,000</span></small></div><div style="color:var(--amber-400);font-weight:700;font-size:11px;margin-top:4px">Expected pace · ₹67,355</div></div>'+
    '<div class="chips"><span>🔴 over budget<em>2</em></span><span>⚠ at limit<em>1</em></span><span>✅ on track<em>7</em></span><span>⊖ no limit set<em>3</em></span></div>'+
    '<div style="font-weight:700;margin:12px 0 8px;font-size:15px">Needs attention</div>'+
    [['D','Dining Out','287% used','red','Over by ₹9,360','r'],['G','Groceries','124% used','red','Over by ₹1,442','r'],['R','Rent','100% used','amb','Limit reached · ₹0 left','a']].map(function(b,i){return '<div class="card bc"><div class="h"><span class="l"><i>'+b[0]+'</i>'+b[1]+'</span><span class="badge '+b[5]+'">'+b[2]+'</span></div><div class="pbar"><div class="'+b[3]+'" style="--w:100%;animation-delay:'+(.6+i*.25)+'s"></div></div><div class="f"><span>'+b[4]+'</span><a>Adjust limit</a></div></div>'}).join('')},
   run:function(ctx){ctx.after(3500,function(){ctx.scroll(260)})}};

  P.nw={nav:'menu',dur:10000,t:'Watch your net worth grow',d:'One real-time number across savings, SIPs, loans and cards, shown as a trajectory you can tap into.',
   h:function(){var pts=[[0,67],[1,64],[2,82],[3,81.7],[4,80.4],[5,80.3],[6,79.4],[7,79.2],[8,82.6],[9,81.8]],x=function(i){return 46+i*(272/9)},y=function(v){return 14+(85-v)/25*210},sm=smooth(pts.map(function(p){return[x(p[0]),y(p[1])]}));
    return '<div class="ph-h"><span class="ph-t">Analytics</span><span class="btn-g" style="padding:8px 11px">✦ 2026 Wrapped</span></div><div class="seg"><span>Spending</span><span>Cashflow</span><span class="on">Net worth</span></div><div style="margin:12px 0 4px;display:flex;gap:8px"><span class="selb">▦ This year ⌄</span><span class="selb" style="border-style:dashed;color:var(--ink-600)">+ Filter</span></div><div class="pgs">1 Jan – 29 Oct 2026</div>'+
    '<div class="card chart" style="position:relative;padding:0;margin-top:10px"><div class="hdr" style="padding:12px 14px;border-bottom:1px solid var(--surface-2)"><div style="font-weight:700;font-size:13.5px">Net worth</div><div style="text-align:right"><div style="font-size:16px;font-weight:700">₹'+C(8174822)+'</div><div class="up" style="font-size:11px;font-weight:700">▲ ₹14,83,616</div></div></div>'+
    '<svg viewBox="0 0 330 262" style="padding:4px 4px 0">'+[85,80,75,70,65,60].map(function(v){return '<line x1="46" x2="324" y1="'+y(v)+'" y2="'+y(v)+'" stroke="#e8e5de"/><text x="6" y="'+(y(v)+3)+'">₹'+v+'L</text>'}).join('')+['31 Jan','31 Mar','31 May','31 Jul','30 Sep'].map(function(l,i){return '<text x="'+(x(i*2)-14)+'" y="256">'+l+'</text>'}).join('')+
    '<path class="ar" d="'+sm+' L'+x(9)+' '+y(60)+' L'+x(0)+' '+y(60)+'Z"/><path class="ln" pathLength="1" d="'+sm+'"/>'+pts.map(function(p,i){return '<circle class="pt" cx="'+x(p[0]).toFixed(1)+'" cy="'+y(p[1]).toFixed(1)+'" r="3.6" style="animation-delay:'+(.5+i*.2)+'s"/>'}).join('')+'</svg>'+
    '<div style="font-size:11px;color:var(--ink-600);padding:0 14px 12px">Tap a point to see what changed up to it.</div><div class="tip" id="tp-tip" style="right:20px;top:120px">29 Oct · ₹81.7L · up ₹1L</div></div>'},
   run:function(ctx){ctx.after(3300,function(){ctx.move($('#tp-page .pt:last-of-type'),0,0)});ctx.after(4300,function(){ctx.click();var t=$('#tp-tip');if(t)t.classList.add('on')})}};

  P.cal={nav:'menu',dur:10000,t:'Every day, at a glance',d:'Income, spending and investments by day, with salary days starred and busy days shaded darker.',
   h:function(){var D={1:[['#0F7657'],2,1],4:[['#C23E27'],1],5:[['#C23E27','#185FA5'],2],7:[['#C23E27'],1],11:[['#C23E27'],1],12:[['#C23E27'],1],14:[['#C23E27'],1],18:[['#C23E27'],1],19:[['#185FA5'],3],20:[['#0F7657'],1,1],25:[['#C23E27'],1],28:[['#0F7657','#C23E27'],2,1],29:[['#C23E27'],1]},cells='';
    ['SUN','MON','TUE','WED','THU','FRI','SAT'].forEach(function(w){cells+='<div class="dw">'+w+'</div>'});for(var i=0;i<4;i++)cells+='<div></div>';
    for(var d=1;d<=31;d++){var q=D[d];cells+='<div class="pc'+(q?' h'+q[1]:'')+(d==29?' is':'')+'" style="animation-delay:'+(.15+d*.03)+'s">'+(q&&q[2]?'<span class="st">★</span>':'')+(d==29?'<span class="tdy">29</span>':'<span>'+d+'</span>')+'<span class="ds">'+(q?q[0].map(function(c){return '<i style="background:'+c+'"></i>'}).join(''):'')+'</span></div>'}
    return '<div style="text-align:center;margin:2px 0 8px"><span class="mo">‹ &nbsp;October 2026&nbsp; ›</span></div><div class="srch" style="margin:6px 0">⌕ Search transactions…</div><div class="lg"><span><i style="background:#0F7657"></i>Income</span><span><i style="background:#C23E27"></i>Expense</span><span><i style="background:#185FA5"></i>Investments</span><span class="star">★ Salary day</span></div><div class="pcal">'+cells+'</div>'+
     '<div class="card" style="margin-top:14px;opacity:0;animation:in .5s 1.9s forwards"><div style="display:flex;justify-content:space-between;align-items:center"><b style="font-size:15px">Thu, 29 Oct <span class="badge" style="background:var(--ink-900);color:#fff">Today</span></b></div><div style="margin:4px 0 8px;font-size:13px">Out <b class="dn">₹4,250</b></div><div class="eyb">Transactions</div><div class="row"><span>Swiggy Dinner Party<br><small style="color:var(--ink-600)">Dining Out · Cash Wallet</small></span><b class="dn">−₹3,800</b></div><div class="row"><span>Uber to Baner<br><small style="color:var(--ink-600)">Transport · Cash Wallet</small></span><b class="dn">−₹450</b></div></div>'},
   run:function(ctx){ctx.after(3400,function(){ctx.scroll(250)})}};

  P.loan={nav:'menu',dur:10000,t:'Loans with the full picture',d:'See how much is principal, how much is interest and how much is left, for every loan you carry.',
   h:function(){return '<div class="ph-h"><span class="ph-t" style="font-size:22px">Loans &amp; Liabilities</span><span class="btn-g">+ Add</span></div><div class="pgs"><b class="up">1</b> active loans · <b class="dn">₹64,021</b> remaining debt</div><div class="pl"><span class="on">Active 1</span><span>Paid Off 1</span><span>All Loans 2</span></div>'+
    '<div class="card" style="margin-top:14px;padding:16px"><div class="ttl" style="font-size:15px;margin-bottom:0">Portfolio Debt Breakdown</div><div class="pgs" style="margin-bottom:8px">Distribution of Principal Paid vs. Interest Paid vs. Debt Remaining</div><div style="position:relative;max-width:190px;margin:6px auto 12px">'+donut([[46.6,'#1D9E75'],[10.6,'#F59E0B'],[42.8,'#EF4444']])+'<div style="position:absolute;inset:0;display:grid;place-content:center;text-align:center;font-size:9px;color:var(--ink-600)"><b style="font-size:22px;color:var(--ink-900)">'+C(46.6,1)+'%</b>PAID OFF</div></div>'+
    '<div class="lrow"><div class="t"><span>Principal Paid</span><span class="up">₹55,979</span></div><div class="pbar" style="margin-top:6px"><div style="--w:46.6%"></div></div><small>46.6% of principal</small></div><div class="lrow" style="--c:#F59E0B"><div class="t"><span>Interest Paid</span><span style="color:var(--amber-400)">₹12,820</span></div></div><div class="lrow" style="--c:#EF4444"><div class="t"><span>Remaining Debt</span><span class="dn">₹64,021</span></div><div class="pbar" style="margin-top:6px"><div class="red" style="--w:53.4%"></div></div><small>53.4% remaining</small></div></div>'},
   run:function(ctx){ctx.after(3600,function(){ctx.scroll(120)})}};

  P.goal={nav:'menu',dur:10000,t:'Goals with a finish date',d:'Set a target, move money towards it and see the date you will actually reach it at your real saving pace.',
   h:function(){function g(ic,n,t,p,s,tot,est,c,dly){return '<div class="card gl"><div class="h"><i>'+ic+'</i><div><b>'+n+'</b><span>▦ Target: '+t+'</span></div></div><div class="row" style="border:0"><div><span>PROGRESS</span><div class="pc2" style="font-size:22px;font-weight:700;color:'+c+'">'+C(p,1)+'%</div></div><div style="text-align:right"><span>SAVED</span><div><b>₹'+s+'</b> / ₹'+tot+'</div></div></div><div class="pbar"><div class="'+(c=='var(--teal-400)'?'':'blu')+'" style="--w:'+p+'%;animation-delay:'+dly+'s"></div></div><div style="font-size:11px;color:var(--ink-600);margin:10px 0">▢ Est. completion: '+est+'</div><div style="border-top:1px solid var(--surface-2);padding-top:12px"><span class="btn-g">👁 View Goal</span></div></div>'}
    return '<div class="ph-t">Savings Goals</div><div class="pgs">Track your progress towards your financial dreams.</div><div class="stat"><small>Total Saved</small><b class="up">₹'+C(75000)+'</b></div><div style="display:flex;justify-content:space-between;align-items:center;margin:4px 0 10px"><b style="font-size:16px">Your Active Goals</b><span class="btn-g">+ New Goal</span></div><div class="banner" style="margin-bottom:12px"><b>How We Track Savings</b>Savings contributions are deducted directly from your selected account balance.</div>'+g('🛡','Emergency Fund','Sep 13, 2027',60,'60,000','1,00,000','Dec 2026','var(--teal-400)',.5)+g('📱','New iPad Pro','Jan 16, 2028',18.8,'15,000','80,000','Aug 2027','var(--blue-400)',.8)},
   run:function(ctx){ctx.after(3400,function(){ctx.scroll(330)})}};

  var SETS={desk:S,phone:P},ORDER=['dash','add','bud','nw','cal','loan','goal'],LABEL={dash:'Dashboard',add:'Quick add',bud:'Budgets',nw:'Net worth',cal:'Calendar',loan:'Loans',goal:'Goals'};
  /* the words come from the page (translated there); the scene text above is only the fallback */
  [].forEach.call(document.querySelectorAll('#tp-captions [data-k]'),function(el){var k=el.getAttribute('data-k');
    if(LABEL[k]===undefined)return;LABEL[k]=el.getAttribute('data-l')||LABEL[k];
    [S,P].forEach(function(set){if(set[k]){set[k].t=el.getAttribute('data-t')||set[k].t;set[k].d=el.getAttribute('data-d')||set[k].d}})});
  var PNAV=[['home','dashboard'],['tx','transactions'],['acc','accounts'],['menu','flows']];

  /* ---- player ---- */
  var sim=$('#tp-sim'),vp=$('#tp-vp'),frame=$('#tp-frame'),wrap=$('#tp-stage'),box=$('#tp-box'),pills=$('#tp-pills'),cap=$('#tp-cap'),cursor,tok=0,cur=-1,hover=false,userPaused=false,inView=true,mode='desk',CW=1200,CH=700,scale=1,timers=[],userPick=false;
  function buildChrome(){
    if(mode==='phone'){
      sim.innerHTML='<div class="isl"></div><div class="ph-status"><span>9:41</span><span>●●● ▮</span></div><div class="ph-scroll"><div id="tp-page"></div></div><div class="ph-fade"></div>'+
       '<div class="ph-nav">'+PNAV.map(function(n){return '<div class="pn" data-k="'+n[0]+'"><svg viewBox="0 0 24 24">'+IC[n[1]]+'</svg>'+(n[0]==='menu'?'<i></i>':'')+'</div>'}).join('')+'</div><div class="fab" data-k="fab">+</div>'+
       '<div class="cursor" id="tp-cursor"></div>';
    }else{
      var nav='';NAV.forEach(function(s){nav+='<div class="sec">'+s[0]+'</div>';s[1].forEach(function(i){nav+='<div class="ni" data-k="'+i[0]+'"><svg viewBox="0 0 24 24">'+IC[i[0]]+'</svg>'+i[1]+(i[2]?'<b class="'+i[2]+'">'+i[3]+'</b>':'')+'</div>'})});
      sim.innerHTML='<div class="sb"><div class="brand"><svg viewBox="0 0 32 32" fill="none"><rect width="32" height="32" rx="6" fill="#1D9E75"/><path d="M6 23L12 17L16 21L26 9" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/><circle cx="26" cy="9" r="2.5" fill="#fff"/></svg>TrackMyRupee</div><div class="find"><span>⌕ Find a page</span><kbd>Ctrl K</kbd></div>'+nav+'</div>'+
       '<div class="mn"><div class="hd"><span>Good morning, <strong>Riya</strong></span><span class="r"><span class="add"><i>+</i>Add ▾</span><span>🔔</span></span></div><div class="pg-panel"><div id="tp-page"></div></div></div>'+
       '<svg class="cursor" id="tp-cursor" viewBox="0 0 22 22"><path d="M3 2l14 7-6 2-2 6z" fill="#1a1a19" stroke="#fff" stroke-width="1.4" stroke-linejoin="round"/></svg>';
    }
    cursor=$('#tp-cursor');
  }
  function fit(){
    vp.style.height='';
    var w=vp.clientWidth;
    if(mode==='phone'){CW=390;CH=800}else{CW=1200;CH=700}
    scale=w/CW;sim.style.width=CW+'px';sim.style.height=CH+'px';sim.style.transform='scale('+scale+')';sim.classList.toggle('tp-phone',mode==='phone');vp.style.height=(CH*scale)+'px';
  }
  function pos(el,dx,dy){var r=el.getBoundingClientRect(),s=sim.getBoundingClientRect();return[(r.left-s.left)/scale+r.width/2+(dx||0),(r.top-s.top)/scale+r.height/2+(dy||0)]}
  function ctxFor(my){
    return {live:function(){return my===tok},
     after:function(ms,fn){if(reduce)return;var t=setTimeout(function(){if(my===tok)fn()},ms);timers.push(t)},
     move:function(el,dx,dy){if(!el)return;var p=pos(el,dx,dy);cursor.classList.add('on');cursor.style.transform='translate('+p[0]+'px,'+p[1]+'px)'},
     click:function(){cursor.classList.remove('click');void cursor.getBoundingClientRect();cursor.classList.add('click')},
     scroll:function(y){var s=$('#tp-sc');if(!s)return;s.style.transition='transform 2.4s cubic-bezier(.4,0,.2,1)';s.style.transform='translateY('+(-y)+'px)'},
     toast:function(m){var t=document.createElement('div');t.className='toast on';t.textContent=m;t.style.cssText='position:absolute;left:50%;right:auto;bottom:96px;transform:translateX(-50%);white-space:nowrap;z-index:9';$('.ph-scroll').appendChild(t)},
     render:function(h){var pg=$('#tp-page');pg.style.animation='none';void pg.offsetWidth;pg.style.animation='';pg.innerHTML=mode==='phone'?'<div class="sc" id="tp-sc">'+h+'</div>':h;runCounts()},
     nav:function(k,cb){var ctx=this,el=sim.querySelector((mode==='phone'?'.pn[data-k="':'.ni[data-k="')+k+'"]')||sim.querySelector('.fab[data-k="'+k+'"]');
       if(!el){setActive(k);cb&&cb();return}
       ctx.move(el,mode==='phone'?0:-20,0);ctx.after(950,function(){ctx.click();setActive(k);cb&&cb()})}};
  }
  function setActive(k){if(k==='fab')return;[].forEach.call(sim.querySelectorAll('.ni,.pn'),function(n){n.classList.toggle('on',n.getAttribute('data-k')===k)})}
  function count(el){var to=+el.getAttribute('data-count'),dec=+el.getAttribute('data-dec')||0,t0=null,d=reduce?1:1300;
    function f(ts){if(!t0)t0=ts;var p=Math.min(1,(ts-t0)/d),e=1-Math.pow(1-p,3);el.textContent=(to*e).toLocaleString('en-IN',{minimumFractionDigits:dec,maximumFractionDigits:dec});if(p<1)requestAnimationFrame(f)}
    requestAnimationFrame(f)}
  function runCounts(){[].forEach.call(sim.querySelectorAll('[data-count]'),count)}

  function show(i){
    var my=++tok;timers.forEach(clearTimeout);timers=[];cur=i;var key=ORDER[i],sc=SETS[mode][key],ctx=ctxFor(my);
    [].forEach.call(pills.children,function(p,j){p.setAttribute('aria-selected',j===i?'true':'false');var g=p.querySelector('.tp-pg');g.style.animation='none';void g.offsetWidth;g.style.animation='';if(j===i)p.style.setProperty('--dur',(sc.dur/1000)+'s')});
    cap.innerHTML='<div class="tp-in"><h3>'+sc.t+'</h3><p>'+sc.d+'</p></div>';
    var first=!sim.querySelector('.ni.on,.pn.on');
    if(first||reduce){setActive(sc.nav);ctx.render(sc.h());sc.run&&sc.run(ctx)}
    else{ctx.nav(sc.nav,function(){ctx.render(sc.h());sc.run&&sc.run(ctx)})}
    if(!reduce){var tick=function(){if(my!==tok)return;if(hover||userPaused||!inView||document.hidden){timers.push(setTimeout(tick,800));return}show((cur+1)%ORDER.length)};timers.push(setTimeout(tick,sc.dur))}
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
    if(window.ResizeObserver){new ResizeObserver(function(){var n=box.clientWidth<860;if(n&&mode==='desk')setMode('phone',false);else if(!n&&mode==='phone'&&!userPick)setMode('desk',false);else fit()}).observe(box)}
    else window.addEventListener('resize',fit);
  });
})();
