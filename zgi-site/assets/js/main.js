/* ============================================================
   ZENITH GLOBAL IMPORTS — interactions
   ------------------------------------------------------------
   >>> EDIT THIS FIRST <<<
   Put your real details in CONFIG below. The WhatsApp number is
   what every button on the site opens. Use full international
   format, digits only (no +, spaces or dashes).
   Example for Kenya 0712 345 678  ->  254712345678
   ============================================================ */
const CONFIG = {
  numbers: {
    james:  "254112142445",   // 0112 142 445
    steven: "254704665141"    // 0704 665 141
  },
  primary: "james",           // who general buttons message — "james" or "steven"
  socials: {
    instagram: "",            // TODO — empty shows "coming soon"
    tiktok:    "",            // TODO
    facebook:  ""             // optional
  }
};

/* ---------- environment ---------- */
const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const touch  = window.matchMedia("(hover: none), (pointer: coarse)").matches;
const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

/* ============================================================
   WHATSAPP LINKS
   ============================================================ */
function wireWhatsApp(){
  $$("[data-wa]").forEach(el => {
    const msg = el.getAttribute("data-wa") || "";
    const who = el.getAttribute("data-wa-to") || CONFIG.primary;
    const num = CONFIG.numbers[who] || CONFIG.numbers[CONFIG.primary];
    el.href = `https://wa.me/${num}?text=${encodeURIComponent(msg)}`;
  });
}

/* ============================================================
   FOOTER (socials + year)
   ============================================================ */
function wireFooter(){
  $("#year").textContent = new Date().getFullYear();
  const wrap = $("#footerSocial"); if(!wrap) return;
  const icons = {
    instagram:'<path d="M12 2.2c3.2 0 3.6 0 4.9.1 1.2.1 1.8.3 2.2.4.6.2 1 .5 1.4.9.4.4.7.8.9 1.4.2.4.4 1 .4 2.2.1 1.3.1 1.7.1 4.9s0 3.6-.1 4.9c-.1 1.2-.3 1.8-.4 2.2-.2.6-.5 1-.9 1.4-.4.4-.8.7-1.4.9-.4.2-1 .4-2.2.4-1.3.1-1.7.1-4.9.1s-3.6 0-4.9-.1c-1.2-.1-1.8-.3-2.2-.4-.6-.2-1-.5-1.4-.9-.4-.4-.7-.8-.9-1.4-.2-.4-.4-1-.4-2.2C2.2 15.6 2.2 15.2 2.2 12s0-3.6.1-4.9c.1-1.2.3-1.8.4-2.2.2-.6.5-1 .9-1.4.4-.4.8-.7 1.4-.9.4-.2 1-.4 2.2-.4C8.4 2.2 8.8 2.2 12 2.2zm0 3.2A6.6 6.6 0 1 0 18.6 12 6.6 6.6 0 0 0 12 5.4zm0 10.9A4.3 4.3 0 1 1 16.3 12 4.3 4.3 0 0 1 12 16.3zm6.8-11.2a1.5 1.5 0 1 1-1.5-1.5 1.5 1.5 0 0 1 1.5 1.5z"/>',
    tiktok:'<path d="M16.6 5.8a4.3 4.3 0 0 1-1-2.8h-3v11.4a2.6 2.6 0 1 1-2.6-2.6 2.7 2.7 0 0 1 .8.1V8.8a5.7 5.7 0 0 0-.8-.1 5.6 5.6 0 1 0 5.6 5.6V8.6a7.2 7.2 0 0 0 4.2 1.3V6.9a4.3 4.3 0 0 1-3.2-1.1z"/>',
    facebook:'<path d="M22 12a10 10 0 1 0-11.6 9.9v-7H7.9V12h2.5V9.8c0-2.5 1.5-3.9 3.8-3.9 1.1 0 2.2.2 2.2.2v2.5h-1.3c-1.2 0-1.6.8-1.6 1.6V12h2.8l-.4 2.9h-2.4v7A10 10 0 0 0 22 12z"/>'
  };
  let any=false;
  Object.entries(CONFIG.socials).forEach(([k,url])=>{
    if(!url) return; any=true;
    const a=document.createElement("a");
    a.href=url;a.target="_blank";a.rel="noopener";a.setAttribute("aria-label",k);
    a.innerHTML=`<svg viewBox="0 0 24 24" aria-hidden="true">${icons[k]||""}</svg>`;
    wrap.appendChild(a);
  });
  if(!any){
    const s=document.createElement("span");
    s.className="footer__soon";s.textContent="Socials — coming soon";
    wrap.appendChild(s);
  }
}

