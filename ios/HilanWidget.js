// Variables used by Scriptable.
// These must be at the very top of the file. Do not edit.
// icon-color: blue; icon-glyph: clock;

/**
 * Hilan Hours for Scriptable (iOS): the live and banked balance on the home
 * screen and lock screen, in every widget size, and in one sentence for Siri
 * through a Shortcut's "Run Script" action. No Mac needs to be running.
 *
 * It is the command line's calculation again, in JavaScript, and the tests
 * hold the two to the same numbers. It only ever reads: the same guard as the
 * command line refuses anything that is not a read. The password stays in the
 * iOS Keychain, and the session is kept between refreshes so that Hilan is
 * logged in to rarely — which is also what keeps its SMS challenge away.
 */

// Your company's Hilan site — the address you log in at, without the path.
const BASE_URL = "https://YOUR-COMPANY.net.hilan.co.il";
const LOGIN_PAGE_PATH = "/login";
const LOGIN_PATH = "/HilanCenter/Public/api/LoginApi/LoginRequest";
const ATTENDANCE_PATH = "/Hilannetv2/Attendance/calendarpage.aspx";
const ATTENDANCE_MARKER = "calendar_container";

const KEYCHAIN_USER_KEY = "hilan_username";
const KEYCHAIN_PASS_KEY = "hilan_password";
const COOKIE_FILE_NAME = "hilan_cookies.json";
const CACHE_FILE_NAME = "hilan_last_analysis.json";

// Guard: forbid modifying buttons
const FORBIDDEN_FIELDS = ["btnSave", "btnClear", "btnPickStepProject", "CollectiveAttendance"];
const READ_ACTIONS = ["RefreshSelectedDays", "RefreshPeriod", "RefreshErrorsDays"];

// --- Storage Helpers ---
const fm = FileManager.local();
const cookieFilePath = fm.joinPath(fm.documentsDirectory(), COOKIE_FILE_NAME);
const cacheFilePath = fm.joinPath(fm.documentsDirectory(), CACHE_FILE_NAME);
const holdFilePath = fm.joinPath(fm.documentsDirectory(), "hilan_login_refused");

// Cookies are kept with their path, like client.py's jar: Hilan's load balancer
// sets the same name on several paths, and one value per name would send the
// wrong one.
function loadSavedCookies() {
  if (!fm.fileExists(cookieFilePath)) return [];
  try {
    const stored = JSON.parse(fm.readString(cookieFilePath));
    if (Array.isArray(stored)) {
      return stored
        .filter(c => c && typeof c.name === "string" && c.value != null)
        .map(c => ({ name: c.name, value: String(c.value), path: c.path || "/", host: c.host || "" }));
    }
  } catch (e) {}
  return [];
}

function saveCookies(cookies) {
  try {
    fm.writeString(cookieFilePath, JSON.stringify(cookies));
  } catch (e) {}
}

// A cached analysis is used only if it has the shape the screen needs; anything
// else counts as no cache, so the real error shows instead of a crash about it.
function loadLastCache() {
  if (!fm.fileExists(cacheFilePath)) return null;
  try {
    const a = JSON.parse(fm.readString(cacheFilePath));
    const shaped = a && typeof a === "object" && a.live && a.today && a.banked && a.monthTotals
      && typeof a.live.balance === "string"
      && (a.cachedAt == null || !isNaN(new Date(a.cachedAt).getTime()));
    return shaped ? a : null;
  } catch (e) {
    return null;
  }
}

function saveCache(data) {
  try {
    fm.writeString(cacheFilePath, JSON.stringify(data));
  } catch (e) {}
}

// Some answers mean the widget must stop logging in on its own: a password
// Hilan has refused (every refresh would retry it, and that is how an account
// gets locked), or a verification code Hilan wants (every refresh would send
// another one). Running the script in the app deals with either and lifts it.
function loginHold() {
  if (!fm.fileExists(holdFilePath)) return null;
  try {
    const held = JSON.parse(fm.readString(holdFilePath));
    if (held && held.kind === "verification") return { kind: "verification", reason: String(held.reason || "") };
  } catch (e) {}
  // Anything else, readable or not, counts as a refusal: the safer reading.
  return { kind: "refused", reason: "" };
}

function markLoginHold(kind, reason) {
  try {
    fm.writeString(holdFilePath, JSON.stringify({ kind, reason: reason || "" }));
  } catch (e) {}
}

function clearLoginHold() {
  try {
    if (fm.fileExists(holdFilePath)) fm.remove(holdFilePath);
  } catch (e) {}
}

// The widget will not log in on its own: Hilan refused the password, or wants
// a code typed in. `kind` says which, so the screen can say what to do.
class LoginRefused extends Error {
  constructor(message, kind = "refused") {
    super(message);
    this.name = "LoginRefused";
    this.kind = kind;
  }
}

// Hilan asked for the code it has just sent by SMS.
class VerificationNeeded extends Error {
  constructor(message) {
    super(message);
    this.name = "VerificationNeeded";
  }
}

// Hilan's clock is Israel time, wherever the phone is: the wall-clock fields
// in Israel at that instant. Not a Date — a Date built from them would land in
// the phone's own timezone, and in the hour its clocks spring forward that
// hour does not exist, so 02:30 would read as 03:30.
function israelNow(instant = new Date()) {
  let f;
  try {
    const parts = new Intl.DateTimeFormat("en-GB", {
      timeZone: "Asia/Jerusalem", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    }).formatToParts(instant);
    const get = type => parseInt(parts.find(p => p.type === type).value, 10);
    f = { y: get("year"), mo: get("month"), d: get("day"), h: get("hour") % 24, mi: get("minute") };
  } catch (e) {
    const t = new Date(instant.getTime());
    f = { y: t.getFullYear(), mo: t.getMonth() + 1, d: t.getDate(), h: t.getHours(), mi: t.getMinutes() };
  }
  const pad = n => String(n).padStart(2, "0");
  const day = `${f.y}-${pad(f.mo)}-${pad(f.d)}`;
  return {
    getFullYear: () => f.y, getMonth: () => f.mo - 1, getDate: () => f.d,
    getHours: () => f.h, getMinutes: () => f.mi,
    toDateString: () => day,
    toString: () => `${day} ${pad(f.h)}:${pad(f.mi)}`,
  };
}

// --- Credentials ---
async function getCredentials() {
  let user = Keychain.contains(KEYCHAIN_USER_KEY) ? Keychain.get(KEYCHAIN_USER_KEY) : null;
  let pass = Keychain.contains(KEYCHAIN_PASS_KEY) ? Keychain.get(KEYCHAIN_PASS_KEY) : null;
  const hold = loginHold();

  if (hold && !config.runsInApp) {
    throw hold.kind === "verification"
      ? new LoginRefused("Hilan wants a verification code — run the script in Scriptable to enter it", "verification")
      : new LoginRefused("Hilan refused the password — run the script in Scriptable to enter it again");
  }
  const refused = hold && hold.kind === "refused";
  if (!user || !pass || refused) {
    if (!config.runsInApp) {
      throw new LoginRefused("No Hilan credentials yet — run the script in Scriptable once to enter them", "setup");
    }
    const alert = new Alert();
    alert.title = "Hilan Credentials";
    alert.message = refused
      ? "Hilan refused the stored password. Enter your employee number and password again."
      : "Enter your Hilan employee number and password once. They are kept in the iOS Keychain.";
    alert.addTextField("Employee Number (e.g. 12345)", user || "");
    alert.addSecureTextField("Password", "");
    alert.addAction("Save");
    alert.addCancelAction("Cancel");
    const idx = await alert.presentAlert();
    if (idx !== 0) throw new Error("Login cancelled");
    user = alert.textFieldValue(0).trim();
    // Not trimmed: a password is whatever was typed, spaces included.
    pass = alert.textFieldValue(1);
    if (!user || !pass) throw new Error("Missing username or password");
    Keychain.set(KEYCHAIN_USER_KEY, user);
    Keychain.set(KEYCHAIN_PASS_KEY, pass);
    // A new password has not been refused: the stop was about the old one. If
    // Hilan refuses this one too, the stop comes back.
    if (refused) clearLoginHold();
  }
  return { user, pass };
}

// The code Hilan has just sent by SMS, typed in the app.
async function askForCode(message) {
  const alert = new Alert();
  alert.title = "Hilan Verification";
  alert.message = message || "Hilan sent a verification code. Enter it to sign in.";
  alert.addTextField("Code", "");
  alert.addAction("Sign in");
  alert.addCancelAction("Cancel");
  const idx = await alert.presentAlert();
  const code = idx === 0 ? alert.textFieldValue(0).trim() : "";
  if (!code) throw new Error("Login cancelled");
  return code;
}

