import { test } from 'node:test';
import assert from 'node:assert/strict';
import { MotionBuffer, Gesture, MotionPump, SamsungPointerCard } from '../custom_components/samsung_pointer/www/samsung-pointer-card.js';

function fixture() {
  let time = 0, seq = 0;
  const tasks = new Map(), calls = [], deferred = [];
  const pump = new MotionPump((x, y) => {
    calls.push([x,y]); return new Promise((resolve,reject) => deferred.push({resolve,reject}));
  }, { now: () => time, schedule: (fn, delay) => { tasks.set(++seq, {fn, due:time+delay}); return seq; }, cancel: id => tasks.delete(id) });
  return {pump,calls,deferred, async advance(ms) {
    time += ms;
    for (const [id, task] of [...tasks]) if (task.due <= time) { tasks.delete(id); task.fn(); }
    await Promise.resolve(); await Promise.resolve();
  }};
}

test('horizontal and diagonal gestures start at the same radial threshold', () => {
  for (const [x,y] of [[4,0],[0,4],[3,3],[-3,-3]]) {
    const g = new Gesture(0,0,0); assert.deepEqual(g.move(x,y),[x,y]); assert.equal(g.isTap(10),false);
  }
});
test('subthreshold motion is recovered once movement starts; no click after return', () => {
  const g = new Gesture(0,0,0); assert.deepEqual(g.move(1,1),[0,0]);
  assert.deepEqual(g.move(4,2),[4,2]); assert.deepEqual(g.move(0,0),[-4,-2]); assert.equal(g.isTap(100),false);
});
test('brief stationary tap clicks; long press does not', () => {
  const g = new Gesture(0,0,0); assert.equal(g.isTap(200),true); assert.equal(g.isTap(301),false);
});
test('same physical movement is invariant to event frequency', () => {
  for (const count of [10,60,120,240]) {
    const b = new MotionBuffer(); const g = new Gesture(0,0,0);
    for (let i=1;i<=count;i++) { const [x,y]=g.move(100*i/count,60*i/count); b.add(x*1.5,y*1.5,50*i/count); }
    const [x,y]=b.take(50); assert.ok(Math.abs(x-150)<1e-8); assert.ok(Math.abs(y-90)<1e-8);
  }
});
test('fractional samples survive batching', () => {
  const b = new MotionBuffer(); for(let i=0;i<10;i++) b.add(0.1,-0.1,i);
  const [x,y]=b.take(10); assert.ok(Math.abs(x-1)<1e-8); assert.ok(Math.abs(y+1)<1e-8);
});
test('old samples expire independently; recent motion survives a stall', () => {
  const b = new MotionBuffer(); b.add(50,0,0); b.add(2,3,90); assert.deepEqual(b.take(110),[2,3]);
});
test('huge accumulated movement stays within service bounds', () => {
  const b = new MotionBuffer(); b.add(10000,-10000,0); assert.deepEqual(b.take(0),[2000,-2000]);
});
test('only one request is in flight, intermediate displacements accumulate', async () => {
  const f=fixture(); f.pump.add(1,0); await f.advance(0);
  f.pump.add(2,3); f.pump.add(4,5); await f.advance(25); assert.equal(f.calls.length,1);
  f.deferred[0].resolve(); await f.advance(0); await f.advance(0);
  assert.deepEqual(f.calls,[[1,0],[6,8]]);
});
test('a slow response cannot release a stale tail of motion', async () => {
  const f=fixture(); f.pump.add(1,0); await f.advance(0); f.pump.add(100,0);
  await f.advance(150); f.deferred[0].resolve(); await f.advance(0); await f.advance(0);
  assert.deepEqual(f.calls,[[1,0]]);
});
test('error drops pending samples and never retries a sent command', async () => {
  const f=fixture(); f.pump.add(1,0); await f.advance(0); f.pump.add(20,0);
  f.deferred[0].reject(new Error('offline')); await f.advance(30); await f.advance(30);
  assert.deepEqual(f.calls,[[1,0]]); assert.equal(f.pump.buffer.samples.length,0);
});
test('cancel/hidden/dispose clears scheduled unsent movement', async () => {
  const f=fixture(); f.pump.add(1,0); f.pump.clear(); await f.advance(100); assert.equal(f.calls.length,0);
  f.pump.add(2,0); f.pump.dispose(); await f.advance(100); assert.equal(f.calls.length,0);
});
test('fresh final sample is sent without inertia or periodic empty commands', async () => {
  const f=fixture(); f.pump.add(2,3); await f.advance(0); f.deferred[0].resolve();
  await f.advance(1000); assert.deepEqual(f.calls,[[2,3]]);
});
test('card sends unaccelerated fractional deltas to the selected entity', async () => {
  const card=new SamsungPointerCard(); card.config={entity:'remote.test_pointer',interval:25};
  card._hass={states:{'remote.test_pointer':{state:'on'}},callService:async (...args)=>args};
  card.createPump();
  assert.deepEqual(await card.pump.send(.3,.4),['samsung_pointer','move',{x:.3,y:.4,max_speed_gain:1},{entity_id:'remote.test_pointer'}]);
  card.pump.dispose();
});