/* ============================================================
   NAV
   ============================================================ */
function wireNav(){
  const nav=$("#nav"), burger=$("#burger"), links=$(".nav__links");
  const onScroll=()=>nav.classList.toggle("scrolled", window.scrollY>30);
  onScroll(); window.addEventListener("scroll",onScroll,{passive:true});
  burger?.addEventListener("click",()=>{
    const open=links.classList.toggle("open");
    burger.setAttribute("aria-expanded",open);
  });
  $$(".nav__links a").forEach(a=>a.addEventListener("click",()=>{
    links.classList.remove("open");burger?.setAttribute("aria-expanded","false");
  }));
}

/* ============================================================
   REVEAL ON SCROLL
   ============================================================ */
function wireReveal(){
  const els=$$(".reveal");
  if(reduce){els.forEach(e=>e.classList.add("in"));return;}
  const io=new IntersectionObserver((ents)=>{
    ents.forEach(e=>{if(e.isIntersecting){e.target.classList.add("in");io.unobserve(e.target);}});
  },{threshold:.14,rootMargin:"0px 0px -8% 0px"});
  els.forEach(e=>io.observe(e));
}

/* ============================================================
   HERO HEADLINE + globe start
   ============================================================ */
function wireHero(){
  requestAnimationFrame(()=>requestAnimationFrame(()=>$("#hero")?.classList.add("ready")));
}

/* ============================================================
   COUNT-UP STATS
   ============================================================ */
