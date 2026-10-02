# -*- coding: utf-8 -*-
"""A cached answer on the widget must say when it was true.

When a fetch fails, the widget falls back to its last saved analysis, whose
live figure is frozen at the moment it was saved. Unmarked, a broken session
would leave yesterday's figure on the home screen all day, read as today's.

These run the widget's own run() with the network failing, the way it fails on
a phone, and look at what reaches the screen and what Siri is handed.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WIDGET = ROOT / "ios" / "HilanWidget.js"

RUNNER = r"""
const fs = require('fs'), vm = require('vm');
const [widgetPath, htmlPath, mode] = process.argv.slice(1);
const code = fs.readFileSync(widgetPath, 'utf-8').replace(/^await run\(\);\s*$/m, '')
  .replace(/^const BASE_URL = .*$/m, 'const BASE_URL = "https://example.net.hilan.co.il";');
const texts = []; let output = null, cached = null;
class W { constructor() { this.url = ''; } setPadding() {} addText(t) { texts.push(t); return {}; }
  addStack() { return new W(); } addSpacer() {} layoutHorizontally() {} layoutVertically() {}
  presentMedium() { return Promise.resolve(); } }
const ctx = { console: { log() {}, error() {} },
  config: mode === 'widget' ? { runsInWidget: true, runsInApp: false, widgetFamily: 'medium' }
        : mode === 'lock'   ? { runsInWidget: true, runsInApp: false, widgetFamily: 'accessoryRectangular' }
        : mode === 'inline' ? { runsInWidget: true, runsInApp: false, widgetFamily: 'accessoryInline' }
        :                     { runsInWidget: false, runsInApp: false, runsWithSiri: true },
  FileManager: { local: () => ({ documentsDirectory: () => '/tmp', joinPath: (a, b) => a + '/' + b,
    fileExists: p => p.endsWith('hilan_last_analysis.json'),
    readString: () => JSON.stringify(cached), writeString: () => {} }) },
  Keychain: { contains: () => true, get: () => 'x', set: () => {} },
  Script: { complete() {}, setWidget() {}, setShortcutOutput(o) { output = o; } },
  Speech: { speak() {} },
  // The phone's view of a dropped connection.
  Request: class { constructor(u) { this.url = u; this.headers = {}; }
    loadString() { return Promise.reject(new Error('The network connection was lost.')); } },
  Alert: class { addAction() {} presentAlert() { return Promise.resolve(0); } },
  ListWidget: W, Font: { systemFont: s => s, boldSystemFont: s => s },
  Color: class { static gray() { return new this(); } static white() { return new this(); } },
};
vm.createContext(ctx);
vm.runInContext(code, ctx);
// Saved at 14:02 in Israel; the phone running this is elsewhere (TZ below).
const savedAt = new Date("2026-09-22T11:02:00Z");
cached = { ...ctx.parseAndAnalyse(fs.readFileSync(htmlPath, 'utf-8'), ctx.israelNow(savedAt)),
           cachedAt: savedAt.toISOString() };
ctx.run().then(() => process.stdout.write(JSON.stringify({ texts, output })));
"""

# Found once, by full path: on Windows a bare "node" is not always resolved.
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def run_widget(tmp_path, html, mode):
    page = tmp_path / "page.html"
    page.write_text(html, encoding="utf-8")
    # A phone set to another timezone must still say the time as it was in Israel.
    out = subprocess.run([NODE, "-e", RUNNER, str(WIDGET), str(page), mode],
                         capture_output=True, text=True, encoding="utf-8", check=True,
                         env={**os.environ, "TZ": "America/New_York"})
    return json.loads(out.stdout)


class TestTheHomeScreen:
    def test_it_says_the_figure_is_old(self, tmp_path, september_html):
        shown = " | ".join(run_widget(tmp_path, september_html, "widget")["texts"])
        assert "as of" in shown and "14:02" in shown, shown

    def test_it_says_why(self, tmp_path, september_html):
        shown = " | ".join(run_widget(tmp_path, september_html, "widget")["texts"])
        assert "Hilan unreachable" in shown, shown

    def test_the_lock_screen_is_marked_too(self, tmp_path, september_html):
        shown = " | ".join(run_widget(tmp_path, september_html, "lock")["texts"])
        assert "as of" in shown, shown

    def test_inline_keeps_the_number_and_the_time_on_one_line(self, tmp_path, september_html):
        """Inline shows one line; a marker of its own would push the number out."""
        first = run_widget(tmp_path, september_html, "inline")["texts"][0]
        assert first.startswith("Hilan: ") and "as of" in first, first


class TestWhatSiriIsHanded:
    def test_it_does_not_say_right_now(self, tmp_path, september_html):
        said = run_widget(tmp_path, september_html, "siri")["output"]
        assert "right now" not in said, said
        assert "as of" in said and "14:02" in said, said
