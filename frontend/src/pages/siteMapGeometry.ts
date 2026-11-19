// 지도 비교용 순수 함수. React·API 모듈을 import하지 않아 node --test로 바로 검사한다(tests/).

export type XY = [number, number];

/** 닫힌 ring(GeoJSON처럼 끝점 = 첫 점)이면 끝점을 뺀다. 열린 ring은 그대로 둔다. */
export function openRing(pts: XY[], tol = 1e-9): XY[] {
  if (pts.length < 2) return pts;
  const [fx, fy] = pts[0];
  const [lx, ly] = pts[pts.length - 1];
  return Math.abs(fx - lx) <= tol && Math.abs(fy - ly) <= tol ? pts.slice(0, -1) : pts;
}

/** 두 다각형이 같은 꼭짓점(순서·닫힘 여부 무관, 허용 오차 tol m)으로 이루어졌는지 */
export function sameRing(a: XY[], b: XY[], tol = 0.05): boolean {
  const p = openRing(a), q = openRing(b);
  if (p.length !== q.length) return false;
  const near = ([x, y]: XY) => ([u, v]: XY) => Math.abs(x - u) <= tol && Math.abs(y - v) <= tol;
  return p.every((pt) => q.some(near(pt))) && q.every((pt) => p.some(near(pt)));
}