function wireCounts(){
  const io=new IntersectionObserver((ents)=>{
    ents.forEach(e=>{
      if(!e.isIntersecting)return; io.unobserve(e.target);
      const el=e.target, end=+el.dataset.count, suf=el.dataset.suffix||"";
      if(reduce){el.textContent=end.toLocaleString()+suf;return;}
      const dur=1500, t0=performance.now();
      const tick=(t)=>{
        const p=Math.min((t-t0)/dur,1), eased=1-Math.pow(1-p,3);
        el.textContent=Math.round(end*eased).toLocaleString()+suf;
        if(p<1)requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
  },{threshold:.5});
  $$(".stat__num").forEach(el=>io.observe(el));
}

/* ============================================================
   CUSTOM CURSOR + MAGNETIC BUTTONS
   ============================================================ */
function wireCursor(){
  if(touch||reduce)return;
  const ring=$("#cursor"), dot=$("#cursorDot");
  document.body.classList.add("has-cursor");
  let rx=innerWidth/2, ry=innerHeight/2, dx=rx, dy=ry;
  addEventListener("mousemove",e=>{dx=e.clientX;dy=e.clientY;dot.style.transform=`translate(${dx}px,${dy}px) translate(-50%,-50%)`;},{passive:true});
  const loop=()=>{rx+=(dx-rx)*.18;ry+=(dy-ry)*.18;ring.style.transform=`translate(${rx}px,${ry}px) translate(-50%,-50%)`;requestAnimationFrame(loop);};
  loop();
  $$("a,button,[data-tilt],summary").forEach(el=>{
    el.addEventListener("mouseenter",()=>ring.classList.add("is-hover"));
    el.addEventListener("mouseleave",()=>ring.classList.remove("is-hover"));
  });
  // magnetic
  $$(".magnetic").forEach(el=>{
    const s=22;
    el.addEventListener("mousemove",e=>{
      const r=el.getBoundingClientRect();
      const mx=(e.clientX-(r.left+r.width/2))/r.width, my=(e.clientY-(r.top+r.height/2))/r.height;
      el.style.transform=`translate(${mx*s}px,${my*s}px)`;
    });
    el.addEventListener("mouseleave",()=>{el.style.transform="";});
  });
}

/* ============================================================
   CARD TILT + SPOTLIGHT
   ============================================================ */
function wireTilt(){
  if(touch||reduce)return;
  $$("[data-tilt]").forEach(card=>{
    const max=7;
    card.addEventListener("mousemove",e=>{
      const r=card.getBoundingClientRect();
      const px=(e.clientX-r.left)/r.width, py=(e.clientY-r.top)/r.height;
      card.style.transform=`perspective(900px) rotateY(${(px-.5)*max*2}deg) rotateX(${(.5-py)*max*2}deg) translateZ(6px)`;
      card.style.setProperty("--mx",`${px*100}%`);
      card.style.setProperty("--my",`${py*100}%`);
    });
    card.addEventListener("mouseleave",()=>{card.style.transform="";});
  });
}

/* ============================================================
   PARTICLE FIELD + shooting stars
   ============================================================ */
function wireParticles(){
  const cv=$("#particles"); if(!cv||reduce)return;
  const ctx=cv.getContext("2d"); let w,h,dpr,parts=[],stars=[],raf,run=true;
  const resize=()=>{dpr=Math.min(devicePixelRatio||1,2);w=cv.width=innerWidth*dpr;h=cv.height=innerHeight*dpr;cv.style.width=innerWidth+"px";cv.style.height=innerHeight+"px";};
  resize();addEventListener("resize",resize);
  const N=touch?34:70;
  for(let i=0;i<N;i++)parts.push({x:Math.random()*w,y:Math.random()*h,r:(Math.random()*1.4+.4)*dpr,vy:-(Math.random()*.25+.05)*dpr,vx:(Math.random()-.5)*.15*dpr,a:Math.random()*.5+.15});
  const spawnStar=()=>{if(!run)return;stars.push({x:Math.random()*w*.8,y:Math.random()*h*.4,len:(Math.random()*120+80)*dpr,v:(Math.random()*6+7)*dpr,life:1});setTimeout(spawnStar,4000+Math.random()*6000);};
  setTimeout(spawnStar,3000);
  const draw=()=>{
    ctx.clearRect(0,0,w,h);
    for(const p of parts){
      p.x+=p.vx;p.y+=p.vy;if(p.y<-10){p.y=h+10;p.x=Math.random()*w;}
      ctx.beginPath();ctx.arc(p.x,p.y,p.r,0,7);ctx.fillStyle=`rgba(201,162,75,${p.a})`;ctx.fill();
    }
    for(let i=stars.length-1;i>=0;i--){
      const s=stars[i];s.x+=s.v;s.y+=s.v*.5;s.life-=.012;
      const g=ctx.createLinearGradient(s.x,s.y,s.x-s.len,s.y-s.len*.5);
      g.addColorStop(0,`rgba(255,244,214,${s.life})`);g.addColorStop(1,"rgba(255,244,214,0)");
      ctx.strokeStyle=g;ctx.lineWidth=1.4*dpr;ctx.beginPath();ctx.moveTo(s.x,s.y);ctx.lineTo(s.x-s.len,s.y-s.len*.5);ctx.stroke();
      if(s.life<=0)stars.splice(i,1);
    }
    raf=requestAnimationFrame(draw);
  };
  draw();
  document.addEventListener("visibilitychange",()=>{run=!document.hidden;if(run){draw();}else cancelAnimationFrame(raf);});
}

/* ============================================================
   HERO ROTATING GLOBE (dot sphere, follows cursor)
   ============================================================ */
function wireGlobe(){
  const cv=$("#heroGlobe"); if(!cv||reduce)return;
  const ctx=cv.getContext("2d"); let w,h,dpr,R,cx,cy,pts=[],rot=0,tx=0,ty=0,mx=0,my=0,vis=true,raf;
  const resize=()=>{dpr=Math.min(devicePixelRatio||1,2);const r=cv.getBoundingClientRect();w=cv.width=r.width*dpr;h=cv.height=r.height*dpr;R=Math.min(w,h)*.34;cx=w/2;cy=h*.5;};
  resize();addEventListener("resize",resize);
  const N=touch?260:520;
  for(let i=0;i<N;i++){const y=1-(i/(N-1))*2,rr=Math.sqrt(1-y*y),th=i*2.399963;pts.push({x:Math.cos(th)*rr,y,z:Math.sin(th)*rr});}
  addEventListener("mousemove",e=>{mx=(e.clientX/innerWidth-.5);my=(e.clientY/innerHeight-.5);},{passive:true});
  const io=new IntersectionObserver(es=>{vis=es[0].isIntersecting;if(vis){raf=requestAnimationFrame(draw);}else cancelAnimationFrame(raf);},{threshold:.02});
  io.observe(cv);
  function draw(){
    if(!vis)return;
    tx+=(my*.5-tx)*.05; ty+=(mx*.6-ty)*.05; rot+=0.0016;
    ctx.clearRect(0,0,w,h);
    const cosR=Math.cos(rot+ty),sinR=Math.sin(rot+ty),cosP=Math.cos(tx),sinP=Math.sin(tx);
    for(const p of pts){
      let x=p.x*cosR - p.z*sinR, z=p.x*sinR + p.z*cosR, y=p.y;
      const y2=y*cosP - z*sinP; z=y*sinP + z*cosP; y=y2;
      const depth=(z+1)/2;
      const sx=cx+x*R, sy=cy+y*R, r=(depth*1.7+.3)*dpr;
      ctx.beginPath();ctx.arc(sx,sy,r,0,7);
      ctx.fillStyle=`rgba(201,162,75,${.12+depth*.55})`;ctx.fill();
    }
    // soft halo
    const g=ctx.createRadialGradient(cx,cy,R*.2,cx,cy,R*1.5);
    g.addColorStop(0,"rgba(201,162,75,.05)");g.addColorStop(1,"rgba(201,162,75,0)");
    ctx.fillStyle=g;ctx.beginPath();ctx.arc(cx,cy,R*1.5,0,7);ctx.fill();
    raf=requestAnimationFrame(draw);
  }
  draw();
}

/* ============================================================
   WORLD MAP — shipping routes
   ============================================================ */
const HUBS=[
  {id:"cn",label:"Shenzhen",flag:"🇨🇳",name:"China — Manufacturing Powerhouse",x:.80,y:.44,cats:["Electronics","Smart Home","Accessories","Manufacturing Partners"]},
  {id:"jp",label:"Tokyo",flag:"🇯🇵",name:"Japan — Premium Technology",x:.88,y:.37,cats:["Premium Technology","Lifestyle Products","Innovation"]},
  {id:"ae",label:"Dubai",flag:"🇦🇪",name:"UAE — Global Crossroads",x:.63,y:.47,cats:["Luxury Goods","Fragrances","Fast Re-export"]},
  {id:"tr",label:"Istanbul",flag:"🇹🇷",name:"Turkey — Style & Textiles",x:.55,y:.35,cats:["Apparel","Home & Textiles","Cosmetics"]},
  {id:"de",label:"Germany",flag:"🇩🇪",name:"Germany — Engineering",x:.49,y:.29,cats:["Industrial Equipment","Tools","Engineering"]},
  {id:"us",label:"USA",flag:"🇺🇸",name:"USA — Brands & Gadgets",x:.17,y:.34,cats:["Gadgets","Fitness Tech","Brand Names"]},
];
const NAIROBI={x:.585,y:.63};

function wireWorldMap(){
  const cv=$("#worldCanvas"), hubsWrap=$("#hubs"); if(!cv)return;
  const ctx=cv.getContext("2d"); let w,h,dpr,box,vis=true,raf,t=0;
  const resize=()=>{const r=cv.getBoundingClientRect();box=r;dpr=Math.min(devicePixelRatio||1,2);w=cv.width=r.width*dpr;h=cv.height=r.height*dpr;};
  resize();addEventListener("resize",resize);
  const P=(p)=>({x:p.x*w,y:p.y*h});

  // build hub buttons + Nairobi
  hubsWrap.innerHTML="";
  const mkHub=(cfg,ke=false)=>{
    const d=document.createElement("div");
    d.className="hub"+(ke?" hub--ke":"");d.style.left=(cfg.x*100)+"%";d.style.top=(cfg.y*100)+"%";
    d.innerHTML=`<button class="hub__dot" data-label="${cfg.label}" aria-label="${cfg.label}"></button>`;
    if(!ke)d.querySelector("button").addEventListener("click",()=>selectHub(cfg,d));
    hubsWrap.appendChild(d);return d;
  };
  HUBS.forEach(hcfg=>mkHub(hcfg));
  mkHub({x:NAIROBI.x,y:NAIROBI.y,label:"Nairobi"},true);

  function selectHub(cfg,node){
    $$(".hub").forEach(n=>n.classList.remove("is-active"));
    node.classList.add("is-active");
    $("#hubFlag").textContent=cfg.flag;$("#hubName").textContent=cfg.name;
    $("#hubList").innerHTML=cfg.cats.map(c=>`<li>${c}</li>`).join("");
    active=cfg.id;
  }
  let active=null;

  function draw(){
    if(!vis)return;
    t+=0.006;ctx.clearRect(0,0,w,h);
    const nai=P(NAIROBI);
    HUBS.forEach((hb,i)=>{
      const a=P(hb), cxp=(a.x+nai.x)/2, cyp=Math.min(a.y,nai.y)-h*.16;
      const isA=active===hb.id;
      // full arc
      ctx.beginPath();ctx.moveTo(a.x,a.y);ctx.quadraticCurveTo(cxp,cyp,nai.x,nai.y);
      ctx.strokeStyle=isA?"rgba(244,222,147,.65)":"rgba(201,162,75,.22)";ctx.lineWidth=(isA?2:1)*dpr;ctx.stroke();
      // travelling pulse
      const tt=((t+i*.5)%1);
      const bx=(1-tt)*(1-tt)*a.x+2*(1-tt)*tt*cxp+tt*tt*nai.x;
      const by=(1-tt)*(1-tt)*a.y+2*(1-tt)*tt*cyp+tt*tt*nai.y;
      const grd=ctx.createRadialGradient(bx,by,0,bx,by,6*dpr);
      grd.addColorStop(0,"rgba(255,244,214,.95)");grd.addColorStop(1,"rgba(255,244,214,0)");
      ctx.fillStyle=grd;ctx.beginPath();ctx.arc(bx,by,6*dpr,0,7);ctx.fill();
    });
    raf=requestAnimationFrame(draw);
  }
  if(!reduce){
    const io=new IntersectionObserver(es=>{vis=es[0].isIntersecting;if(vis)raf=requestAnimationFrame(draw);else cancelAnimationFrame(raf);},{threshold:.05});
    io.observe(cv);
    draw();
  }
}

/* ============================================================
   IMPORT JOURNEY (sequential reveal)
   ============================================================ */
function wireJourney(){
  const steps=$$("#journey .journey__step"); if(!steps.length)return;
  if(reduce){steps.forEach(s=>s.classList.add("on"));return;}
  const io=new IntersectionObserver((ents)=>{
    ents.forEach(e=>{
      if(!e.isIntersecting)return; io.unobserve(e.target);
      steps.forEach((s,i)=>setTimeout(()=>s.classList.add("on"),i*260));
    });
  },{threshold:.4});
  io.observe($("#journey"));
}

/* ============================================================
   PROCESS TIMELINE FILL
   ============================================================ */
function wireTimeline(){
  const tl=$("#timeline"), fill=$("#tlFill"), steps=$$(".tl-step"); if(!tl)return;
  const io=new IntersectionObserver((ents)=>ents.forEach(e=>e.target.classList.toggle("in",e.isIntersecting||e.target.classList.contains("in"))),{threshold:.5});
  steps.forEach(s=>io.observe(s));
  const onScroll=()=>{
    const r=tl.getBoundingClientRect(),vh=innerHeight;
    const prog=Math.min(Math.max((vh*.7-r.top)/(r.height),0),1);
    fill.style.height=(prog*100)+"%";
  };
  onScroll();addEventListener("scroll",onScroll,{passive:true});
}

/* ============================================================
   VIDEO (autoplay in view, scroll scale, sound toggle)
   ============================================================ */
function wireVideo(){
  const frame=$("#videoFrame"), vid=$("#brandVideo"), btn=$("#soundBtn"); if(!vid)return;
  const io=new IntersectionObserver(es=>es.forEach(e=>{e.isIntersecting?vid.play().catch(()=>{}):vid.pause();}),{threshold:.35});
  io.observe(vid);
  btn?.addEventListener("click",()=>{vid.muted=!vid.muted;btn.textContent=vid.muted?"🔊":"🔇";if(!vid.muted)vid.play().catch(()=>{});});
  if(!reduce){
    const onScroll=()=>{
      const r=frame.getBoundingClientRect(),vh=innerHeight;
      const p=Math.min(Math.max((vh-r.top)/(vh+r.height),0),1);
      const s=.92+Math.min(p,.5)*.16;
      frame.style.transform=`scale(${s.toFixed(3)})`;
    };
    onScroll();addEventListener("scroll",onScroll,{passive:true});
  }
}

/* ============================================================
   VOTE (Discovery Lab) — persisted in localStorage
   ============================================================ */
function wireVote(){
  const grid=$("#voteGrid"); if(!grid)return;
  const KEY="zgi_votes_v1";
  let votes=JSON.parse(localStorage.getItem(KEY)||"null")|| {smarthome:47,earbuds:63,projector:38,massager:29};
  const mine=JSON.parse(localStorage.getItem(KEY+"_mine")||"[]");
  const render=()=>{
    const max=Math.max(...Object.values(votes),1);
    $$(".vote",grid).forEach(card=>{
      const id=card.dataset.id, n=votes[id]||0;
      card.querySelector(".vote__bar span").style.width=(n/max*100)+"%";
      card.querySelector(".vote__btn b").textContent=n;
      if(mine.includes(id))card.querySelector(".vote__btn").classList.add("voted");
    });
  };
  render();
  $$(".vote__btn",grid).forEach(btn=>btn.addEventListener("click",()=>{
    const card=btn.closest(".vote"), id=card.dataset.id;
    if(mine.includes(id))return;
    votes[id]=(votes[id]||0)+1; mine.push(id);
    localStorage.setItem(KEY,JSON.stringify(votes));
    localStorage.setItem(KEY+"_mine",JSON.stringify(mine));
    render();
  }));
}

/* ============================================================
   INIT
   ============================================================ */
document.addEventListener("DOMContentLoaded",()=>{
  wireWhatsApp();wireFooter();wireNav();wireReveal();wireHero();wireCounts();
  wireCursor();wireTilt();wireParticles();wireGlobe();wireWorldMap();
  wireJourney();wireTimeline();wireVideo();wireVote();
});
