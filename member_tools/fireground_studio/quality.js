// Quality affects raster resolution and sampling only, never authored scenario state/time.
(function(root){
 const presets={high:{name:'High',maxPixels:2073600,smokeParticles:28,flameLayers:3},balanced:{name:'Balanced',maxPixels:518400,smokeParticles:18,flameLayers:2},low:{name:'Low',maxPixels:230400,smokeParticles:10,flameLayers:1}};
 function settings(mode,view){const count=view.effects.filter(e=>e.visible).length;const level=mode==='auto'?(count>6?'low':count>3?'balanced':'high'):Object.hasOwn(presets,mode)?mode:'high';return {...presets[level],level};}
 root.Quality={settings,presets};
})(globalThis);
