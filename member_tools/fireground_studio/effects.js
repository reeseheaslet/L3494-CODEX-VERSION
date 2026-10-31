// Fireground original analytic effects. No proprietary textures, CFD claims, or accumulated state.
// All geometry and emission are addressed by emitter seed + absolute scenario time.
(function(root){
 const TAU=Math.PI*2,clamp=(v,a=0,b=1)=>Math.max(a,Math.min(b,v)),mix=(a,b,t)=>a+(b-a)*t,fract=v=>v-Math.floor(v);
 const smooth=v=>{v=clamp(v);return v*v*(3-2*v);};
 function hash(x,y,z=0,s=0){let n=Math.imul(x,374761393)^Math.imul(y,668265263)^Math.imul(z,2147483647)^s;n=Math.imul(n^(n>>>13),1274126177);return ((n^(n>>>16))>>>0)/4294967295;}
 const rnd=n=>hash(n|0,(n*17)|0,0,73123);
 function noise(x,y,s=0,period=0){const ix=Math.floor(x),iy=Math.floor(y),u=smooth(x-ix),v=smooth(y-iy),wrap=n=>period?((n%period)+period)%period:n;return mix(mix(hash(ix,wrap(iy),0,s),hash(ix+1,wrap(iy),0,s),u),mix(hash(ix,wrap(iy+1),0,s),hash(ix+1,wrap(iy+1),0,s),u),v);}
 function noise3(x,y,z,s=0){const iz=Math.floor(z),w=smooth(z-iz),layer=k=>{const ix=Math.floor(x),iy=Math.floor(y),u=smooth(x-ix),v=smooth(y-iy);return mix(mix(hash(ix,iy,k,s),hash(ix+1,iy,k,s),u),mix(hash(ix,iy+1,k,s),hash(ix+1,iy+1,k,s),u),v);};return mix(layer(iz),layer(iz+1),w);}
 // Curl of a scalar noise potential; used as a coherent displacement, not a fluid solver.
 function curl(x,y,t){const d=.06;return [(noise(x,y+d,29)-noise(x,y-d,29))/(2*d),(noise(x-d,y,29)-noise(x+d,y,29))/(2*d)];}
 const fireCache=new Map(),cloudCache=new Map(),FRAMES=32,FW=144,FH=240;
 function remember(cache,key,value,limit){cache.set(key,value);if(cache.size>limit)cache.delete(cache.keys().next().value);return value;}
 function fireTile(kind,frame,warmth=.65){
  frame=((frame%FRAMES)+FRAMES)%FRAMES;const palette=Math.round(clamp(warmth)*5),key=kind+':'+frame+':'+palette;
  if(fireCache.has(key))return fireCache.get(key);
  const c=document.createElement('canvas');c.width=FW;c.height=FH;const h=c.height,x=c.getContext('2d'),data=x.createImageData(FW,h),travel=frame/FRAMES*12;
  for(let j=0;j<h;j++)for(let i=0;i<FW;i++){
   const u=i/(FW-1)*2-1,v=j/(h-1),anchored=kind==='tongue';
   const warp=anchored?(noise(u*2.8+5,v*5+travel,41,12)-.5)*(.26+(1-v)*.3):(noise(u*3+8,v*5+travel,13,12)-.5)*(.70+(1-v)*.36),xx=u+warp;
   const n=anchored?noise(xx*4+9,v*6+travel,123,12)*.5+noise(xx*9+21,v*12+travel*2,333,24)*.32+noise(u*20+47,v*24+travel*4,801,48)*.18:noise(xx*4+7,v*6+travel,47,12)*.52+noise(xx*9+19,v*12+travel*2,173,24)*.31+noise(xx*23+11,v*24+travel*4,643,48)*.17;
   let field;
   if(kind==='jet'){
    const shear=noise(xx*6+Math.sin(v*9+travel)*.6,v*3+travel,347,12),width=.18+v*.43;
    field=.58-Math.abs(xx+Math.sin(v*16-travel)*.12)/width+(n-.44)*2.4+(shear-.5)*1.4-(1-v)*.15;
   }else if(kind==='sheet'){
    const fold=noise(xx*3+Math.sin(v*7+travel)*.7,v*5+travel,57,12),hole=noise(xx*7+6,v*9+travel,701,12);
    // The broad sheet rises into uneven lobes. Its top and shoulders have no common clipping line.
    const ridge=noise(xx*5+travel*.22,travel*.16+4,359,12),tips=noise(xx*14+travel*.32,travel*.25+7,563,12);
    const crown=.035+Math.pow(Math.abs(u),1.5)*.38+(1-ridge)*.22+(1-tips)*.12;
    field=(1-Math.pow(Math.abs(xx),2.4))*(.7+v*.38)+(fold-.45)*1.6-.45-Math.max(0,.48-hole)*2.6;
    field-=smooth((crown-v)/.11)*2.7+smooth((Math.abs(u)-.75)/.21)*1.55;
   }else{
    // Retain the useful connected, rising shape of the original flame while adding finer edges.
    const fuel=Math.pow(v,.8)*1.07-Math.abs(xx)*.94-.11;
    field=fuel+(n-.5)*(.98+(1-v)*.5);
    if(kind==='intense'){
     // A rolling, asymmetric crown keeps a wide flame from exposing its tile edge.
     const crown=.075+Math.pow(Math.abs(u),1.3)*.25+(1-noise(u*5+travel*.18,travel*.13+4,359,12))*.12;
     field-=smooth((crown-v)/.12)*2.2;
    }
   }
   const boundary=smooth(field/.12),vertical=smooth(v/.055)*smooth((1-v)/.035)*smooth((1-Math.abs(u))/.085);
   if(!boundary||!vertical)continue;
   // Thin reaction edges are hot; the connected body retains translucent amber detail.
   const rim=Math.exp(-Math.abs(field-.12)*6),heat=anchored?clamp(field*1.6+v*.16+rim*.07+(palette/5-.5)*.18):clamp((n-.23)*1.45+rim*.18+v*.08+(palette/5-.5)*.25),alpha=(1-Math.exp(-Math.max(0,field)*5.5))*(.85+rim*.15)*vertical;
   const at=(j*FW+i)*4;data.data[at]=Math.round(mix(185,255,smooth(heat/.4)));data.data[at+1]=Math.round(heat<.5?mix(25,145,heat*2):mix(145,245,(heat-.5)*2));data.data[at+2]=Math.round(heat<.6?mix(1,12,heat/.6):mix(12,157,(heat-.6)/.4));data.data[at+3]=Math.round(alpha*255);
  }x.putImageData(data,0,0);return remember(fireCache,key,c,128);
 }
 function cloudTile(color,seed,dark=false){
  const key=color+':'+seed+':'+dark;if(cloudCache.has(key))return cloudCache.get(key);
  const size=128,c=document.createElement('canvas');c.width=c.height=size;const x=c.getContext('2d'),im=x.createImageData(size,size),rgb=(/^#[a-f\d]{6}$/i.test(color)?color:'#44484d').match(/\w\w/g).map(n=>parseInt(n,16));
  for(let y=0;y<size;y++)for(let z=0;z<size;z++){
   const u=(z-63.5)/61,v=(y-63.5)/61,r=Math.hypot(u,v);if(r>1.14)continue;
   const detail=noise(u*5+9,v*5+7,seed+19),edge=noise(u*10+21,v*10+13,seed+41),uu=u+(detail-.5)*.18,vv=v+(edge-.5)*.13;
   let depth=0;for(let k=0;k<7;k++){const zz=-1+k/3,rad=Math.sqrt(uu*uu+vv*vv+zz*zz),n=noise3(uu*3+7,vv*3+9,zz*3+6,seed)*.7+noise3(uu*7+19,vv*7+11,zz*7+14,seed+23)*.3;depth+=clamp((1-rad)*3.8+(n-.5)*1.15)/3;}
   if(depth<=0)continue;
   const optical=depth*(dark?3.6:2.55),alpha=1-Math.exp(-optical),normalLight=clamp(.55-u*.32-v*.37),pockets=(detail-.5)*48+(edge-.5)*23,shade=normalLight*43+pockets-depth*(dark?17:11),at=(y*size+z)*4;
   for(let k=0;k<3;k++)im.data[at+k]=clamp(rgb[k]*(dark?.48:.69)+shade,7,226);im.data[at+3]=Math.round(alpha*255);
  }x.putImageData(im,0,0);return remember(cloudCache,key,c,48);
 }
 function params(e){return {speed:e.speed??1,turb:e.turbulence??1,spread:e.spread??.55,density:e.density??.75,heat:e.heat??.65,embers:e.embers??.12};}
 const blendBuffer=document.createElement('canvas'),blendContext=blendBuffer.getContext('2d');
 function tile(c,kind,phase,x,y,w,h,alpha,heat,layers){const a=Math.floor(phase),f=phase-a,first=fireTile(kind,a,heat);let image=first;
  if(layers>1){if(blendBuffer.width!==first.width||blendBuffer.height!==first.height){blendBuffer.width=first.width;blendBuffer.height=first.height;}blendContext.clearRect(0,0,blendBuffer.width,blendBuffer.height);blendContext.globalCompositeOperation='source-over';blendContext.globalAlpha=1-f;blendContext.drawImage(first,0,0);blendContext.globalCompositeOperation='lighter';blendContext.globalAlpha=f;blendContext.drawImage(fireTile(kind,a+1,heat),0,0);blendContext.globalAlpha=1;blendContext.globalCompositeOperation='source-over';image=blendBuffer;}
  c.save();c.globalCompositeOperation='source-over';c.globalAlpha*=clamp(alpha*1.7,0,1);c.drawImage(image,x,y,w,h);c.restore();}
 function glow(c,ew,eh,strength,heat){c.save();c.globalCompositeOperation='screen';const g=c.createRadialGradient(0,eh*.35,0,0,eh*.35,ew*.8);g.addColorStop(0,`rgba(255,${Math.round(70+heat*60)},12,${strength*.11})`);g.addColorStop(1,'rgba(255,70,0,0)');c.fillStyle=g;c.fillRect(-ew,-eh*.15,ew*2,eh);c.restore();}
 function embers(c,ew,eh,e,t,p,strength,wind,quality){const count=Math.floor((quality.flameLayers>1?12:5)*p.embers);if(!count)return;c.save();c.globalCompositeOperation='screen';for(let i=0;i<count;i++){const a=fract(t*(.4+rnd(e.seed+i)) *p.speed+rnd(e.seed+i*7)),x=(rnd(e.seed+i*3)-.5)*ew*.5+Math.sin(a*7+i)*ew*.1+wind*a*eh*.3,y=eh*.46-a*eh*1.15;c.globalAlpha*=1;c.fillStyle=`rgba(255,190,83,${(1-a)*strength*.7})`;c.fillRect(x,y,Math.max(.8,ew*.008),Math.max(1.2,eh*.012));}c.restore();}
 function connectedFire(c,ew,eh,e,t,strength,growth,wind,q){
  const p=params(e),style=e.style,clock=t*p.speed,source=ew*(.8+p.spread*.42)*(1+(growth-1)*.18),height=eh*strength*growth,base=eh*.48,seed=rnd(e.seed)*31,alpha=clamp(p.density*.93,0,.98);
  glow(c,source,height,strength*p.density,p.heat);
  c.save();c.transform(1,0,-wind*.19,1,wind*height*.11,0);
  if(style==='fast-fire'){
   // Faster upward texture transport on two connected, source-anchored tongues.
   for(let i=0;i<2;i++){const sway=Math.sin(clock*1.2+i+seed)*p.turb*ew*.027,x=(i-.5)*source*.31+sway,h=height*(1.03+rnd(e.seed+i)*.28),w=source*.67;tile(c,'tongue',(clock*1.02+seed+i*.31)*FRAMES,x-w/2,base-h,w,h,alpha*.84,p.heat,q.flameLayers);}
  }else if(style==='intense-fire'){
   // Three connected sources form a broad front without one rectangular sprite crown.
   const surge=.94+.06*Math.sin(clock*.65+seed);
   for(let i=0;i<3;i++){
    const w=source*(i===1?.79:.65),h=height*surge*(i===1?1.09:i===0?.82:.9);
    const x=(i-1)*source*.26-w*.5;
    tile(c,'tongue',(clock*(.41+i*.025)+seed+i*.31)*FRAMES,x,base-h,w,h,alpha*(i===1?.82:.7),p.heat,q.flameLayers);
   }
  }else{
   const breathe=.96+.04*Math.sin(clock*1.2+seed),w=source*breathe;
   tile(c,'tongue',(clock*.53+seed)*FRAMES,-w*.5,base-height,w,height,alpha*.92,p.heat,q.flameLayers);
   if(q.flameLayers>1)tile(c,'tongue',(clock*.57+seed+.38)*FRAMES,-w*.28,base-height*.77,w*.58,height*.77,alpha*.3,p.heat,q.flameLayers);
  }
  c.restore();embers(c,ew,eh,e,t,p,strength,wind,q);
 }
 function plumeParticles(ew,eh,e,t,strength,growth,wind,q,dark=false,wispy=false){
  const p=params(e),n=Math.min(wispy?18:dark?24:22,q.smokeParticles),particles=[],clock=t*p.speed;
  // Keep a complete age distribution even at Low detail. Random independent lifetimes
  // cluster emissions and leave visible holes when only ten billows are drawn.
  const life=wispy?7.4:dark?6.6:7.1,seedPhase=rnd(e.seed)*.47,opacityScale=Math.sqrt(28/n);
  for(let k=0;k<n;k++){
   const i=Math.floor(k*28/n),offset=(k+.5)/n+seedPhase;
   const phase=clock/life+offset,age=fract(phase),cycle=Math.floor(phase);
   const birth=(cycle-offset)*life,spread=(.55+p.spread*.8)*(1+(growth-1)*.2);
   const origin=(rnd(e.seed+i*3+cycle*13)-.5)*ew*(wispy?.27:.17)*spread;
   // All billows follow the same rising and downwind flow; none spins around its own center.
   const drift=Math.sin(age*3.8+birth*.21+e.seed*.01)*ew*(wispy?.07:.045)*p.turb;
   const radius=ew*((wispy?.09:dark?.14:.12)+age*(wispy?.28:dark?.46:.40))*spread;
   particles.push({i,age,r:radius,x:origin+drift+wind*age*eh*.48,
    y:eh*.48-age*eh*growth,angle:(rnd(e.seed+i)-.5)*.16,
    alpha:smooth(age/.12)*smooth((1-age)/.25)*strength*p.density*(wispy?.28:dark?.68:.56)*opacityScale});
  }
  return particles.sort((a,b)=>b.age-a.age);
 }
 function drawClouds(c,particles,e,dark,scale=1,tone=e.color){for(const b of particles){c.save();c.globalAlpha*=clamp(b.alpha*scale,0,.98);c.translate(b.x,b.y);c.rotate(b.angle);const w=b.r*(e.style==='wispy-smoke'?.72:1),h=b.r*(e.style==='wispy-smoke'?1.32:1);c.drawImage(cloudTile(tone,b.i%4,dark),-w,-h,w*2,h*2);c.restore();}}
 function wisps(c,ew,eh,e,t,strength,growth,wind,q){
  const rgb=e.color.match(/\w\w/g).map(n=>Math.round(mix(parseInt(n,16),178,.28)));
  const tone='#'+rgb.map(n=>n.toString(16).padStart(2,'0')).join('');
  drawClouds(c,plumeParticles(ew,eh,e,t,strength,growth,wind,q,false,true),e,false,1.05,tone);
 }
 function smokeFire(c,ew,eh,e,t,strength,growth,wind,q){
  const particles=plumeParticles(ew,eh,e,t,strength,growth,wind,q,true);
  drawClouds(c,particles,e,true,.35);
  connectedFire(c,ew*.69,eh*.62,{...e,style:'fire',embers:0},t,strength*.72,growth,wind,q);
  // Front smoke absorbs the source fire rather than orange-tinting the entire plume.
  drawClouds(c,particles,e,true,.72);
 }
 function burst(c,ew,eh,e,t,strength,growth,wind,q){
  const p=params(e),span=Math.max(1,e.burstDuration??8),age=Math.max(0,(t-e.start)*p.speed);
  const flash=smooth(age/.28)*Math.exp(-age/(span*.34));
  // The first seconds expand sideways; the anchored fire stays active until the layer End.
  if(flash>.015){
   c.save();c.globalAlpha*=clamp(flash*.48);
   drawClouds(c,plumeParticles(ew*1.2,eh*.8,e,t,strength,growth,wind,q,true),e,true,.35);
   c.restore();
  }
  const sustained={...e,style:'intense-fire',embers:0};
  connectedFire(c,ew*(1+flash*.85),eh*(1+flash*.28),sustained,t,strength,growth,wind,q);
  if(flash>.02){
   c.save();c.globalAlpha*=flash*.55;
   for(const side of [-1,1]){
    c.save();c.translate(side*ew*.27,eh*.2);c.rotate(side*.58);
    tile(c,'tongue',(t*.8+rnd(e.seed)+side*.19)*FRAMES,-ew*.2,-eh*.65,ew*.4,eh*.65,p.density,p.heat,q.flameLayers);
    c.restore();
   }
   c.restore();
  }
 }
 function flame(c,ew,eh,e,t,strength,growth,wind,q={flameLayers:3,smokeParticles:28}){if(!e.style||e.style==='legacy')return root.LegacyFX.flame(c,ew,eh,e,t,strength,growth,wind,q);if(strength<=0||c.globalAlpha<=0||(e.density??.75)<=0)return;if(e.style==='explosive-fire')burst(c,ew,eh,e,t,strength,growth,wind,q);else connectedFire(c,ew,eh,e,t,strength,growth,wind,q);}
 function smoke(c,ew,eh,e,t,strength,growth,wind,q={flameLayers:3,smokeParticles:28}){if(!e.style||e.style==='legacy'||e.type==='steam')return root.LegacyFX.smoke(c,ew,eh,e,t,strength,growth,wind,q);if(strength<=0||c.globalAlpha<=0||(e.density??.75)<=0)return;if(e.style==='wispy-smoke')return wisps(c,ew,eh,e,t,strength,growth,wind,q);if(e.style==='smoke-fire')return smokeFire(c,ew,eh,e,t,strength,growth,wind,q);const dark=e.style==='dark-smoke';drawClouds(c,plumeParticles(ew,eh,e,t,strength,growth,wind,q,dark),e,dark);}
 function people(c,ew,eh,e,t,strength){
  if(strength<=0||c.globalAlpha<=0)return;c.save();c.globalAlpha*=strength;c.scale(ew,eh);
  const pose=e.style||'standing-person',skin=e.skin||'#ae7654',coat=e.color||'#365f7c',window=pose==='window-person',seated=pose==='seated-person';
  const path=points=>{c.beginPath();points.forEach((p,i)=>i?c.lineTo(...p):c.moveTo(...p));c.closePath();c.fill();};
  const hand=(x,y)=>{c.fillStyle=skin;c.beginPath();c.ellipse(x,y,.048,.026,0,0,TAU);c.fill();};
  if(seated){
   // A side-on, bent-knee pose. Hips rest on a simple seat; shins fall below both knees.
   c.strokeStyle='#64717a';c.lineWidth=.027;c.lineCap='round';c.beginPath();c.moveTo(-.31,-.07);c.lineTo(-.27,.2);c.lineTo(.21,.2);c.moveTo(-.25,.2);c.lineTo(-.27,.48);c.moveTo(.16,.2);c.lineTo(.17,.47);c.stroke();
   c.strokeStyle='#283440';c.lineWidth=.13;c.lineJoin='round';c.beginPath();c.moveTo(-.09,.12);c.lineTo(.07,.23);c.lineTo(.07,.41);c.moveTo(.015,.12);c.lineTo(.32,.17);c.lineTo(.35,.40);c.stroke();
   c.fillStyle='#18242d';path([[-.005,.39],[.13,.39],[.18,.48],[-.04,.48]]);path([[.29,.38],[.41,.38],[.47,.47],[.26,.47]]);
   const fabric=c.createLinearGradient(-.25,-.2,.2,.12);fabric.addColorStop(0,coat);fabric.addColorStop(1,'#25343f');c.fillStyle=fabric;
   path([[-.23,-.19],[-.13,-.23],[.10,-.21],[.19,-.12],[.12,.13],[-.12,.16],[-.2,.04]]);
   c.strokeStyle=coat;c.lineWidth=.105;c.beginPath();c.moveTo(-.18,-.14);c.lineTo(-.25,.02);c.lineTo(-.02,.17);c.moveTo(.14,-.13);c.lineTo(.26,.02);c.lineTo(.29,.16);c.stroke();hand(-.02,.17);hand(.29,.16);
   c.fillStyle=skin;c.fillRect(-.055,-.31,.12,.1);c.beginPath();c.ellipse(.005,-.32,.14,.105,-.10,0,TAU);c.fill();
   c.fillStyle='#302a28';c.beginPath();c.ellipse(-.005,-.375,.14,.055,-.08,Math.PI,TAU);c.fill();
   c.strokeStyle='#ffffff22';c.lineWidth=.012;c.beginPath();c.moveTo(-.025,-.14);c.lineTo(.035,.10);c.stroke();c.restore();return;
  }
  const headY=window?-.24:-.32,shoulderY=window?-.04:-.19,hipY=window?.46:.12;
  if(!window){c.fillStyle='#28343d';path([[-.17,.08],[-.01,.08],[-.035,.44],[-.19,.44]]);path([[.01,.08],[.17,.08],[.20,.44],[.05,.44]]);c.fillStyle='#1a242c';path([[-.19,.42],[-.03,.42],[-.025,.49],[-.25,.49]]);path([[.05,.42],[.21,.42],[.27,.49],[.05,.49]]);}
  const fabric=c.createLinearGradient(-.25,0,.25,.25);fabric.addColorStop(0,coat);fabric.addColorStop(1,'#25343f');c.fillStyle=fabric;path([[-.23,shoulderY],[-.10,shoulderY-.025],[.11,shoulderY-.025],[.23,shoulderY],[.18,hipY],[-.18,hipY]]);
  c.strokeStyle=coat;c.lineWidth=.13;c.lineCap='round';c.lineJoin='round';
  c.beginPath();c.moveTo(-.19,shoulderY+.015);if(window){c.lineTo(-.38,.18);c.lineTo(-.45,.12);}else{c.lineTo(-.29,.04);c.lineTo(-.28,.17);}c.stroke();hand(window?-.45:-.28,window?.12:.18);
  c.beginPath();c.moveTo(.19,shoulderY+.015);if(pose==='calling-person'||window){const wave=Math.sin(t*2.2+e.seed)*.035;c.lineTo(.34,shoulderY-.09);c.lineTo(.39+wave,headY-.13);c.stroke();hand(.39+wave,headY-.14);}else{c.lineTo(.28,.02);c.lineTo(seated?.15:.27,seated?.15:.18);c.stroke();hand(seated?.15:.27,seated?.15:.18);}
  c.fillStyle=skin;c.fillRect(-.065,headY+.065,.13,.075);c.beginPath();c.ellipse(0,headY,.14,.10,0,0,TAU);c.fill();
  c.fillStyle='#302a28';c.beginPath();c.ellipse(0,headY-.052,.143,.053,0,Math.PI,TAU);c.fill();
  c.strokeStyle='#ffffff22';c.lineWidth=.012;c.beginPath();c.moveTo(0,shoulderY+.02);c.lineTo(0,hipY-.02);c.stroke();c.restore();
 }
 root.FX={flame,smoke,people,cacheInfo:()=>({fireTiles:fireCache.size,cloudTiles:cloudCache.size})};
})(globalThis);
