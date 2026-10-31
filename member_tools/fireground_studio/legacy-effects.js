// Original procedural raster effects: advected periodic noise and shaded smoke billows.
// All animation is a deterministic function of scenario time and emitter seed.
(function(root){
 const clamp=(v,a=0,b=1)=>Math.max(a,Math.min(b,v));
 const smooth=t=>{t=clamp(t);return t*t*(3-2*t);},mix=(a,b,t)=>a+(b-a)*t;
 function hash(x,y,s=0){let n=Math.imul(x,374761393)^Math.imul(y,668265263)^s;n=Math.imul(n^(n>>>13),1274126177);return ((n^(n>>>16))>>>0)/4294967295;}
 const rand=n=>hash(n|0,(n*37)|0,43891);
 function noise(x,y,s=0,period=0){const ix=Math.floor(x),iy=Math.floor(y),tx=smooth(x-ix),ty=smooth(y-iy),wrap=v=>period?((v%period)+period)%period:v;return mix(mix(hash(ix,wrap(iy),s),hash(ix+1,wrap(iy),s),tx),mix(hash(ix,wrap(iy+1),s),hash(ix+1,wrap(iy+1),s),tx),ty);}
 const smokeCache=new Map(),flameCache=new Map(),FRAMES=36,FW=128,FH=224;
 function smokeSprite(color,seed){
  const key=color+seed;if(smokeCache.has(key))return smokeCache.get(key);
  const c=document.createElement('canvas');c.width=c.height=160;const ctx=c.getContext('2d'),im=ctx.createImageData(160,160),rgb=color.match(/\w\w/g).map(v=>parseInt(v,16));
  for(let y=0;y<160;y++)for(let x=0;x<160;x++){
   const dx=(x-80)/73,dy=(y-80)/73,r=Math.hypot(dx,dy),angle=Math.atan2(dy,dx),n=noise(x/31,y/31,seed+20)*.55+noise(x/13,y/13,seed+91)*.3+noise(x/5,y/5,seed+15)*.15;
   const lobes=Math.sin(angle*5+seed)*.045+Math.sin(angle*8+seed*2)*.026,edge=smooth((1-r+lobes+(n-.5)*.28)/.24),density=edge*(.68+n*.32);
   const light=clamp(1-r*r),shade=(n-.5)*63+(-dx-dy)*15+light*8-8,i=(y*160+x)*4;
   for(let k=0;k<3;k++)im.data[i+k]=clamp(rgb[k]+shade,0,255);im.data[i+3]=Math.round(density*255);
  }ctx.putImageData(im,0,0);if(smokeCache.size>40)smokeCache.clear();smokeCache.set(key,c);return c;
 }
 function flameTexture(frame){
  frame=((frame%FRAMES)+FRAMES)%FRAMES;if(flameCache.has(frame))return flameCache.get(frame);
  const c=document.createElement('canvas');c.width=FW;c.height=FH;const ctx=c.getContext('2d'),im=ctx.createImageData(FW,FH),travel=frame/FRAMES*12;
  for(let y=0;y<FH;y++)for(let x=0;x<FW;x++){
   const u=x/(FW-1)*2-1,v=y/(FH-1),warp=(noise(u*2.8+5,v*5+travel,41,12)-.5)*(.26+(1-v)*.3);
   const n=noise((u+warp)*4+9,v*6+travel,123,12)*.5+noise((u+warp)*9+21,v*12+travel*2,333,24)*.32+noise(u*20+47,v*24+travel*4,801,48)*.18;
   const fuel=Math.pow(v,.8)*1.06-Math.abs(u+warp)*.95-.11,d=fuel+(n-.5)*(.95+(1-v)*.5),alpha=smooth(d/.14)*smooth(v/.08);
   if(!alpha)continue;
   const heat=clamp(d*1.65+v*.16),i=(y*FW+x)*4;
   im.data[i]=Math.round(mix(195,255,smooth(heat/.35)));im.data[i+1]=Math.round(heat<.5?mix(28,147,heat*2):mix(147,247,(heat-.5)*2));im.data[i+2]=Math.round(heat<.6?mix(1,10,heat/.6):mix(10,149,(heat-.6)/.4));im.data[i+3]=Math.round(alpha*255);
  }ctx.putImageData(im,0,0);flameCache.set(frame,c);return c;
 }
 function smoke(c,ew,eh,e,t,strength,growth,wind,quality={smokeParticles:28}){
  if(strength<=0||c.globalAlpha<=0)return;
  const count=quality.smokeParticles,p=[];for(let sample=0;sample<count;sample++){const i=Math.floor(sample*28/count),life=5.5+rand(e.seed+i)*3,age=(t/life+rand(e.seed+i*3))%1;
   p.push({age,i,r:ew*(.13+age*.46),x:(rand(e.seed+i*7)-.5)*ew*.22+wind*age*eh*.62+Math.sin(age*5+i*.6)*ew*.09,y:eh*.48-age*eh*growth});}
  p.sort((a,b)=>b.age-a.age);
  for(const b of p){const fade=smooth(b.age/.12)*smooth((1-b.age)/.25),opacity=fade*strength*(e.type==='steam'?.19:.48);c.save();c.globalAlpha*=Math.min(.95,opacity*28/count);c.translate(b.x,b.y);c.rotate((rand(b.i+e.seed)-.5)*1.3+b.age*.45);c.drawImage(smokeSprite(e.color,b.i%5),-b.r,-b.r,b.r*2,b.r*2);c.restore();}
 }
 function flame(c,ew,eh,e,t,strength,growth,wind,quality={flameLayers:3}){
  if(strength<=0||c.globalAlpha<=0)return;
  const inherited=c.globalAlpha,height=eh*strength*growth,width=ew*(.96+Math.sin(t*7+e.seed)*.035),phase=(t*.53+rand(e.seed))*FRAMES,fi=Math.floor(phase),blend=phase-fi,base=eh*.48;
  c.save();c.globalCompositeOperation='screen';const glow=c.createRadialGradient(0,base-height*.18,0,0,base-height*.18,width*1.15);glow.addColorStop(0,`rgba(255,99,12,${strength*.22})`);glow.addColorStop(1,'rgba(255,70,0,0)');c.fillStyle=glow;c.fillRect(-width*1.2,base-height*.7,width*2.4,height*1.2);c.restore();
  c.save();c.transform(1,0,-wind*.22,1,wind*height*.13,0);
  // Two interpolated noisy flame sheets create broken edges and rising internal detail.
  c.globalAlpha=inherited*(quality.flameLayers===1?1:1-blend)*.94;c.drawImage(flameTexture(fi),-width*.55,base-height,width*1.1,height);
  if(quality.flameLayers>1){c.globalAlpha=inherited*blend*.94;c.drawImage(flameTexture(fi+1),-width*.55,base-height,width*1.1,height);}
  if(quality.flameLayers>2){c.globalAlpha=inherited*.22;c.scale(-1,1);c.drawImage(flameTexture(fi+13),-width*.49,base-height*.76,width*.98,height*.76);}c.restore();
 }
 root.LegacyFX={smoke,flame};
 // Warm the shared texture atlas during idle slices, never during an interaction.
 if(typeof requestIdleCallback==='function'){let n=0;const warm=deadline=>{while(n<FRAMES&&deadline.timeRemaining()>3)flameTexture(n++);if(n<FRAMES)requestIdleCallback(warm);};requestIdleCallback(warm);}
})(globalThis);