// --- Date & Serial Math ---
function dateToSerial(year, month, day) {
  const d = new Date(Date.UTC(year, month - 1, day));
  const epoch = new Date(Date.UTC(2000, 0, 1));
  return Math.round((d - epoch) / 86400000);
}


function daysInMonth(year, month) {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

function selectedDaysField(year, month) {
  const total = daysInMonth(year, month);
  const first = dateToSerial(year, month, 1);
  const res = [];
  for (let i = 0; i < total; i++) res.push(first + i);
  return res.join(',');
}

function dailyStandard(year, month, day, special) {
  const d = new Date(Date.UTC(year, month - 1, day));
  const weekday = d.getUTCDay(); // 0 is Sunday, 4 is Thu, 5 is Fri, 6 is Sat
  if (weekday === 5 || weekday === 6) return 0;
  if (special) {
    if (special === "חג") return 0;
    if (special.startsWith("ערב")) return 4.0;
  }
  return weekday === 4 ? 8.5 : 9.0;
}

// Hours as whole minutes, half a minute rounded away from zero like calc.py's
// _minutes; the nudge keeps 8.325 × 60 from landing a hair under 499.5.
function minutesOf(hours) {
  return Math.sign(hours) * Math.round(Math.abs(hours) * 60 + 1e-9);
}

function roundHours(minutes) {
  return Math.round((minutes / 60) * 100) / 100;
}


// One level of HTML entities, the way an HTML parser decodes an attribute.
// Hilan escapes an empty cell twice, so its ov arrives as "&amp;nbsp;" and
// decodes to the literal text "&nbsp;" — which isBlank then recognises.
function decodeEntities(text) {
  if (text == null) return text;
  return text.replace(/&(#x[0-9a-f]+|#\d+|quot|apos|lt|gt|nbsp|amp);/gi, (whole, name) => {
    const n = name.toLowerCase();
    if (n.startsWith('#x')) return String.fromCodePoint(parseInt(n.slice(2), 16));
    if (n.startsWith('#')) return String.fromCodePoint(parseInt(n.slice(1), 10));
    return { quot: '"', apos: "'", lt: '<', gt: '>', nbsp: '\u00a0', amp: '&' }[n];
  });
}

// The same four readers as parser.py, so both sides see the same page.
function isBlank(val) {
  return val == null || ['', '&nbsp;'].includes(val.replace(/\u00a0/g, '').trim());
}

function parseTime(val) {
  if (isBlank(val)) return null;
  const m = val.trim().match(/^(\d{1,2}):(\d{2})$/);
  if (!m) return null;
  const h = parseInt(m[1], 10);
  const min = parseInt(m[2], 10);
  if (h > 24 || min >= 60) return null;
  return { hour: h % 24, minute: min, str: `${String(h % 24).padStart(2, '0')}:${String(min).padStart(2, '0')}` };
}

function parseDurationMinutes(val) {
  if (isBlank(val)) return null;
  const m = val.trim().match(/^(\d{1,2}):(\d{2})$/);
  if (!m) return null;
  return parseInt(m[1], 10) * 60 + parseInt(m[2], 10);
}

function parseDecimal(val) {
  if (isBlank(val)) return null;
  let text = val.trim();
  // Right-to-left pages often write a negative number as 12.50-.
  if (text.endsWith('-') && !text.startsWith('-')) text = '-' + text.slice(0, -1);
  return /^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/.test(text) ? parseFloat(text) : null;
}

function segmentMinutes(entry, exit, totalMinutes) {
  if (totalMinutes != null) return totalMinutes;
  if (!entry || !exit) return null;
  let start = entry.hour * 60 + entry.minute;
  let end = exit.hour * 60 + exit.minute;
  if (end < start) end += 24 * 60;
  return end - start;
}

const WORK_SYMBOLS = new Set(["נוכחות", "נכח", "עבודה מהבית", "עבודה במילואים", "מפגש עובד מנהל"]);
function isAbsence(symbol) {
  return Boolean(symbol && !WORK_SYMBOLS.has(symbol));
}

// --- Hilan HTTP Client ---
//: Every field the month read sends; an attendance POST with anything else is refused.
const READ_FIELDS = new Set([
  "__EVENTTARGET", "__EVENTARGUMENT", "__LASTFOCUS", "Time", "DisableTimeout",
  "__VIEWSTATE", "__VIEWSTATEGENERATOR", "H-XSRF-Token", "__calendarSelectedDays",
  "ctl00$mp$Strip$hCurrentItemId", "ctl00$mp$currentMonth", "__EVENTVALIDATION",
  ...READ_ACTIONS.map(a => `ctl00$mp$${a}`),
]);

//: Every field the login sends. Matched exactly: Hilan binds form fields
//: whatever their case, so "IsChangePassword" must not pass for another field.
const LOGIN_FIELDS = new Set([
  "orgId", "username", "password", "isChangePassword", "newPassword", "id", "isEn",
  "verificationCode", "saveBrowser",
]);

//: What makes an ASP.NET request a postback, wherever it is carried.
const POSTBACK_FIELDS = ["__viewstate", "__eventtarget", "__eventargument", "__eventvalidation",
                         "__callbackid", "__callbackparam", "__previouspage", "__viewstatefieldcount",
                         "__viewstategenerator", "__lastfocus", "__scrollpositionx", "__scrollpositiony"];

const USER_AGENT = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15";

// Scheme, host, port, path and query, or null. Anchored at both ends and with
// no "@" or "\" allowed in the host, so "https://site:@elsewhere/" is not read
// as the site: iOS would send that request to "elsewhere".
function parseUrl(url) {
  const m = /^([a-z][a-z0-9+.-]*):\/\/([^/?#:@\\]+)(?::(\d*))?(\/[^?#]*)?(\?[^#]*)?(#.*)?$/i.exec(String(url || ""));
  return m ? { scheme: m[1].toLowerCase(), host: m[2].toLowerCase(), port: m[3] || "",
               path: m[4] || "/", query: m[5] ? m[5].slice(1) : "" } : null;
}

// The names and values in a query string, decoded and lower-cased.
function queryPairs(query) {
  const decode = s => {
    const plain = s.replace(/\+/g, " ");
    try { return decodeURIComponent(plain).toLowerCase(); } catch (e) {
      // One bad escape must not leave the good ones undecoded (btn%53ave):
      // decode each %XX on its own, as client.py's unquote does.
      return plain.replace(/%([0-9a-f]{2})/gi, (m, h) => String.fromCharCode(parseInt(h, 16))).toLowerCase();
    }
  };
  return String(query || "").split("&").filter(Boolean).map(part => {
    const eq = part.indexOf("=");
    return eq < 0 ? [decode(part), ""] : [decode(part.slice(0, eq)), decode(part.slice(eq + 1))];
  });
}

function queryKeys(query) {
  return queryPairs(query).map(([key]) => key);
}

// ASP.NET reads fields from the query string as well as the body, and treats a
// GET carrying postback fields as a postback: no query may carry either. It
// also decodes %uXXXX escapes, which nothing here does, so none may pass.
function checkQuery(query, where) {
  if (/%u/i.test(String(query || ""))) {
    throw new Error(`Guard refused ${where} whose query string carries a %u escape`);
  }
  for (const [key, value] of queryPairs(query)) {
    if (POSTBACK_FIELDS.includes(key)
        || FORBIDDEN_FIELDS.some(f => key.includes(f.toLowerCase()) || value.includes(f.toLowerCase()))) {
      throw new Error(`Guard refused ${where} whose query string carries ${key}`);
    }
  }
}

// RFC 6265: a cookie set for /Hilan is not one for /Hilannetv2.
function pathMatches(cookiePath, requestPath) {
  if (requestPath === cookiePath) return true;
  if (!requestPath.startsWith(cookiePath)) return false;
  return cookiePath.endsWith("/") || requestPath[cookiePath.length] === "/";
}

// The path a cookie without a Path attribute belongs to: the request's folder.
function defaultCookiePath(requestPath) {
  const cut = String(requestPath || "/").lastIndexOf("/");
  return cut <= 0 ? "/" : requestPath.slice(0, cut);
}

class HilanClient {
  constructor() {
    // iOS gives a widget refresh tens of seconds in all; past that it is killed
    // before the cached figure can be shown. Every request shares this budget.
    this.deadline = Date.now() + 25 * 1000;
    this.base = parseUrl(BASE_URL);
    // Only this site's: a session from another company's Hilan is not sent here.
    this.cookies = loadSavedCookies().filter(c => c.host === this.base.host);
    this.refusedRedirect = null;
  }

  _timeout() {
    // A widget is given seconds, not minutes: fail early enough to show the
    // cached figure rather than be killed with the old snapshot still up.
    if (!config.runsInWidget) return 90;
    const left = (this.deadline - Date.now()) / 1000;
    if (left < 2) throw new Error("Hilan is too slow to answer within a widget's time");
    return Math.min(20, left);
  }

  _applyCookies(req, path) {
    // Every cookie whose path matches, the longer paths first — what a browser
    // sends (RFC 6265 §5.4), same names included.
    const pairs = this.cookies
      .filter(c => c.value && pathMatches(c.path || "/", path))
      .sort((a, b) => (b.path || "/").length - (a.path || "/").length)
      .map(c => `${c.name}=${c.value}`);
    if (pairs.length > 0) {
      req.headers = { ...(req.headers || {}), "Cookie": pairs.join("; ") };
    }
  }

  // Only this site's cookies are kept: one set for another domain is not ours
  // to send back to Hilan.
  _ownDomain(domain) {
    if (!domain) return true;
    const d = String(domain).toLowerCase().replace(/^\./, "");
    return this.base.host === d || this.base.host.endsWith("." + d);
  }

  _storeCookies(response, requestPath) {
    if (!response) return;
    // An expiry already past is the server deleting the cookie (iOS calls the
    // field expiresDate).
    const expired = c => [c.expiresDate, c.expires].some(d => d != null && Date.parse(d) <= Date.now());
    let found = (Array.isArray(response.cookies) ? response.cookies : [])
      .filter(c => c && this._ownDomain(c.domain))
      .map(c => (expired(c) ? { ...c, value: "" } : c));
    if (found.length === 0 && response.headers) {
      // Only if the parsed list is missing: iOS joins several Set-Cookie headers
      // with ", ", so split where the next "name=" begins, not inside a date.
      for (const [key, val] of Object.entries(response.headers)) {
        if (key.toLowerCase() !== "set-cookie") continue;
        for (const item of String(val).split(/,(?=\s*[!#$%&'*+\-.^_`|~0-9A-Za-z]+=)/)) {
          const [pair, ...attrs] = item.split(";");
          const eq = pair.indexOf("=");
          if (eq <= 0) continue;
          const attr = name => {
            const a = attrs.map(x => x.trim()).find(x => x.toLowerCase().startsWith(name + "="));
            return a == null ? null : a.slice(name.length + 1).trim();
          };
          if (!this._ownDomain(attr("domain"))) continue;
          const maxAge = attr("max-age");
          const expires = attr("expires");
          const path = attr("path");
          // Max-Age of zero or less, or an Expires already past, is the server deleting it.
          const gone = (maxAge != null && parseInt(maxAge, 10) <= 0)
            || (maxAge == null && expires != null && Date.parse(expires) <= Date.now());
          found.push({ name: pair.slice(0, eq).trim(),
                       value: gone ? "" : pair.slice(eq + 1).trim(),
                       path: path && path.startsWith("/") ? path : defaultCookiePath(requestPath) });
        }
      }
    }
    if (found.length === 0) return;
    for (const c of found) {
      if (!c.name) continue;
      const path = c.path || "/";
      this.cookies = this.cookies.filter(k => !(k.name === c.name && (k.path || "/") === path));
      // An empty value is the server clearing the cookie.
      if (c.value != null && c.value !== "") {
        this.cookies.push({ name: c.name, value: String(c.value), path, host: this.base.host });
      }
    }
    saveCookies(this.cookies);
  }

  // Like client.py's guard on every redirect hop: https, this site, the
  // standard port, no postback in the query — and a POST carried along may not
  // change its path. Cookies are added only once a hop has passed.
  _onRedirect(originalPath) {
    return (redirectReq) => {
      const target = parseUrl(redirectReq.url);
      const resent = String(redirectReq.method || "GET").toUpperCase() === "POST";
      let allowed = target && target.scheme === "https" && target.host === this.base.host
        && (target.port === "" || target.port === "443")
        && !(resent && target.path !== originalPath)
        && !/%u/i.test(target.path);
      if (allowed) {
        try { checkQuery(target.query, "a redirect"); } catch (e) { allowed = false; }
      }
      // A POST carried along keeps the query rules of its page, as post() set them.
      if (allowed && resent) {
        const keys = queryKeys(target.query);
        if (originalPath === LOGIN_PATH && keys.length > 0) allowed = false;
        if (originalPath === ATTENDANCE_PATH && keys.some(k => k !== "isonself")) allowed = false;
      }
      if (!allowed) {
        this.refusedRedirect = redirectReq.url;
        return null;
      }
      this._applyCookies(redirectReq, target.path);
      return redirectReq;
    };
  }

  async _send(req, path) {
    this.refusedRedirect = null;
    req.onRedirect = this._onRedirect(path);
    const body = await req.loadString();
    if (this.refusedRedirect) {
      const where = this.refusedRedirect;
      this.refusedRedirect = null;
      throw new Error(`Guard refused a redirect to ${where}`);
    }
    const final = parseUrl(req.response && req.response.url) || { path };
    this._storeCookies(req.response, final.path);
    return { status: req.response.statusCode, text: body, path: final.path };
  }

  async get(urlPath, params = {}) {
    let url = BASE_URL + urlPath;
    const query = Object.entries(params).map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`).join("&");
    if (query) url += (url.includes('?') ? '&' : '?') + query;
    const target = parseUrl(url);
    if (/%u/i.test(url)) throw new Error("Guard refused a GET carrying a %u escape");
    checkQuery(target && target.query, "a GET");

    const req = new Request(url);
    req.method = "GET";
    req.timeoutInterval = this._timeout();
    req.headers = { "User-Agent": USER_AGENT, "Accept-Language": "he-IL,he;q=0.9,en;q=0.8" };
    const path = urlPath.split("?")[0];
    this._applyCookies(req, path);
    return this._send(req, path);
  }

  async post(urlPath, data = {}) {
    const url = BASE_URL + urlPath;
    // The same rules as client.py's guard: only what is recognisably a read
    // leaves, rather than only refusing what is recognisably a write.
    for (const [key, value] of Object.entries(data)) {
      if (typeof value !== "string") {
        throw new Error(`Guard refused a POST whose ${key} is not text`);
      }
    }
    const bodyStr = Object.entries(data)
      .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
      .join("&");

    const [path, query = ""] = urlPath.split("?");
    const keys = queryKeys(query);
    checkQuery(query, "a POST");
    if (/%|\.\.|\/\//.test(path)) {
      throw new Error(`Guard refused a POST to an unusual path ${path}`);
    }
    if (path === LOGIN_PATH) {
      if (keys.length > 0) throw new Error("Guard refused a login with a query string");
      const unexpected = Object.keys(data).filter(k => !LOGIN_FIELDS.has(k));
      if (unexpected.length > 0) {
        throw new Error(`Guard refused a login carrying ${unexpected.join(", ")}`);
      }
      const changing = (data.isChangePassword != null && data.isChangePassword !== "false")
        || (data.newPassword != null && data.newPassword !== "");
      if (changing) throw new Error("Guard refused a login that changes the password");
    } else if (path === ATTENDANCE_PATH) {
      if (keys.some(k => k !== "isonself")) {
        throw new Error("Guard refused an attendance POST with an unexpected query string");
      }
      const decoded = Object.entries(data).map(([k, v]) => `${k}=${v}`).join("&").toLowerCase();
      for (const field of FORBIDDEN_FIELDS) {
        if (decoded.includes(field.toLowerCase())) {
          throw new Error(`Guard rejected request containing mutating field ${field}`);
        }
      }
      const unexpected = Object.keys(data).filter(k => !READ_FIELDS.has(k));
      if (unexpected.length > 0) {
        throw new Error(`Guard refused an attendance POST carrying ${unexpected.join(", ")}`);
      }
      if (data.__EVENTTARGET) {
        throw new Error("Guard refused an attendance POST that names a postback target");
      }
      if (READ_ACTIONS.filter(a => `ctl00$mp$${a}` in data).length !== 1) {
        throw new Error("Guard refused an attendance POST without exactly one known read action");
      }
    } else {
      throw new Error(`Guard refused a POST to ${path}: only login and the attendance read may be sent`);
    }

    const req = new Request(url);
    req.method = "POST";
    req.timeoutInterval = this._timeout();
    const headers = {
      "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
      "User-Agent": USER_AGENT,
      "Accept-Language": "he-IL,he;q=0.9,en;q=0.8",
    };
    if (path === LOGIN_PATH) headers["X-Requested-With"] = "XMLHttpRequest";
    req.headers = headers;
    this._applyCookies(req, path);
    req.body = bodyStr;
    return this._send(req, path);
  }

  async login(username, password, code = null) {
    const loginPage = await this.get(LOGIN_PAGE_PATH);
    if (loginPage.status === 403) {
      throw new Error("Hilan's firewall refused the phone (403) — wait a while before retrying");
    }
    if (loginPage.status >= 400) {
      throw new Error(`Hilan answered ${loginPage.status} — try again later`);
    }
    const orgMatch = loginPage.text.match(/OrgId\\*"\s*:\s*\\*"(\d+)/) || loginPage.text.match(/OrgId["'\s:]+(\d+)/i);
    // Like client.py: better a try with an empty orgId than someone else's.
    const orgId = orgMatch ? orgMatch[1] : "";

    const payload = {
      orgId: orgId,
      username: username,
      password: password,
      isChangePassword: "false",
      newPassword: "",
      id: "",
      isEn: "false",
    };
    if (code) {
      payload.verificationCode = code;
      payload.saveBrowser = "true";
    }
    const res = await this.post(LOGIN_PATH, payload);
    // Unauthorised, from the endpoint that judges the password, is a no to it.
    if (res.status === 401) throw new LoginRefused("Hilan refused the login (401)");
    if (res.status === 403) {
      throw new Error("Hilan's firewall refused the phone (403) — wait a while before retrying");
    }
    // A server error says nothing about the password, whatever its body says.
    if (res.status >= 400) {
      throw new Error(`Hilan's login answered ${res.status} — try again later`);
    }
    let json;
    try {
      json = JSON.parse(res.text);
    } catch (e) {
      throw new Error(`Hilan's login answered ${res.status} without JSON — try again later`);
    }
    // Accepted only when Hilan says so, as in client.py: an answer of another
    // shape taken for success would lift the stop and send the password again.
    if (json && json.IsFail === false) return;
    if (!json || json.IsFail !== true) {
      throw new LoginRefused("Hilan answered the login in a form this widget does not recognise");
    }
    if (json.IsShowVerificationCode || json.IsShowVerification) {
      throw new VerificationNeeded(json.VerificationCodeSentText || "Hilan sent a verification code. Enter it to sign in.");
    }
    throw new LoginRefused(json.ErrorMessage || `Hilan refused the login (code ${json.Code})`);
  }

  // Log in, and stop logging in on its own after an answer that retrying
  // cannot fix. In the app, a verification code is asked for and sent.
  async _logIn() {
    const creds = await getCredentials();
    try {
      await this.login(creds.user, creds.pass);
    } catch (e) {
      if (e instanceof LoginRefused) {
        markLoginHold("refused", e.message);
        try { Keychain.remove(KEYCHAIN_PASS_KEY); } catch (removeError) {}
        throw e;
      }
      if (!(e instanceof VerificationNeeded)) throw e;
      // Held until a code goes through: each automatic try would send an SMS.
      markLoginHold("verification", e.message);
      if (!config.runsInApp) {
        throw new LoginRefused("Hilan wants a verification code — run the script in Scriptable to enter it", "verification");
      }
      const code = await askForCode(e.message);
      try {
        await this.login(creds.user, creds.pass, code);
      } catch (again) {
        if (again instanceof LoginRefused || again instanceof VerificationNeeded) {
          // A wrong code is not a wrong password: the password stays.
          throw new LoginRefused("Hilan did not accept the code — run the script again for a new one", "verification");
        }
        throw again;
      }
    }
    clearLoginHold();
  }

  // Like client.py: log in only when the session is really gone — refused, or
  // sent back to /login. A server error is an error, not a reason to send the
  // password again.
  _needsLogin(page) {
    return page.status === 401 || page.status === 403
      || (page.status < 400 && (page.path !== ATTENDANCE_PATH || !page.text.includes(ATTENDANCE_MARKER)));
  }

  async fetchMonth(year, month) {
    let page = await this.get(ATTENDANCE_PATH, { isOnSelf: "true" });
    if (page.status >= 400 && page.status !== 401 && page.status !== 403) {
      throw new Error(`Hilan answered ${page.status} — try again later`);
    }
    if (this._needsLogin(page)) {
      await this._logIn();
      page = await this.get(ATTENDANCE_PATH, { isOnSelf: "true" });
      if (page.status >= 400 || this._needsLogin(page)) {
        throw new Error("Hilan did not keep the session after logging in — try again later");
      }
    }

    // Hidden ASP.NET fields, decoded once as a browser would before sending.
    function getHidden(name) {
      const escaped = name.replace(/\$/g, '\\$');
      const m1 = page.text.match(new RegExp(`\\bname="${escaped}"[^>]*\\bvalue="([^"]*)"`, 'i'));
      if (m1) return decodeEntities(m1[1]);
      const m2 = page.text.match(new RegExp(`\\bvalue="([^"]*)"[^>]*\\bname="${escaped}"`, 'i'));
      return m2 ? decodeEntities(m2[1]) : "";
    }

    const postData = {
      "__EVENTTARGET": "",
      "__EVENTARGUMENT": "",
      "__LASTFOCUS": "",
      "Time": getHidden("Time") || "9",
      "DisableTimeout": "true",
      "__VIEWSTATE": getHidden("__VIEWSTATE"),
      "__VIEWSTATEGENERATOR": getHidden("__VIEWSTATEGENERATOR"),
      "H-XSRF-Token": getHidden("H-XSRF-Token"),
      "__calendarSelectedDays": selectedDaysField(year, month),
      "ctl00$mp$Strip$hCurrentItemId": getHidden("ctl00$mp$Strip$hCurrentItemId"),
      "ctl00$mp$currentMonth": `01/${String(month).padStart(2, '0')}/${year}`,
      "ctl00$mp$RefreshSelectedDays": "ימים נבחרים",
    };
    // A page with ASP.NET event validation turned on refuses a postback that
    // does not hand its token back.
    const eventValidation = getHidden("__EVENTVALIDATION");
    if (eventValidation) postData["__EVENTVALIDATION"] = eventValidation;

    const monthPage = await this.post(ATTENDANCE_PATH + "?isOnSelf=true", postData);
    if (monthPage.status >= 400) {
      throw new Error(`Hilan answered ${monthPage.status} — try again later`);
    }
    if (monthPage.path !== ATTENDANCE_PATH || !monthPage.text.includes(ATTENDANCE_MARKER)) {
      throw new Error("The month did not come back from Hilan — try again later");
    }
    return monthPage.text;
  }
}

// --- Parsing and Analysis ---
// Like parser.py's _pick_grid: a page can carry more than one month's grid,
// and "whichever comes first" would silently report the wrong month.
function pickGrid(html) {
  const grids = [...new Set([...html.matchAll(/<table\b[^>]*\bid="([^"]*_reportsGrid_innerBody[^"]*)"/gi)]
    .map(m => m[1]))];
  if (grids.length === 0) throw new Error("attendance grid not found — is this an attendance page?");
  if (grids.length === 1) return grids[0];
  const cmMatch = html.match(/\bname="ctl00\$mp\$currentMonth"[^>]*\bvalue="([^"]*)"/i) ||
                  html.match(/\bvalue="([^"]*)"[^>]*\bname="ctl00\$mp\$currentMonth"/i);
  const cm = (cmMatch ? cmMatch[1] : "").match(/(\d{2})\/(\d{4})$/);
  if (cm) {
    const wanted = `_${cm[2]}_${cm[1]}_reportsGrid_innerBody`;
    const hit = grids.find(g => g.endsWith(wanted));
    if (hit) return hit;
  }
  throw new Error(`page carries ${grids.length} month grids and currentMonth does not identify one of them`);
}

function parseAndAnalyse(html, nowObj = israelNow()) {
  const gridId = pickGrid(html);
  const gridMatch = gridId.match(/RG_Days_(\d+)_(\d{4})_(\d{2})_reportsGrid_innerBody/);
  if (!gridMatch) throw new Error(`unexpected grid id ${gridId}`);
  const year = parseInt(gridMatch[2], 10);
  const month = parseInt(gridMatch[3], 10);
  const gid = gridId.replace("_reportsGrid_innerBody", "");

  // The whole element, markup inside it included: the date can follow a <b>.
  const syncMatch = html.match(/<([a-z0-9]+)\b[^>]*\bid="[^"]*LastUpdateLegendText"[^>]*>([\s\S]*?)<\/\1>/i);
  let syncDate = null;
  let syncDisplay = "";
  if (syncMatch) {
    const sm = syncMatch[2].replace(/<[^>]+>/g, " ").match(/(\d{2})\/(\d{2})\/(\d{4})\s+(\d{2}):(\d{2})/);
    if (sm) {
      syncDate = `${sm[3]}-${sm[2]}-${sm[1]}`;
      syncDisplay = `${sm[1]}/${sm[2]} ${sm[4]}:${sm[5]}`;
    }
  }

  function getAttr(tagAttrs, name) {
    const m = tagAttrs.match(new RegExp(`\\b${name}\\s*=\\s*(?:"([^"]*)"|'([^']*)')`, 'i'));
    return m ? decodeEntities(m[1] != null ? m[1] : m[2]) : null;
  }

  // Comments and scripts are not the page: BeautifulSoup never reads a <td>
  // inside them, so neither does this.
  html = html.replace(/<!--[\s\S]*?-->/g, "").replace(/<script\b[\s\S]*?<\/script>/gi, "");

  // 1. Scan all ov attributes regardless of attribute order
  let tm;
  const ovMap = new Map();
  const tagRegex = /<td\b([^>]*)>/gi;
  while ((tm = tagRegex.exec(html)) !== null) {
    const attrs = tm[1];
    const id = getAttr(attrs, "id");
    const ov = getAttr(attrs, "ov");
    if (id && ov != null) {
      ovMap.set(id, ov);
    }
  }

  // 2. Scan special cells with boundary lookahead
  const specialMap = new Map();
  // Scoped to this grid, like the ov cells: a page can carry another month's
  // rows, numbered from 0 just the same.
  const gidRe = gid.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const spRegex = new RegExp(`<td\\b[^>]*\\bid="${gidRe}_special_row_(\\d+)"[^>]*>([\\s\\S]*?)(?=<td\\b[^>]*\\bid=|<\\/tr>)`, 'gi');
  while ((tm = spRegex.exec(html)) !== null) {
    const row = parseInt(tm[1], 10);
    const raw = decodeEntities(tm[2].replace(/<[^>]+>/g, ' ')).replace(/\s+/g, ' ').trim();
    if (raw) specialMap.set(row, raw);
  }

  // 3. Scan symbol cells with boundary lookahead (prevent cross-row leak)
  const symbolMap = new Map();
  const symCellRegex = new RegExp(`<td\\b[^>]*\\bid="${gidRe}_cellOf_Symbol\\.SymbolId_EmployeeReports_row_(\\d+)_(\\d+)"[^>]*>([\\s\\S]*?)(?=<td\\b[^>]*\\bid=|<\\/tr>)`, 'gi');
  while ((tm = symCellRegex.exec(html)) !== null) {
    const key = `${tm[1]}_${tm[2]}`;
    const cellContent = tm[3];
    const optMatch = cellContent.match(/<option\b[^>]*\bselected\b[^>]*>([\s\S]*?)<\/option>/i);
    if (optMatch) {
      const symText = decodeEntities(optMatch[1].replace(/<[^>]+>/g, '')).trim();
      if (symText) symbolMap.set(key, symText);
    }
  }

  // Every row the grid has, by number: a grid can open with the last days of
  // the month before, which pushes the month's own last day past row 30.
  const rowPrefix = `${gid}_cellOf_ReportDate_row_`;
  const rowNumbers = [...new Set([...ovMap.keys()]
    .filter(id => id.startsWith(rowPrefix) && /^\d+$/.test(id.slice(rowPrefix.length)))
    .map(id => parseInt(id.slice(rowPrefix.length), 10)))].sort((a, b) => a - b);

  let days = [];
  for (const n of rowNumbers) {
    const dateLabel = ovMap.get(`${gid}_cellOf_ReportDate_row_${n}`);
    if (!dateLabel) continue;
    const dm = dateLabel.match(/(\d{1,2})\/(\d{1,2})/);
    // A row labelled with another month does not belong to this grid.
    if (!dm || parseInt(dm[2], 10) !== month) continue;
    const dayNum = parseInt(dm[1], 10);
    const isoDate = `${year}-${String(month).padStart(2, '0')}-${String(dayNum).padStart(2, '0')}`;

    // The special cell holds a holiday label or, failing that, a validation
    // message — the same split as parser.py's _classify_special.
    // A holiday label, a validation message, or both side by side — split as
    // parser.py's _classify_special does.
    const rawSpecial = specialMap.get(n) || null;
    let special = null;
    let error = null;
    if (rawSpecial) {
      const labels = ["ערב ראש השנה", 'ערב יוה"כ', "חול המועד", "ערב חג", "חג"];
      const found = labels.find(l => rawSpecial === l || rawSpecial.startsWith(l + " ") || rawSpecial.endsWith(" " + l));
      if (found) {
        special = found;
        const rest = rawSpecial === found ? ""
          : rawSpecial.startsWith(found + " ") ? rawSpecial.slice(found.length + 1)
          : rawSpecial.slice(0, rawSpecial.length - found.length - 1);
        error = rest || null;
      } else if (rawSpecial.startsWith("ערב")) {
        special = rawSpecial;
      } else {
        error = rawSpecial;
      }
    }

    const clock = [];
    for (let k = 0; k < 12; k++) {
      const entryKey = `${gid}_cellOf_OriginalEntry_ClockReports_row_${n}_${k}`;
      if (!ovMap.has(entryKey)) break;
      const entryVal = ovMap.get(entryKey);
      const exitVal = ovMap.get(`${gid}_cellOf_OriginalExit_ClockReports_row_${n}_${k}`);
      if (isBlank(entryVal) && isBlank(exitVal)) continue;
      clock.push({ entry: parseTime(entryVal), exit: parseTime(exitVal) });
    }

    const report = [];
    // What Hilan requires on rows that report nothing yet — a future day, a
    // day not filled in. It still states the requirement there.
    const idleStandards = [];
    for (let k = 0; k < 12; k++) {
      const entryKey = `${gid}_cellOf_ManualEntry_EmployeeReports_row_${n}_${k}`;
      if (!ovMap.has(entryKey)) break;
      const entryVal = ovMap.get(entryKey);
      const exitVal = ovMap.get(`${gid}_cellOf_ManualExit_EmployeeReports_row_${n}_${k}`);
      const totVal = ovMap.get(`${gid}_cellOf_ManualTotal_EmployeeReports_row_${n}_${k}`);
      const stdVal = ovMap.get(`${gid}_cellOf_StandardWorkHours_EmployeeReports_row_${n}_${k}`);
      const commentVal = ovMap.get(`${gid}_cellOf_Comment_EmployeeReports_row_${n}_${k}`);
      const symbol = symbolMap.get(`${n}_${k}`) || "";

      if (isBlank(entryVal) && isBlank(exitVal) && isBlank(totVal) && !symbol) {
        const idle = parseDecimal(stdVal);
        if (idle != null) idleStandards.push(idle);
        continue;
      }
      report.push({
        entry: parseTime(entryVal),
        exit: parseTime(exitVal),
        totalMinutes: parseDurationMinutes(totVal),
        standard: parseDecimal(stdVal),
        comment: isBlank(commentVal) ? "" : commentVal.trim(),
        symbol: symbol,
      });
    }

    days.push({
      date: isoDate,
      year: year,
      month: month,
      day: dayNum,
      special: special,
      error: error,
      clock: clock,
      report: report,
      idleStandards: idleStandards,
    });
  }

  // Two rows for one date: the later one stands, as in calc.py.
  days = [...new Map(days.map(d => [d.date, d])).values()];
  days.sort((a, b) => a.date.localeCompare(b.date));

  // Today ISO
  const todayIso = `${nowObj.getFullYear()}-${String(nowObj.getMonth() + 1).padStart(2, '0')}-${String(nowObj.getDate()).padStart(2, '0')}`;
  const lastDom = daysInMonth(year, month);
  const monthEndIso = `${year}-${String(month).padStart(2, '0')}-${String(lastDom).padStart(2, '0')}`;

  const toMin = t => t.hour * 60 + t.minute;
  const wall = (a, b) => ((toMin(b) - toMin(a)) % 1440 + 1440) % 1440;
  const clockExit = (rec, entry) => {
    const s = rec.clock.find(c => c.entry && c.exit && toMin(c.entry) === toMin(entry));
    return s ? s.exit : null;
  };
  // calc.py's _closed_spans: every finished session — a row with an exit, with
  // a total, or with an exit only the clock column has yet.
  const closedSpans = rec => rec.report.filter(s => s.entry).map(s => {
    const end = s.exit ? toMin(s.exit)
      : s.totalMinutes != null ? (toMin(s.entry) + s.totalMinutes) % 1440
      : clockExit(rec, s.entry) ? toMin(clockExit(rec, s.entry)) : null;
    return end == null ? null : [toMin(s.entry), end];
  }).filter(Boolean);
  // calc.py's _covered: a finished session spans this moment or comes after it,
  // so a session begun here is not the one running now.
  const covered = (rec, entry) => closedSpans(rec).some(([a, b]) => a > b || toMin(entry) < b);
  // calc.py's _open_entry: of the entries still open, the latest one running.
  const openEntry = rec => {
    const latest = entries => entries.filter(e => !covered(rec, e))
      .reduce((best, e) => (best == null || toMin(e) > toMin(best) ? e : best), null);
    const reported = latest(rec.report
      .filter(s => s.entry && !s.exit && s.totalMinutes == null && !clockExit(rec, s.entry))
      .map(s => s.entry));
    return reported != null ? reported : latest(rec.clock.filter(s => s.entry && !s.exit).map(s => s.entry));
  };
  const isoMinusDays = (iso, n) => {
    const [y, m, d] = iso.split('-').map(x => parseInt(x, 10));
    const t = new Date(Date.UTC(y, m - 1, d));
    t.setUTCDate(t.getUTCDate() - n);
    return t.toISOString().slice(0, 10);
  };
  const nowMinOfDay = nowObj.getHours() * 60 + nowObj.getMinutes();

  // settled_through = min(month_end, min(today, sync_date || today) - 1 day)
  const syncDateLimit = syncDate || todayIso;
  const reachedDate = syncDateLimit < todayIso ? syncDateLimit : todayIso;
  let reachedMinusOneIso = isoMinusDays(reachedDate, 1);
  // A shift still running past midnight has not finished its day (calc.py's
  // LONGEST_SHIFT of 16 hours tells it from a forgotten clock-out).
  const yesterdayIso = isoMinusDays(todayIso, 1);
  const yesterdayRec = days.find(d => d.date === yesterdayIso);
  const todayRec = days.find(d => d.date === todayIso);
  // Not once today has an entry of its own before now.
  const inToday = todayRec && [...todayRec.report, ...todayRec.clock]
    .some(s => s.entry && toMin(s.entry) <= nowMinOfDay);
  if (yesterdayRec && reachedMinusOneIso >= yesterdayIso && !inToday) {
    const began = openEntry(yesterdayRec);
    if (began && 1440 - toMin(began) + nowMinOfDay <= 16 * 60) {
      reachedMinusOneIso = isoMinusDays(yesterdayIso, 1);
    }
  }

  const settledThrough = reachedMinusOneIso < monthEndIso ? reachedMinusOneIso : monthEndIso;

  const specialsMap = new Map();
  for (const d of days) {
    if (d.special) specialsMap.set(d.date, d.special);
  }

  const daysCalc = days.map(d => {
    const byRule = dailyStandard(d.year, d.month, d.day, specialsMap.get(d.date));
    // Where Hilan states the day's requirement it decides, on any of the day's
    // rows; rows that disagree with each other leave it to the rule.
    const stated = new Set(d.idleStandards);
    for (const seg of d.report) {
      if (seg.standard != null) stated.add(seg.standard);
    }
    const standard = stated.size === 1 ? Array.from(stated)[0] : byRule;
    const standardMin = minutesOf(standard);

    let workedMin = 0;
    let hoursOffMin = 0;
    let dayOffMin = 0;
    for (const seg of d.report) {
      const dur = segmentMinutes(seg.entry, seg.exit, seg.totalMinutes);
      if (isAbsence(seg.symbol)) {
        if (dur) {
          // Leave reported with its hours is worth those hours, not a day.
          hoursOffMin += dur;
        } else {
          const segStd = seg.standard != null ? seg.standard : standard;
          dayOffMin += minutesOf(segStd);
        }
        continue;
      }
      if (dur) workedMin += dur;
    }

    // An absence fills what is left of the day's requirement and no more.
    const neededMin = Math.max(0, standardMin - workedMin);
    const creditedMin = Math.min(hoursOffMin + dayOffMin, neededMin);

    return {
      date: d.date,
      workedMinutes: workedMin,
      workedHours: roundHours(workedMin),
      creditedMinutes: creditedMin,
      creditedHours: roundHours(creditedMin),
      standardMinutes: standardMin,
      standardHours: standard,
    };
  });

  let settledWorkedMin = 0;
  let settledCreditedMin = 0;
  let settledStandardMin = 0;

  for (const d of daysCalc) {
    if (d.date <= settledThrough) {
      settledWorkedMin += d.workedMinutes;
      settledCreditedMin += d.creditedMinutes;
    }
  }
  // Every settled day of the month asks its hours, one the page has no row for
  // included (the rule's), as in the month's total — calc.py does the same.
  const standardByDay = new Map(daysCalc.map(d => [d.date, d.standardMinutes]));
  for (let dom = 1; dom <= lastDom; dom++) {
    const iso = `${year}-${String(month).padStart(2, '0')}-${String(dom).padStart(2, '0')}`;
    if (iso > settledThrough) break;
    settledStandardMin += standardByDay.has(iso)
      ? standardByDay.get(iso)
      : minutesOf(dailyStandard(year, month, dom, specialsMap.get(iso)));
  }

  const bankedWorked = roundHours(settledWorkedMin);
  const bankedCredited = roundHours(settledCreditedMin);
  const bankedStandard = roundHours(settledStandardMin);
  const bankedBalance = roundHours(settledWorkedMin + settledCreditedMin - settledStandardMin);

  let mWorkedMin = 0;
  let mCreditedMin = 0;
  let mStandardMin = 0;
  // The month asks what Hilan says each day asks, as the balance does; the
  // rule only for a day the page has no row for.
  for (let dom = 1; dom <= lastDom; dom++) {
    const iso = `${year}-${String(month).padStart(2, '0')}-${String(dom).padStart(2, '0')}`;
    mStandardMin += standardByDay.has(iso)
      ? standardByDay.get(iso)
      : minutesOf(dailyStandard(year, month, dom, specialsMap.get(iso)));
  }
  for (const d of daysCalc) {
    mWorkedMin += d.workedMinutes;
    mCreditedMin += d.creditedMinutes;
  }
  const monthWorked = roundHours(mWorkedMin);
  const monthStandard = roundHours(mStandardMin);
  const monthRemaining = roundHours(mStandardMin - mWorkedMin - mCreditedMin);

  // Today Forecast — counted exactly as calc.py's _forecast: today's finished
  // work and leave as for any other day, its requirement as Hilan states it.
  // A page without a row for today has no forecast at all, as in Python.
  const todayRecord = days.find(d => d.date === todayIso);
  const todayCalc = daysCalc.find(d => d.date === todayIso);
  // Plus rows whose exit has reached the clock column but not the report.
  const todayClosedMin = (todayCalc ? todayCalc.workedMinutes : 0) + (todayRecord
    ? todayRecord.report
        .filter(s => s.entry && !s.exit && s.totalMinutes == null && clockExit(todayRecord, s.entry))
        .reduce((sum, s) => sum + wall(s.entry, clockExit(todayRecord, s.entry)), 0)
    : 0);
  const todayCreditedMin = todayCalc ? todayCalc.creditedMinutes : 0;
  const todayStandardMin = todayCalc ? todayCalc.standardMinutes : 0;
  let todayOpenEntry = null;

  if (todayRecord) {
    todayOpenEntry = openEntry(todayRecord);
  }

  let todayWorkedSoFarMin = todayClosedMin;
  if (todayOpenEntry) {
    const startMin = toMin(todayOpenEntry);
    if (startMin <= nowMinOfDay) {
      todayWorkedSoFarMin += (nowMinOfDay - startMin);
    }
  }

  const liveBalanceMin = (settledWorkedMin + settledCreditedMin - settledStandardMin)
    + todayWorkedSoFarMin + todayCreditedMin - todayStandardMin;
  const liveBalance = roundHours(liveBalanceMin);

  // Leave time to close today
  let leaveToCloseStr = null;
  // Once the day is made the leave time lies in the past and says nothing, so
  // none is offered.
  const todayRemainingMin = Math.max(0, todayStandardMin - todayWorkedSoFarMin - todayCreditedMin);
  const doneForToday = todayRemainingMin === 0;
  if (todayOpenEntry && !doneForToday) {
    const neededFromNowMin = Math.max(0, todayStandardMin - todayClosedMin - todayCreditedMin);
    const leaveTotalMin = (todayOpenEntry.hour * 60 + todayOpenEntry.minute + neededFromNowMin) % (24 * 60);
    const lh = Math.floor(leaveTotalMin / 60);
    const lm = leaveTotalMin % 60;
    leaveToCloseStr = `${String(lh).padStart(2, '0')}:${String(lm).padStart(2, '0')}`;
  }

  return {
    year,
    month,
    todayIso,
    settledThrough,
    syncDisplay,
    banked: {
      worked: bankedWorked.toFixed(2),
      credited: bankedCredited.toFixed(2),
      standard: bankedStandard.toFixed(2),
      balance: bankedBalance.toFixed(2),
      balanceNum: bankedBalance,
    },
    monthTotals: {
      worked: monthWorked.toFixed(2),
      standard: monthStandard.toFixed(2),
      remaining: monthRemaining.toFixed(2),
    },
    live: {
      balance: liveBalance.toFixed(2),
      balanceNum: liveBalance,
      balanceMinutes: liveBalanceMin,
    },
    today: {
      workedSoFar: roundHours(todayWorkedSoFarMin).toFixed(2),
      workedSoFarMin: todayWorkedSoFarMin,
      standard: roundHours(todayStandardMin).toFixed(2),
      remaining: roundHours(todayRemainingMin).toFixed(2),
      credited: roundHours(todayCreditedMin).toFixed(2),
      isOpen: Boolean(todayOpenEntry),
      leaveToClose: leaveToCloseStr,
      entry: todayOpenEntry ? todayOpenEntry.str : null,
      done: doneForToday,
      present: Boolean(todayRecord),
    },
  };
}

// "as of 14:02" today, "as of 21/09 14:02" otherwise — a cached figure has to
// say when it was true, or it reads as now. In Israel time, like the figures.
function staleLabel(analysis, now = israelNow()) {
  if (!analysis.stale) return null;
  const saved = analysis.stale.since ? new Date(analysis.stale.since) : null;
  if (!saved || isNaN(saved.getTime())) return "as of an earlier run";
  const d = israelNow(saved);
  const pad = n => String(n).padStart(2, '0');
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  const sameDay = d.toDateString() === now.toDateString();
  return sameDay ? `as of ${hm}` : `as of ${pad(d.getDate())}/${pad(d.getMonth() + 1)} ${hm}`;
}

// Why the figure is old, short enough for a widget line.
function staleReason(analysis) {
  const kind = analysis.stale && analysis.stale.kind;
  if (kind === "refused") return "password refused, open the script";
  if (kind === "verification") return "code needed, open the script";
  if (kind === "setup") return "sign-in needed, open the script";
  return "Hilan unreachable";
}

// Whether a cached figure was worked out today, in Israel.
function staleToday(analysis, now = israelNow()) {
  const saved = analysis.stale && analysis.stale.since ? new Date(analysis.stale.since) : null;
  return Boolean(saved) && !isNaN(saved.getTime()) && israelNow(saved).toDateString() === now.toDateString();
}

// "2026-09-22" as 22/09, the way Hilan and the command line write dates.
function dayMonth(iso) {
  const [, m, d] = String(iso).split("-");
  return `${d}/${m}`;
}

// --- Spoken output for Siri / Shortcuts ---
function toSpoken(analysis) {
  function formatHm(minTotal) {
    const mins = Math.abs(minTotal);
    const h = Math.floor(mins / 60);
    const m = mins % 60;
    if (h === 0) return m === 1 ? "1 minute" : `${m} minutes`;
    const pl = h === 1 ? "hour" : "hours";
    return m === 0 ? `${h} ${pl}` : `${h} ${pl} ${m}`;
  }

  // Exact minutes where the analysis has them; a cached analysis may carry
  // only the rounded hours.
  const liveMinutes = analysis.live.balanceMinutes != null
    ? analysis.live.balanceMinutes
    : Math.round(analysis.live.balanceNum * 60);
  const direction = liveMinutes >= 0 ? "up" : "down";
  // "right now" is a lie about a cached figure; say when it was true instead.
  const when = staleLabel(analysis) || "right now";
  const parts = [`You are ${formatHm(liveMinutes)} ${direction} ${when}`];

  // A cached figure says why it is old, and an earlier day's hours and leave
  // time are not said as today's.
  if (analysis.stale) {
    const why = {
      refused: "Hilan refused the password; open the script in Scriptable to enter it.",
      verification: "Hilan wants a verification code; open the script in Scriptable to enter it.",
      setup: "Hilan needs you to sign in; open the script in Scriptable.",
    }[analysis.stale.kind] || "Hilan could not be reached.";
    if (!staleToday(analysis)) return `${parts[0]}. ${why}`;
    parts[0] = `${why} ${parts[0]}`;
  }

  const t = analysis.today;
  // No row for today on the page: nothing to say about today, as in say.py.
  if (t.present === false) return parts[0] + ".";
  if (t.done) {
    parts.push(`${formatHm(t.workedSoFarMin)} today, done for today`);
  } else if (!t.entry) {
    parts.push(`${formatHm(t.workedSoFarMin)} today, not clocked in`);
  } else {
    parts.push(`${formatHm(t.workedSoFarMin)} today`);
    if (t.leaveToClose) {
      parts.push(`you can leave at ${t.leaveToClose}`);
    }
  }

  let said = parts.slice(0, 2).join(", ");
  if (parts.length > 2) {
    said += `. ${parts[2][0].toUpperCase()}${parts[2].slice(1)}`;
  }
  return said + ".";
}

// --- WidgetKit UI Rendering ---
function createWidget(analysis, family) {
  const widget = new ListWidget();
  widget.backgroundColor = new Color("#1c1c1e");
  widget.setPadding(12, 14, 12, 14);

  // A cached answer is marked on every size, so it can never pass for now.
  const stale = staleLabel(analysis);
  const staleColor = new Color("#ff9f0a");
  if (stale && family === "accessoryRectangular") {
    const s = widget.addText(`⚠ ${stale}`);
    s.font = Font.systemFont(11);
  } else if (stale && family === "accessoryInline") {
    // Inline shows a single line; a separate marker would push the number out,
    // so the balance line itself carries the time below.
  } else if (stale && family !== "accessoryCircular") {
    // The round widget marks an old figure in its own label below.
    const s = widget.addText(`⚠ ${stale} · ${staleReason(analysis)}`);
    s.font = Font.systemFont(10);
    s.textColor = staleColor;
    widget.addSpacer(2);
  }

  const liveSign = analysis.live.balanceNum > 0 ? "+" : "";
  const liveColor = analysis.live.balanceNum >= 0 ? new Color("#30d158") : new Color("#ff453a");

  const bankedSign = analysis.banked.balanceNum > 0 ? "+" : "";
  const bankedColor = analysis.banked.balanceNum >= 0 ? new Color("#30d158") : new Color("#ff453a");

  if (family === "accessoryCircular") {
    // The round Lock Screen widget: the balance, and a mark if it is old.
    const label = widget.addText(stale ? "⚠" : "NOW");
    label.font = Font.systemFont(9);
    const value = widget.addText(`${liveSign}${analysis.live.balance}`);
    value.font = Font.boldSystemFont(12);
    value.minimumScaleFactor = 0.5;
    return widget;
  }

  if (family === "accessoryRectangular" || family === "accessoryInline") {
    // Lock Screen Widget
    const staleSuffix = stale && family === "accessoryInline" ? ` ⚠ ${stale}` : "";
    const titleText = widget.addText(`Hilan: ${liveSign}${analysis.live.balance}${staleSuffix}`);
    titleText.font = Font.boldSystemFont(14);
    if (family === "accessoryRectangular") {
      const line2 = widget.addText(`Today: ${analysis.today.workedSoFar}/${analysis.today.standard}`);
      line2.font = Font.systemFont(12);
      // Three lines fit; an old figure's marker takes one, and its leave time
      // is old too.
      if (analysis.today.leaveToClose && !stale) {
        const line3 = widget.addText(`Leave: ${analysis.today.leaveToClose}`);
        line3.font = Font.systemFont(11);
      }
    }
    return widget;
  }

  if (family === "small") {
    // Small Widget
    const headerStack = widget.addStack();
    headerStack.layoutHorizontally();
    const title = headerStack.addText("HILAN");
    title.font = Font.boldSystemFont(12);
    title.textColor = new Color("#0a84ff");
    headerStack.addSpacer();

    widget.addSpacer(4);
    const nowLabel = widget.addText("NOW");
    nowLabel.font = Font.systemFont(10);
    nowLabel.textColor = Color.gray();

    const nowVal = widget.addText(`${liveSign}${analysis.live.balance}`);
    nowVal.font = Font.boldSystemFont(26);
    nowVal.textColor = liveColor;

    widget.addSpacer(4);
    const bankedRow = widget.addStack();
    bankedRow.layoutHorizontally();
    const bLabel = bankedRow.addText("BANKED ");
    bLabel.font = Font.systemFont(11);
    bLabel.textColor = Color.gray();
    const bVal = bankedRow.addText(`${bankedSign}${analysis.banked.balance}`);
    bVal.font = Font.boldSystemFont(11);
    bVal.textColor = bankedColor;

    widget.addSpacer(2);
    const todayRow = widget.addStack();
    todayRow.layoutHorizontally();
    const tLabel = todayRow.addText("TODAY ");
    tLabel.font = Font.systemFont(11);
    tLabel.textColor = Color.gray();
    const tVal = todayRow.addText(`${analysis.today.workedSoFar}/${analysis.today.standard}`);
    tVal.font = Font.systemFont(11);
    tVal.textColor = Color.white();

    if (analysis.today.leaveToClose) {
      widget.addSpacer(2);
      const leaveText = widget.addText(`leave at ${analysis.today.leaveToClose}`);
      leaveText.font = Font.systemFont(10);
      leaveText.textColor = new Color("#ffd60a");
    }

    return widget;
  }

  // Medium Widget (Default)
  const topStack = widget.addStack();
  topStack.layoutHorizontally();

  const title = topStack.addText("Hilan Hours");
  title.font = Font.boldSystemFont(13);
  title.textColor = new Color("#0a84ff");
  topStack.addSpacer();

  if (analysis.syncDisplay) {
    const syncText = topStack.addText(`Synced: ${analysis.syncDisplay}`);
    syncText.font = Font.systemFont(10);
    syncText.textColor = Color.gray();
  }

  widget.addSpacer(6);

  // Main columns
  const contentStack = widget.addStack();
  contentStack.layoutHorizontally();

  // Left Column: NOW & BANKED
  const leftCol = contentStack.addStack();
  leftCol.layoutVertically();

  const nowStack = leftCol.addStack();
  nowStack.layoutHorizontally();
  const nowHead = nowStack.addText("NOW: ");
  nowHead.font = Font.boldSystemFont(18);
  nowHead.textColor = Color.white();
  const nowVal = nowStack.addText(`${liveSign}${analysis.live.balance}`);
  nowVal.font = Font.boldSystemFont(18);
  nowVal.textColor = liveColor;

  leftCol.addSpacer(2);
  const bankedStack = leftCol.addStack();
  bankedStack.layoutHorizontally();
  const bHead = bankedStack.addText("BANKED: ");
  bHead.font = Font.systemFont(13);
  bHead.textColor = Color.gray();
  const bVal = bankedStack.addText(`${bankedSign}${analysis.banked.balance}`);
  bVal.font = Font.boldSystemFont(13);
  bVal.textColor = bankedColor;

  leftCol.addSpacer(2);
  const settledText = leftCol.addText(`settled to ${dayMonth(analysis.settledThrough)}`);
  settledText.font = Font.systemFont(10);
  settledText.textColor = Color.gray();

  contentStack.addSpacer();

  // Right Column: TODAY & LEAVE
  const rightCol = contentStack.addStack();
  rightCol.layoutVertically();

  const todayStack = rightCol.addStack();
  todayStack.layoutHorizontally();
  const todayHead = todayStack.addText("TODAY: ");
  todayHead.font = Font.systemFont(13);
  todayHead.textColor = Color.gray();
  const todayVal = todayStack.addText(`${analysis.today.workedSoFar} of ${analysis.today.standard}`);
  todayVal.font = Font.boldSystemFont(13);
  todayVal.textColor = Color.white();

  rightCol.addSpacer(2);
  if (analysis.today.leaveToClose) {
    const leaveStack = rightCol.addStack();
    leaveStack.layoutHorizontally();
    const lHead = leaveStack.addText("LEAVE: ");
    lHead.font = Font.systemFont(13);
    lHead.textColor = new Color("#ffd60a");
    const lVal = leaveStack.addText(analysis.today.leaveToClose);
    lVal.font = Font.boldSystemFont(13);
    lVal.textColor = new Color("#ffd60a");
  } else if (analysis.today.done) {
    const doneText = rightCol.addText("Done for today");
    doneText.font = Font.boldSystemFont(12);
    doneText.textColor = new Color("#30d158");
  } else {
    const notInText = rightCol.addText(analysis.today.isOpen ? "Clocked in" : "Not clocked in");
    notInText.font = Font.systemFont(11);
    notInText.textColor = Color.gray();
  }

  rightCol.addSpacer(2);
  const monthText = rightCol.addText(`Month left: ${analysis.monthTotals.remaining}`);
  monthText.font = Font.systemFont(10);
  monthText.textColor = Color.gray();

  // Large and extra large widgets get the same content, with the time it was
  // worked out, so a figure that iOS has not refreshed can be seen as such.
  if (family === "large" || family === "extraLarge") {
    widget.addSpacer();
    const footer = widget.addText(analysis.computedAt ? `worked out at ${analysis.computedAt}` : "");
    footer.font = Font.systemFont(10);
    footer.textColor = Color.gray();
  }

  // Tap opens Hilan portal in Safari
  widget.url = `${BASE_URL}${ATTENDANCE_PATH}?isOnSelf=true`;

  return widget;
}

// The password is sent to BASE_URL, so it has to be a Hilan site.
function checkBaseUrl() {
  const url = BASE_URL.toLowerCase();
  if (!/^https:\/\/[a-z0-9-]+(\.[a-z0-9-]+)*\.hilan\.co\.il$/.test(url) || url.includes("your-company")) {
    throw new Error("Set BASE_URL at the top of the script to your company's Hilan site, like https://yourcompany.net.hilan.co.il");
  }
}

async function run() {
  try {
    checkBaseUrl();
    // Hilan's day and hours are Israel's, wherever the phone happens to be.
    const now = israelNow();
    let analysis = null;

    try {
      const client = new HilanClient();
      const html = await client.fetchMonth(now.getFullYear(), now.getMonth() + 1);
      analysis = parseAndAnalyse(html, now);
      const pad = n => String(n).padStart(2, '0');
      analysis.computedAt = `${pad(now.getHours())}:${pad(now.getMinutes())}`;
      saveCache({ ...analysis, cachedAt: new Date().toISOString() });
    } catch (err) {
      // Fall back to the last good answer, but never pass it off as current:
      // its live figure is frozen at the moment it was saved, so unmarked, a
      // broken session would leave yesterday's figure on the home screen all
      // day, read as today's.
      analysis = loadLastCache();
      if (!analysis) {
        throw err;
      }
      analysis.stale = { since: analysis.cachedAt || null, reason: err.message,
                         kind: err instanceof LoginRefused ? err.kind : null };
    }

    const spokenText = toSpoken(analysis);

    // If invoked via Siri / Shortcuts ("Run Script" action):
    if (!config.runsInWidget && !config.runsInApp) {
      Script.setShortcutOutput(spokenText);
      if (config.runsWithSiri) {
        await Speech.speak(spokenText);
      } else {
        // If they just tapped the Scriptable block in the Shortcuts app, show an alert so it doesn't fail with "no UI"
        const alert = new Alert();
        alert.title = "Hilan Balance";
        alert.message = spokenText;
        alert.addAction("OK");
        await alert.presentAlert();
      }
      Script.complete();
      return;
    }

    // If invoked as Widget:
    const family = config.widgetFamily || "medium";
    const widget = createWidget(analysis, family);

    if (config.runsInWidget) {
      // The live figure moves with the clock; ask iOS for a fresh one within
      // a quarter of an hour (it decides when, this is only a request).
      widget.refreshAfterDate = new Date(Date.now() + 15 * 60 * 1000);
      Script.setWidget(widget);
    } else {
      // Preview in App
      await widget.presentMedium();
      console.log(spokenText);
    }
  } catch (err) {
    console.error(err);
    if (!config.runsInWidget && !config.runsInApp) {
      Script.setShortcutOutput(`Error: ${err.message}`);
      // Siri says what went wrong, rather than nothing at all.
      if (config.runsWithSiri) await Speech.speak(`Hilan: ${err.message}`);
    } else if (config.runsInWidget) {
      const errorWidget = new ListWidget();
      errorWidget.backgroundColor = new Color("#ff453a");
      // A lock-screen widget has room for a word, not a sentence.
      const tiny = String(config.widgetFamily || "").startsWith("accessory");
      const short = err instanceof LoginRefused
        ? ({ verification: "⚠ code needed", setup: "⚠ open Scriptable" }[err.kind] || "⚠ password refused")
        : "⚠ Hilan";
      const text = errorWidget.addText(tiny ? short : err.message);
      text.font = Font.systemFont(12);
      errorWidget.refreshAfterDate = new Date(Date.now() + 15 * 60 * 1000);
      Script.setWidget(errorWidget);
    } else {
      const alert = new Alert();
      alert.title = "Hilan Widget Error";
      alert.message = err.message;
      alert.addAction("OK");
      await alert.presentAlert();
    }
  } finally {
    Script.complete();
  }
}

await run();
