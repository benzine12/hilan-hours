# -*- coding: utf-8 -*-
"""Runs the widget under node, in a made-up Scriptable with a made-up Hilan.

The widget only ever runs on a phone, so its network code would otherwise go
untested until it misbehaved there. This stands in for the parts of Scriptable
the widget touches — Request, Keychain, FileManager, Alert, the widget classes —
and answers its requests from a script of responses, recording everything it
sent and everything it showed.

A scenario is a dict:

    routes      [{method, path, responses: [response, ...]}] — a request goes
                to the first route with its method whose path its own starts
                with; responses are used in turn, the last one repeated.
                A response: {status, body, url, cookies, redirect: {to, method}}.
                ``url`` is where the request ended up, after redirects.
    keychain    what the Keychain holds to begin with
    files       documents already saved, by file name
    config      Scriptable's ``config`` (where the script is running)
    now         the instant it is, as an ISO string
    alert       {choice, fields} — what the person does with an alert
    call        "run" (default) or ["fetchMonth", year, month]
    requestSeconds  how long each request takes on the fake clock

What comes back: requests, texts, spoken, output, alerts, keychain, files,
refreshAfter, error, and for a fetchMonth call, the page it returned.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

WIDGET = Path(__file__).resolve().parent.parent / "ios" / "HilanWidget.js"
SITE = "https://example.net.hilan.co.il"
NODE = shutil.which("node")

RUNNER = r"""
const fs = require('fs'), vm = require('vm');
const [widgetPath, scenarioPath] = process.argv.slice(1);
const spec = JSON.parse(fs.readFileSync(scenarioPath, 'utf-8'));
const code = fs.readFileSync(widgetPath, 'utf-8').replace(/^await run\(\);\s*$/m, '')
  .replace(/^const BASE_URL = .*$/m, `const BASE_URL = ${JSON.stringify(spec.site)};`);

// The clock moves only when a request takes time (spec.requestSeconds).
let instant = spec.now ? Date.parse(spec.now) : Date.now();
class FixedDate extends Date {
  constructor(...a) { if (a.length === 0) super(instant); else super(...a); }
  static now() { return instant; }
}

const seen = { requests: [], texts: [], spoken: [], output: null, alerts: [], refreshAfter: null };
const files = { ...(spec.files || {}) };
const keychain = { ...(spec.keychain || {}) };
const served = new Map();
const docs = '/docs';

function respond(method, url) {
  if (!url.startsWith(spec.site + '/')) throw new Error(`a request to another host: ${url}`);
  const path = url.slice(spec.site.length).split('?')[0];
  const index = (spec.routes || []).findIndex(r => r.method === method && path.startsWith(r.path));
  if (index < 0) throw new Error(`no route for ${method} ${path}`);
  const list = spec.routes[index].responses;
  const n = served.get(index) || 0;
  served.set(index, n + 1);
  return list[Math.min(n, list.length - 1)];
}

class Request {
  constructor(url) { this.url = url; this.method = 'GET'; this.headers = {}; this.body = null; }
  async loadString() {
    instant += (spec.requestSeconds || 0) * 1000;
    const sent = { method: this.method, url: this.url, headers: { ...this.headers }, body: this.body,
                   timeout: this.timeoutInterval };
    seen.requests.push(sent);
    const r = respond(this.method, this.url);
    if (r.fail) throw new Error(r.fail);
    let final = r.url ? spec.site + r.url : this.url;
    if (r.redirect) {
      const hop = new Request(r.redirect.to);
      hop.method = r.redirect.method || this.method;
      hop.headers = {};
      const next = this.onRedirect ? this.onRedirect(hop) : hop;
      sent.redirect = { to: r.redirect.to, followed: next != null,
                        cookie: next ? (next.headers || {}).Cookie || null : null };
      if (next == null) {
        this.response = { statusCode: 302, headers: {}, cookies: [], url: this.url };
        return '';
      }
      final = r.redirect.to;
    }
    this.response = { statusCode: r.status || 200, headers: r.headers || {}, cookies: r.cookies || [], url: final };
    return r.body || '';
  }
}

class Widget {
  constructor() { this.url = ''; }
  setPadding() {} addSpacer() {} layoutHorizontally() {} layoutVertically() {}
  addText(t) { seen.texts.push(t); return {}; }
  addStack() { return new Widget(); }
  presentMedium() { return Promise.resolve(); }
}

class Alert {
  constructor() { this.fields = []; }
  addTextField(p, v) { this.fields.push(v || ''); }
  addSecureTextField(p, v) { this.fields.push(v || ''); }
  addAction() {} addCancelAction() {}
  presentAlert() {
    seen.alerts.push({ title: this.title, message: this.message });
    return Promise.resolve((spec.alert || {}).choice || 0);
  }
  textFieldValue(i) { return ((spec.alert || {}).fields || [])[i] || ''; }
}

const ctx = {
  console: { log() {}, error() {} },
  Date: FixedDate,
  config: spec.config || { runsInWidget: true, runsInApp: false, widgetFamily: 'medium' },
  FileManager: { local: () => ({
    documentsDirectory: () => docs,
    joinPath: (a, b) => a + '/' + b,
    fileExists: p => p.slice(docs.length + 1) in files,
    readString: p => files[p.slice(docs.length + 1)],
    writeString: (p, s) => { files[p.slice(docs.length + 1)] = s; },
    remove: p => { delete files[p.slice(docs.length + 1)]; },
  }) },
  Keychain: {
    contains: k => k in keychain, get: k => keychain[k],
    set: (k, v) => { keychain[k] = v; }, remove: k => { delete keychain[k]; },
  },
  Script: { complete() {}, setWidget(w) { seen.refreshAfter = w.refreshAfterDate ? w.refreshAfterDate.toISOString() : null; },
            setShortcutOutput(o) { seen.output = o; } },
  Speech: { speak(s) { return new Promise(done => setTimeout(() => { seen.spoken.push(s); done(); }, 5)); } },
  Request, Alert, ListWidget: Widget,
  Font: { systemFont: s => s, boldSystemFont: s => s },
  Color: class { static gray() { return new this(); } static white() { return new this(); } },
};
vm.createContext(ctx);
vm.runInContext(code + '\nglobalThis.__Client = HilanClient;', ctx);

async function main() {
  const call = spec.call || 'run';
  let page = null, error = null;
  try {
    if (call === 'run') await ctx.run();
    else if (call[0] === 'fetchMonth') page = await new ctx.__Client().fetchMonth(call[1], call[2]);
    else if (call[0] === 'israelNow') page = ctx.israelNow().toString();
    else if (call[0] === 'staleLabel') page = ctx.staleLabel(call[1]);
  } catch (e) {
    error = { name: e.name, message: e.message };
  }
  const out = { ...seen, keychain, files, page, error };
  process.stdout.write(JSON.stringify(out));
}
main();
"""


def phone(tmp_path: Path, scenario: dict, *, tz: str = "Asia/Jerusalem") -> dict:
    """Run the widget through one scenario; see the module docstring."""
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps({"site": SITE, **scenario}, ensure_ascii=False), encoding="utf-8")
    env = {**os.environ, "TZ": tz}
    out = subprocess.run([NODE, "-e", RUNNER, str(WIDGET), str(path)],
                         capture_output=True, text=True, encoding="utf-8", check=True, env=env)
    return json.loads(out.stdout)
