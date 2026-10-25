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
test('Disabled boss priority preserves the closest enemy, even beside a boss', () => {
    const mob = {...enemy(1, 1, 0), is_boss: false};
    const boss = {...enemy(2, 3, 0), is_boss: true};
    assert.equal(choose([mob, boss], {x: 0, y: 0}, 4, 0, false).id, 1);
});
test('Enabled boss priority wins over a nearer mob regardless of enumeration order', () => {
    const mob = {...enemy(1, 1, 0), is_boss: false};
    const boss = {...enemy(2, 3, 0), is_boss: true};
    for (const candidates of [[mob, boss], [boss, mob]]) {
        const selected = choose(candidates, {x: 0, y: 0}, 4, 0, true);
        assert.equal(selected.id, 2);
        assert.equal(selected.is_boss, true);
    }
});
test('Several bosses retain Close to Mouse ordering within the boss group', () => {
    assert.equal(choose([{...enemy(1, 0.1, 0), is_boss: false},
        {...enemy(2, 3, 0), is_boss: true}, {...enemy(3, 2, 0), is_boss: true}],
        {x: 0, y: 0}, 4, 0, true).id, 3);
});
test('A boss outside the radius or ineligible cannot steal normal targeting', () => {
    const mob = {...enemy(1, 1, 0), is_boss: false};
    const invalidBosses = [{...enemy(2, 4, 0), is_boss: true},
        {...enemy(3, 0, 0), is_boss: true, eligible: false}];
    const selected = choose([mob, ...invalidBosses], {x: 0, y: 0}, 4, 0, true);
    assert.equal(selected.id, 1);
    assert.equal(selected.is_boss, false);
});
test('Missing boss evidence stays unknown and does not get boss priority', () => {
    const selected = choose([enemy(1, 1, 0), {...enemy(2, 2, 0), is_boss: 1}],
        {x: 0, y: 0}, 4, 0, true);
    assert.equal(selected.id, 1);
    assert.equal(selected.is_boss, null);
});
test('An unusable boss prediction falls back to a valid enemy without moving the radius', () => {
    const selected = choose([{...enemy(2, 0, 0, NaN, 0), is_boss: true}, enemy(1, 1, 0)],
        {x: 0, y: 0}, 4, 302, true);
    assert.equal(selected.id, 1);
});
test('Wall filtering is optional and keeps the old selection when disabled', () => {
    const wall = {...enemy(1, 0.1, 0), is_wall: true};
    const mob = {...enemy(2, 1, 0), is_wall: false};
    assert.equal(choose([wall, mob], {x: 0, y: 0}, 4, 0, false, false).id, 1);
    assert.equal(choose([wall, mob], {x: 0, y: 0}, 4, 0, false, true).id, 2);
});
test('Wall exclusion happens before boss prioritization', () => {
    const markedWall = {...enemy(1, 0.1, 0), is_wall: true, is_boss: true};
    const mob = {...enemy(2, 1, 0), is_wall: false, is_boss: false};
    assert.equal(choose([markedWall, mob], {x: 0, y: 0}, 4, 0, true, true).id, 2);
});
test('Only walls leaves the original cursor aim, while unknown wall evidence stays unknown', () => {
    assert.equal(choose([{...enemy(1, 0.1, 0), is_wall: true}], {x: 0, y: 0}, 4, 0, true, true), null);
    const selected = choose([enemy(2, 1, 0)], {x: 0, y: 0}, 4, 0, false, true);
    assert.equal(selected.id, 2);
    assert.equal(selected.is_wall, null);
});
console.log(`${checks} AutoAim selection checks passed.`);
