import { test } from "node:test";
import assert from "node:assert/strict";
import { openRing, sameRing, type XY } from "../src/pages/siteMapGeometry.ts";

const open: XY[] = [[0, 0], [2, 0], [2, 1], [0, 1]];
const closed: XY[] = [...open, [0, 0]];

test("닫힌 ring은 끝점을 제거한다", () => {
  assert.deepEqual(openRing(closed), open);
  assert.deepEqual(openRing(open), open);
});

test("Backend 5점(닫힘)과 sim_scene 4점(열림)은 같은 배치로 본다", () => {
  assert.equal(sameRing(closed, open), true);
  assert.equal(sameRing(open, closed), true);
  assert.equal(sameRing(closed, closed), true);
});

test("꼭짓점 순서가 달라도 허용 오차 안이면 같다", () => {
  assert.equal(sameRing([[0, 1], [0.03, 0], [2, 0], [2, 1], [0, 1]], open), true);
});

test("꼭짓점이 5cm 넘게 다르면 불일치", () => {
  assert.equal(sameRing([[0, 0], [2.2, 0], [2.2, 1], [0, 1], [0, 0]], open), false);
});
