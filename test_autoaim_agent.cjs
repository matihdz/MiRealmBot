'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const context = vm.createContext({});
vm.runInContext(fs.readFileSync(path.join(__dirname, 'autoaim_agent.js'), 'utf8'), context);
const choose = context.chooseAutoAimTarget;
const enemy = (id, x, y, vx = 0, vy = 0) => ({ id, x, y, vx, vy, eligible: true });
let checks = 0;
function test(name, action) { action(); checks += 1; console.log('PASS ' + name); }

test('Close to Mouse selects cursor distance, even when another enemy is nearer the player', () => {
    const result = choose([enemy(1, 1, 0), enemy(2, 9, 0)], {x: 10, y: 0}, 4, 0);
    assert.equal(result.id, 2);
});
test('Selection uses current enemy positions before extrapolation', () => {
    const result = choose([enemy(1, 1, 0, 2, 0), enemy(2, 2, 0, -2, 0)], {x: 0, y: 0}, 4, 2);
    assert.equal(result.id, 1);
    assert.equal(result.x, 5);
});
test('Extrapolation follows both movement axes using the requested coefficient', () => {
    const result = choose([enemy(4, 10, 20, 0.01, -0.02)], {x: 10, y: 20}, 4, 302);
    assert.equal(result.x, Math.fround(10 + Math.fround(Math.fround(302) * 0.01)));
    assert.equal(result.y, Math.fround(20 + Math.fround(Math.fround(302) * -0.02)));
});
test('Zero extrapolation aims at the present position without a usable velocity', () => {
    const result = choose([enemy(2, 10, 20, NaN, Infinity)], {x: 10, y: 20}, 4, 0);
    assert.equal(result.x, 10);
    assert.equal(result.y, 20);
});
test('Radius uses a strict boundary and leaves original aim when nothing qualifies', () => {
    assert.equal(choose([enemy(1, 4, 0)], {x: 0, y: 0}, 4, 0), null);
    assert.equal(choose([], {x: 0, y: 0}, 4, 302), null);
});
test('Ineligible, malformed and overflowing candidates do not produce an aim', () => {
    assert.equal(choose([{...enemy(1, 0, 0), eligible: false}, enemy(2, NaN, 0),
        enemy(3, 0, 0, Infinity, 0), enemy(4, 0, 0, 1e38, 0)], {x: 0, y: 0}, 4, 302), null);
});
test('Invalid cursor and settings fail without an aim', () => {
    for (const [cursor, radius, scale] of [[{x: NaN, y: 0}, 4, 0], [{x: 0, y: 0}, 0, 0],
        [{x: 0, y: 0}, -1, 0], [{x: 0, y: 0}, 4, -1], [{x: 0, y: 0}, 4, Infinity]]) {
        assert.equal(choose([enemy(1, 0, 0)], cursor, radius, scale), null);
    }
});
console.log(`${checks} AutoAim selection checks passed.`);