test('mode switch replaces the pad and passes HA to the active card', async () => {
  const { SamsungPointerSwitchCard } = await import('../custom_components/samsung_pointer/www/samsung-pointer-card.js');
  const card = new SamsungPointerSwitchCard();
  const rendered = [], pressed = [];
  let removed = 0;
  card.config = { off_card: {type:'old'}, on_card:{type:'new'} };
  card.shadowRoot = {querySelector:selector=>selector==='button'
    ? {setAttribute:(_name,value)=>pressed.push(value)}
    : {replaceChildren:child=>{if(child)rendered.push(child)}}};
  globalThis.window = {loadCardHelpers:async()=>({createCardElement:config=>({config,remove:()=>removed++})})};
  card.hass={test:true}; await card.showCard();
  assert.equal(card.child.config.type,'old'); assert.equal(card.child.hass.test,true);
  card.toggle(); await Promise.resolve(); await Promise.resolve();
  assert.equal(card.child.config.type,'new'); assert.equal(removed,1); assert.deepEqual(pressed,['true']);
  card.toggle(); await Promise.resolve(); await Promise.resolve();
  assert.equal(card.child.config.type,'old'); assert.equal(removed,2);
  card.disconnectedCallback(); assert.equal(removed,3); assert.equal(card.child,null);
  delete globalThis.window;
});

test('slow card helper cannot mount an obsolete mode after rapid switching', async () => {
  const { SamsungPointerSwitchCard } = await import('../custom_components/samsung_pointer/www/samsung-pointer-card.js');
  const card = new SamsungPointerSwitchCard(), pending=[], rendered=[];
  card.config={off_card:{type:'old'},on_card:{type:'new'}};
  card.shadowRoot={querySelector:()=>({replaceChildren:c=>{if(c)rendered.push(c)},setAttribute:()=>{}})};
  globalThis.window={loadCardHelpers:()=>new Promise(resolve=>pending.push(resolve))};
  const first=card.showCard(); card.toggle();
  const helpers={createCardElement:config=>({config,remove:()=>{}})};
  pending[0](helpers); await first; assert.equal(rendered.length,0);
  pending[1](helpers); await Promise.resolve(); await Promise.resolve();
  assert.equal(rendered.length,1); assert.equal(rendered[0].config.type,'new');
  delete globalThis.window;
});

test('tilt starts without jump, stops with phone and handles screen rotation and stale events', async () => {
  const {TiltTracker}=await import('../custom_components/samsung_pointer/www/samsung-pointer-card.js');
  const t=new TiltTracker();
  assert.deepEqual(t.sample(20,10,0,0),[0,0]);
  assert.deepEqual(t.sample(22,13,0,20),[36,24]);
  assert.deepEqual(t.sample(22,13,0,40),[0,0]);
  assert.deepEqual(t.sample(30,30,0,300),[0,0]);
  assert.deepEqual(t.sample(30,30,90,320),[0,0]);
  const [x,y]=t.sample(32,33,90,340); assert.ok(Math.abs(x-24)<1e-8); assert.ok(Math.abs(y+36)<1e-8);
  assert.deepEqual(t.sample(null,0,90,350),[0,0]);
  assert.deepEqual(t.sample(20,0,90,360),[0,0]);
});
test('tilt rejects pole jumps and handles wrapped angles', async () => {
  const {TiltTracker}=await import('../custom_components/samsung_pointer/www/samsung-pointer-card.js');
  const t=new TiltTracker();t.sample(179,0,0,0);
  assert.deepEqual(t.sample(-179,0,0,20),[0,24]);
  assert.deepEqual(t.sample(0,80,0,40),[0,0]);
});
test('releasing gyro cancels queued motion, sensor subscription, and never clicks', () => {
  const c=new SamsungPointerCard(); let cleared=0,removed=0,clicked=0;
  c.pointerId=7;c.gesture={};c.gyroActive=true;c.orientationHandler=()=>{};
  c.pump={clear:()=>cleared++};c.doClick=()=>clicked++;
  globalThis.window={removeEventListener:()=>removed++};
  c.up({pointerId:7});assert.equal(c.pointerId,null);assert.equal(c.gyroActive,false);
  assert.equal(cleared,1);assert.equal(removed,1);assert.equal(clicked,0);
  delete globalThis.window;
});
