# -*- coding: utf-8 -*-
"""The widget must be told which Hilan site to use, and use nothing else.

Every company has its own Hilan address, so the script ships with a
placeholder. Until it is replaced the widget has to say what to do — and the
password, which goes to that address, must not be sent anywhere at all.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

WIDGET = Path(__file__).resolve().parent.parent / "ios" / "HilanWidget.js"

RUNNER = r"""
const fs = require('fs'), vm = require('vm');
const [widgetPath, mode, url] = process.argv.slice(1);
let code = fs.readFileSync(widgetPath, 'utf-8').replace(/^await run\(\);\s*$/m, '');
if (url !== 'as-shipped') code = code.replace(/^const BASE_URL = .*$/m, `const BASE_URL = ${JSON.stringify(url)};`);
const texts = [], requested = []; let output = null;
class W { constructor() { this.url = ''; } setPadding() {} addText(t) { texts.push(t); return {}; }
  addStack() { return new W(); } addSpacer() {} layoutHorizontally() {} layoutVertically() {}
  presentMedium() { return Promise.resolve(); } }
const ctx = { console: { log() {}, error() {} },
  config: mode === 'widget' ? { runsInWidget: true, runsInApp: false, widgetFamily: 'medium' }
        :                     { runsInWidget: false, runsInApp: false, runsWithSiri: true },
  FileManager: { local: () => ({ documentsDirectory: () => '/tmp', joinPath: (a, b) => a + '/' + b,
    fileExists: () => false, readString: () => '', writeString: () => {} }) },
  Keychain: { contains: () => true, get: () => 'x', set: () => {} },
  Script: { complete() {}, setWidget() {}, setShortcutOutput(o) { output = o; } },
  Speech: { speak() {} },
  Request: class { constructor(u) { requested.push(u); this.url = u; this.headers = {}; }
    loadString() { return Promise.reject(new Error('offline')); } },
  Alert: class { addAction() {} presentAlert() { return Promise.resolve(0); } },
  ListWidget: W, Font: { systemFont: s => s, boldSystemFont: s => s },
  Color: class { static gray() { return new this(); } static white() { return new this(); } },
};
vm.createContext(ctx);
vm.runInContext(code, ctx);
ctx.run().then(() => process.stdout.write(JSON.stringify({ texts, output, requested })));
"""

# Found once, by full path: on Windows a bare "node" is not always resolved.
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def run_widget(mode, url):
    out = subprocess.run([NODE, "-e", RUNNER, str(WIDGET), mode, url],
                         capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(out.stdout)


class TestAsShipped:
    def test_the_home_screen_says_what_to_set(self):
        shown = " | ".join(run_widget("widget", "as-shipped")["texts"])
        assert "Set BASE_URL" in shown, shown

    def test_siri_says_what_to_set(self):
        assert "Set BASE_URL" in run_widget("siri", "as-shipped")["output"]

    def test_nothing_is_requested(self):
        assert run_widget("widget", "as-shipped")["requested"] == []

    def test_the_placeholder_is_refused_in_any_case(self):
        result = run_widget("widget", "https://your-company.net.hilan.co.il")
        assert result["requested"] == []


class TestAnAddressThatIsNotHilan:
    @pytest.mark.parametrize("url", [
        "https://example.com",
        "http://example.net.hilan.co.il",              # the password in clear text
        "https://example.net.hilan.co.il.evil.com",
        "https://evil.com/.hilan.co.il",
        "https://example.net.hilan.co.il/login",       # a path would be glued to every request
        "",
    ])
    def test_nothing_is_requested(self, url):
        result = run_widget("widget", url)
        assert result["requested"] == [], result
        assert "Set BASE_URL" in " | ".join(result["texts"])


class TestASetAddress:
    @pytest.mark.parametrize("url", [
        "https://example.net.hilan.co.il",
        "https://Example.net.hilan.co.il",             # hosts are not case-sensitive
    ])
    def test_it_is_used(self, url):
        requested = run_widget("widget", url)["requested"]
        assert requested and all(u.startswith(url) for u in requested), requested
