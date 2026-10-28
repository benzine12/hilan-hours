# -*- coding: utf-8 -*-
"""Scriptable's drawing API, made up for the tests that run the widget in Node.

Strict where a phone would fail quietly: a colour that is not #RRGGBB with an
alpha between 0 and 1, a coordinate that is not a finite number, and anything
drawn outside its canvas are all written to `drawing.problems`, which a test
reads back. A layout that overflows on the phone overflows here first.

Each runner puts DRAWING in its script and passes `drawing` into its context.
"""

DRAWING = r"""
const drawing = (() => {
  const problems = [], images = [];
  const finite = (v, what) => {
    if (typeof v !== 'number' || !Number.isFinite(v)) problems.push(`${what} is ${v}`);
  };
  class Size { constructor(w, h) { finite(w, 'Size width'); finite(h, 'Size height');
                                   this.width = w; this.height = h; } }
  class Point { constructor(x, y) { finite(x, 'Point x'); finite(y, 'Point y'); this.x = x; this.y = y; } }
  class Rect { constructor(x, y, w, h) {
    [x, y, w, h].forEach((v, i) => finite(v, `Rect ${'xywh'[i]}`));
    if (w < 0 || h < 0) problems.push(`Rect of ${w}x${h}`);
    Object.assign(this, { x, y, width: w, height: h }); } }
  class Color {
    constructor(hex, alpha = 1) {
      if (!/^#[0-9a-f]{6}$/i.test(String(hex))) problems.push(`colour ${hex}`);
      if (!(alpha >= 0 && alpha <= 1)) problems.push(`alpha ${alpha}`);
      this.hex = hex; this.alpha = alpha;
    }
    static gray() { return new this('#8E8E93'); } static white() { return new this('#FFFFFF'); }
    static dynamic(light, dark) { return dark; }
  }
  class Path {
    constructor() { this.points = []; }
    move(p) { this.points.push(p); } addLine(p) { this.points.push(p); }
    addRoundedRect(r, cw, ch) { finite(cw, 'corner'); finite(ch, 'corner');
      this.points.push(new Point(r.x, r.y), new Point(r.x + r.width, r.y + r.height)); }
  }
  class DrawContext {
    constructor() { this.size = null; this.paths = 0; this.texts = []; this.colours = []; }
    inside(p, what) {
      const s = this.size, slack = 0.5;
      if (p.x < -slack || p.y < -slack || p.x > s.width + slack || p.y > s.height + slack)
        problems.push(`${what} at ${p.x.toFixed(1)},${p.y.toFixed(1)} outside ${s.width}x${s.height}`);
    }
    addPath(path) { path.points.forEach(p => this.inside(p, 'path')); this.paths++; }
    setFillColor(c) { this.colours.push(c); } setStrokeColor(c) { this.colours.push(c); }
    setLineWidth(w) { finite(w, 'line width'); }
    fillPath() {} strokePath() {}
    setFont() {} setTextColor(c) { this.colours.push(c); }
    setTextAlignedLeft() {} setTextAlignedCenter() {} setTextAlignedRight() {}
    drawTextInRect(text, r) {
      this.inside(new Point(r.x, r.y), `text "${text}"`);
      this.inside(new Point(r.x + r.width, r.y + r.height), `text "${text}"`);
      this.texts.push(String(text));
    }
    getImage() {
      if (!this.size) problems.push('an image drawn with no size');
      const image = { width: this.size && this.size.width, height: this.size && this.size.height,
                      paths: this.paths, texts: this.texts,
                      colours: this.colours.map(c => `${c.hex}/${c.alpha}`) };
      images.push(image);
      return image;
    }
  }
  return { problems, images, classes: { Size, Point, Rect, Color, Path, DrawContext } };
})();
"""
