"""Drive the real Youi motors journey in a browser.

Why this exists
---------------
Youi's WAF (`enableWafBotDetection` / `configure-enhanced-routing`) silently
routes sessions it does not recognise to the call centre: pricing calls answer
200 with zero `dataDeltaOperations` and no premium. Requests we issue ourselves
are never sensor-signed — the site's own calls carry `hqsudwprpp-*` and
`x-client-fingerprint`, ours carry none, and the signer is module-scoped inside
the bundle (window.fetch and XMLHttpRequest are untouched, no sensor global is
exposed). The only way to make the WAF see legitimate traffic is to let the app
issue every request itself, i.e. drive the UI.

What the journey actually is
----------------------------
Angular + Angular Material, served under /dcq/vehicle/<sqid>/lookup. Three
things differ from the NRMA journey that ui_flow.py drives:

  * The URL never changes. Steps swap the form in place, so progress is detected
    by the control set changing, not by navigation.
  * Radios are CSS-hidden behind `label[for=...]` toggles; clicking the label is
    what marks the control ng-valid/ng-dirty (verified — a JS .click() on the
    input is not needed).
  * Dropdowns are `mat-select` (a `role=combobox` div, not a `<select>`) and text
    fields are `mat-autocomplete`; both resolve through `mat-option` elements in
    an overlay panel. A plain `input,select` scrape misses every one of them.

Validation messages sit in the DOM while hidden, so only visible ones count.
"""

import logging
import os
import re

log = logging.getLogger("YouiMotorsUI")

SAVE = "#btnSave"
RENDER_MS = int(os.getenv("YOUI_UI_RENDER_MS", "150000"))
SETTLE_MS = int(os.getenv("YOUI_UI_SETTLE_MS", "2500"))
STEP_TIMEOUT_MS = int(os.getenv("YOUI_UI_STEP_MS", "60000"))


# A mat-select renders its placeholder as innerText, so an unanswered dropdown
# reads back as "Please select" rather than "". Treating that as a filled value
# silently skipped every dropdown in the journey.
PLACEHOLDERS = ("please select", "please choose", "select", "choose", "-")


def is_blank(value: str | None) -> bool:
    v = (value or "").strip().lower()
    return v == "" or v in PLACEHOLDERS


class UIFlowError(RuntimeError):
    """The journey could not be driven forward."""


class Retryable(UIFlowError):
    """Session/exit-IP problem — worth a fresh browser."""


class Unanswerable(UIFlowError):
    """A control appeared that no mapping covers — a real gap, not a flake."""


# Every control the journey exposes, however it is rendered. Kept in one place so
# the walker and the fillers agree on what "a field" is.
SCAN_JS = """() => {
  const box = e => { const r = e.getBoundingClientRect();
                     return r.width > 0 && r.height > 0; };
  const out = {url: location.href, controls: [], errors: [], headings: [],
               text: (document.body.innerText||'').replace(/\\s+/g,' ').slice(0,1200)};
  document.querySelectorAll('h1,h2,h3').forEach(h => {
    const t = (h.innerText||'').trim(); if (t) out.headings.push(t.slice(0,80));
  });
  // native inputs (radios are hidden by design, so no visibility filter on them)
  document.querySelectorAll('input,textarea').forEach(e => {
    const type = e.getAttribute('type') || 'text';
    if (type !== 'radio' && type !== 'checkbox' && !box(e)) return;
    const lab = e.id ? document.querySelector('label[for="' + CSS.escape(e.id) + '"]') : null;
    out.controls.push({kind: type === 'radio' ? 'radio'
                             : type === 'checkbox' ? 'checkbox' : 'text',
      id: e.getAttribute('id')||'', name: e.getAttribute('name')||'',
      fcn: e.getAttribute('formcontrolname')||'', value: e.value||'',
      checked: !!e.checked,
      label: lab ? (lab.innerText||'').trim().slice(0,60) : '',
      autocomplete: !!e.getAttribute('aria-autocomplete') ||
                    (e.className||'').includes('autocomplete')});
  });
  document.querySelectorAll('select').forEach(e => { if (!box(e)) return;
    out.controls.push({kind: 'select', id: e.getAttribute('id')||'',
      fcn: e.getAttribute('formcontrolname')||'', value: e.value||'',
      options: [...e.options].map(o => o.value)});
  });
  // Angular Material dropdowns: role=combobox divs, value lives in the text
  document.querySelectorAll('mat-select,[role=combobox]').forEach(e => {
    if (e.tagName === 'INPUT' || !box(e)) return;
    out.controls.push({kind: 'matselect', id: e.getAttribute('id')||'',
      fcn: e.getAttribute('formcontrolname')||'',
      value: (e.innerText||'').trim().slice(0,60),
      label: (e.getAttribute('aria-label')||'').slice(0,60)});
  });
  out.buttons = [];
  document.querySelectorAll('button,[role=button]').forEach(e => {
    if (!box(e)) return;
    out.buttons.push({id: e.getAttribute('id')||'',
                      text: (e.innerText||'').trim().slice(0,40),
                      disabled: !!e.disabled,
                      cls: (e.className||'').toString().slice(0,60)});
  });
  document.querySelectorAll('[class*=error],.invalid-feedback,mat-error').forEach(e => {
    const t = (e.innerText||'').trim();
    if (t && box(e)) out.errors.push(t.slice(0,90));
  });
  out.errors = [...new Set(out.errors)];
  return out;
}"""


def signature(scan: dict) -> str:
    """Identifies which step we are on, since the URL never changes."""
    return "|".join(sorted(
        f"{c['kind']}:{c.get('id') or c.get('fcn')}" for c in scan["controls"]
    )) + "||" + "/".join(scan["headings"])


class MotorsJourney:
    """Drives one motor quote through the real UI on an already-bootstrapped page."""

    def __init__(self, page, answers: dict, vehicle: dict | None = None):
        self.page = page
        # Vehicle attribute signals for the find-your-car dropdowns, which are
        # dynamic and cannot be driven from a single input column.
        self.vehicle = vehicle or {}
        self.answers = answers          # control-id substring -> value
        self.history: list[str] = []
        # Requested-but-unavailable options. A mapped control whose value matches
        # no option is not a missing mapping and not an error — it is a
        # vocabulary mismatch, and it needs surfacing rather than silently
        # leaving the question unanswered.
        self.match_failures: list[dict] = []

    # ── primitives ────────────────────────────────────────────────────

    def scan(self) -> dict:
        return self.page.evaluate(SCAN_JS)

    def wait_ready(self) -> None:
        waited = 0
        while waited < RENDER_MS:
            if self.forward_button():
                self.page.wait_for_timeout(SETTLE_MS)
                return
            self.page.wait_for_timeout(2000)
            waited += 2000
        raise Retryable(f"step never rendered at {self.page.url}")

    def set_radio(self, control_id: str) -> bool:
        """Tick a hidden radio, verifying it actually took.

        Clicking `label[for=...]` is what Angular's reactive form listens to on
        most steps, but not all of them — the usage step reported a successful
        click and still said "Please make a selection". So the result is read
        back, and harder methods are tried until the input is really checked.
        """
        self.dismiss_overlay()
        checked = lambda: self.page.evaluate(
            "id => { const e = document.getElementById(id); return !!(e && e.checked); }",
            control_id)
        attempts = (
            ("label", lambda: self.page.click(f'label[for="{control_id}"]', timeout=8000)),
            ("force", lambda: self.page.locator(f"#{control_id}").check(force=True, timeout=8000)),
            ("js", lambda: self.page.evaluate(
                """id => { const e = document.getElementById(id);
                           if (!e) return;
                           e.click();
                           e.checked = true;
                           e.dispatchEvent(new Event('input', {bubbles: true}));
                           e.dispatchEvent(new Event('change', {bubbles: true})); }""",
                control_id)),
        )
        for name, action in attempts:
            try:
                action()
            except Exception:
                continue
            self.page.wait_for_timeout(600)
            if checked():
                if name != "label":
                    log.debug("│    radio %s needed %s", control_id, name)
                return True
        log.warning("│    radio %s: could not be ticked", control_id)
        return False

    def _mat_options(self, wait_ms: int = 15000) -> list[dict]:
        """Options arrive from the BFF, so poll instead of guessing a sleep.

        No visibility filter: a freshly opened overlay panel can measure zero for
        a frame, and dropping real options because of that is how the make field
        silently ended up empty.
        """
        read = """() => [...document.querySelectorAll('mat-option')]
                       .map(o => ({id: o.getAttribute('id')||'',
                                   text: (o.innerText||'').trim()}))
                       .filter(o => o.text)"""
        waited, opts, stable = 0, [], 0
        while waited < wait_ms:
            current = self.page.evaluate(read)
            if current and len(current) == len(opts):
                # The panel fills incrementally. Matching against a partial list
                # picked "Yaris" while "Yaris Cross" was still loading, so wait
                # for the count to hold steady before choosing.
                stable += 1
                if stable >= 2:
                    return current
            else:
                stable = 0
            opts = current
            self.page.wait_for_timeout(500)
            waited += 500
        return opts

    def _click_option(self, option_id: str) -> None:
        """Click an overlay option, past Material's own stacking quirks.

        Options in a mat-autocomplete/mat-select panel overlap enough that
        Playwright's actionability check reports "<mat-option> intercepts pointer
        events" and times out, so the interception check is skipped and a real
        DOM click is used as the fallback.
        """
        try:
            self.page.locator(f"#{option_id}").click(force=True, timeout=8000)
            return
        except Exception:
            pass
        self.page.evaluate(
            "id => { const o = document.getElementById(id); if (o) o.click(); }",
            option_id,
        )

    def _pick_option(self, wanted: str, control_id: str = "") -> str | None:
        """Exact match, then case-insensitive, then containment, then prefix."""
        opts = self._mat_options()
        if not opts:
            return None
        w = (wanted or "").strip().upper()
        for pred in (
            lambda t: t.upper() == w,
            lambda t: t.upper().replace(" ", "") == w.replace(" ", ""),
            # Variant/type options read "GX - 5 dr - Hatchback - FWD"; compare the
            # leading segment so "GX" cannot be satisfied by "GXL".
            lambda t: t.split(" - ")[0].strip().upper() == w,
            # Normalised comparison: the input says "GARAGE - PRIVATE AND LOCKED"
            # and the UI says "In A Garage - Private And Locked".
            lambda t: w and _norm_option(t) == _norm_option(w),
            lambda t: w and _norm_option(w) in _norm_option(t),
            # A 6-char prefix rule used to live here. It silently matched
            # "GARAGE - PRIVATE AND LOCKED" to "Under A Carport", i.e. answered a
            # pricing question wrongly rather than reporting it. Refusing is safer.
        ):
            hit = next((o for o in opts if pred(o["text"])), None)
            if hit:
                self._click_option(hit["id"])
                self.page.wait_for_timeout(1200)
                self.dismiss_overlay()
                return hit["text"]
        self.match_failures.append({"control": control_id, "wanted": wanted,
                                    "options": [o["text"] for o in opts]})
        log.warning("│  no option matching %r among %s", wanted,
                    [o["text"] for o in opts][:12])
        return None

    def dismiss_overlay(self, timeout_ms: int = 6000) -> None:
        """Clear any lingering CDK overlay backdrop.

        After a mat-select panel closes, Material can leave
        `.cdk-overlay-backdrop` in place for a beat; it intercepts pointer events
        and Playwright's click on #btnSave times out against it.
        """
        waited = 0
        while waited < timeout_ms:
            present = self.page.evaluate(
                "() => !!document.querySelector('.cdk-overlay-backdrop-showing')")
            if not present:
                return
            # Clicking the backdrop is how Material closes a panel; Escape alone
            # left it in place often enough to block the next field's click.
            try:
                self.page.locator(".cdk-overlay-backdrop-showing").first.click(
                    force=True, timeout=2000)
            except Exception:
                try:
                    self.page.keyboard.press("Escape")
                except Exception:
                    pass
            self.page.wait_for_timeout(400)
            waited += 400

    def set_autocomplete(self, control_id: str, value: str) -> str | None:
        """Type, wait for the panel, pick the best match.

        Typed character by character: Material's autocomplete filters on each
        keystroke, and a value pasted in one shot does not always trigger the
        lookup that populates the panel.
        """
        self.dismiss_overlay()
        self.page.locator(f"#{control_id}").click(force=True, timeout=8000)
        # fill() (not type()): Angular re-renders this input, and a keystroke
        # sequence races that re-render — the text silently failed to land.
        self.page.fill(f"#{control_id}", str(value), timeout=8000)
        landed = self.page.input_value(f"#{control_id}", timeout=5000)
        if landed.strip().upper() != str(value).strip().upper():
            log.warning("│    %s: typed %r but field holds %r",
                        control_id, value, landed)
        picked = self._pick_option(value, control_id)
        if picked is None:
            log.warning("│    %s: no options for %r (field now %r)",
                        control_id, value,
                        self.page.input_value(f"#{control_id}", timeout=5000))
        return picked

    def set_matselect(self, control_id: str, value: str) -> str | None:
        self.dismiss_overlay()
        self.page.locator(f"#{control_id}").click(force=True, timeout=8000)
        return self._pick_option(value)

    def set_vehicle_select(self, control_id: str) -> str | None:
        """Choose a find-your-car dropdown option by scoring the whole vehicle."""
        self.page.click(f"#{control_id}", timeout=8000)
        opts = self._mat_options()
        texts = [o["text"] for o in opts]
        want = best_vehicle_option(texts, self.vehicle)
        if want is None:
            self.match_failures.append(
                {"wanted": f"(vehicle match for {control_id})", "options": texts})
            log.warning("│    %s: no confident vehicle match among %s",
                        control_id, texts[:8])
            self.dismiss_overlay()
            return None
        hit = next(o for o in opts if o["text"] == want)
        self._click_option(hit["id"])
        self.page.wait_for_timeout(1200)
        self.dismiss_overlay()
        return want

    def set_text(self, control_id: str, value: str) -> bool:
        try:
            self.page.fill(f"#{control_id}", str(value), timeout=8000)
            return True
        except Exception:
            return False

    def value_for(self, control: dict) -> str | None:
        """Resolve a control id to an answer, most specific rule first.

        Suffix before substring, because ids embed the whole question path:
        `find-your-car-lookup-by-make-and-model-make` contains both `-make` and
        `-model`, and a longest-substring rule picks `-model` — which typed the
        family name into the Make field and left the journey stuck.
        """
        ident = (control.get("id") or control.get("fcn") or "").lower()
        if not ident:
            return None
        if ident in self.answers:
            return self.answers[ident]
        for match in (str.endswith, str.__contains__):
            best = None
            for key, val in self.answers.items():
                k = key.lower()
                if match(ident, k) and (best is None or len(k) > len(best[0])):
                    best = (k, val)
            if best:
                return best[1]
        return None

    # ── step filling ──────────────────────────────────────────────────

    def fill_radio_groups(self, scan: dict) -> bool:
        """Answer each radio group by its label text.

        Option ids carry no meaning here ("...-existing-damage-807"), so the
        answer is resolved against the group name and then matched to the member
        whose visible label says Yes/No/etc.
        """
        groups: dict[str, list[dict]] = {}
        for c in scan["controls"]:
            if c["kind"] == "radio" and c.get("id"):
                groups.setdefault(c.get("name") or c["id"], []).append(c)
        acted = False
        for name, members in groups.items():
            if any(m.get("checked") for m in members):
                continue
            wanted = self.value_for({"id": name})
            if wanted is None:
                continue
            w = str(wanted).strip().lower()
            hit = (next((m for m in members if (m.get("label") or "").strip().lower() == w), None)
                   or next((m for m in members if w and w in (m.get("label") or "").lower()), None))
            if hit and self.set_radio(hit["id"]):
                log.info("│    radio %s -> %r", name, hit.get("label") or hit["id"])
                acted = True
            elif not hit:
                log.warning("│    radio %s: no option labelled %r among %s", name, wanted,
                            [m.get("label") for m in members])
        return acted

    def fill_step(self, max_reveals: int = 25) -> dict:
        """Fill the current step until it stops revealing new fields.

        The form is progressive: ticking a radio reveals the next input, filling
        that reveals the one after, and Continue only works once the whole
        visible form is answered. A single scan-then-Continue pass therefore
        submits a half-empty form and gets "Please select an option" — the
        journey has to be re-scanned after every answer.
        """
        scan = self.scan()
        for _ in range(max_reveals):
            acted = self.fill_radio_groups(scan)
            if acted:
                self.page.wait_for_timeout(SETTLE_MS)
                scan = self.scan()
                continue
            for c in scan["controls"]:
                cid = c.get("id") or ""
                if not cid:
                    continue
                value = self.value_for(c)
                vehicle_sel = (c["kind"] == "matselect" and bool(self.vehicle)
                               and is_vehicle_select(cid))
                if value is None and not vehicle_sel:
                    continue
                filled = not is_blank(c.get("value"))
                if c["kind"] == "radio":
                    pass          # radio groups are handled before this loop
                elif c["kind"] == "matselect" and not filled:
                    if vehicle_sel:
                        got = self.set_vehicle_select(cid)
                        log.info("│    vehicle %s -> %r", cid, got)
                    else:
                        got = self.set_matselect(cid, value)
                        log.info("│    select %s -> %r", cid, got)
                    acted = got is not None
                elif c["kind"] == "select" and not filled:
                    try:
                        self.page.select_option(f"#{cid}", label=value, timeout=8000)
                        acted = True
                    except Exception:
                        pass
                elif c["kind"] == "text" and not filled:
                    if c.get("autocomplete"):
                        got = self.set_autocomplete(cid, value)
                        log.info("│    auto %s -> %r", cid, got)
                        acted = got is not None
                    elif self.set_text(cid, value):
                        log.info("│    text %s = %r", cid, value)
                        acted = True
                if acted:
                    break            # re-scan: that answer may have revealed more
            if not acted:
                # Nothing left to do *yet*. A revealed field can take longer than
                # one settle to render, and concluding early submits a form that
                # looks complete but is not — which reads as "Please make a
                # selection" on a step whose controls are all ng-valid.
                self.page.wait_for_timeout(SETTLE_MS * 2)
                confirm = self.scan()
                if signature(confirm) == signature(scan):
                    return confirm
                scan = confirm
                continue
            self.page.wait_for_timeout(SETTLE_MS)
            scan = self.scan()
        return scan

    def unfilled(self, scan: dict) -> list[str]:
        """Visible controls still empty that no mapping covers — the real gaps."""
        out = []
        groups: dict[str, list[dict]] = {}
        for c in scan["controls"]:
            if c["kind"] == "radio":
                groups.setdefault(c.get("name") or c.get("id") or "?", []).append(c)
                continue
            if c["kind"] == "checkbox":
                continue
            if not is_blank(c.get("value")):
                continue
            if self.value_for(c) is None:
                out.append(c.get("id") or c.get("fcn") or "?")
        # Radio groups were skipped here, so an unanswered question with no
        # mapping produced no gap and no error — the step just refused to advance
        # with nothing naming the culprit.
        for name, members in groups.items():
            if any(m.get("checked") for m in members):
                continue
            labels = [m.get("label", "")[:30] for m in members]
            out.append(f"{name} (radio; options={labels})")
        return out

    # Continue is #btnSave on the first step but not on every step, so it is
    # located by role rather than assumed: primary styling or Continue/Next text,
    # never Back.
    FORWARD_TEXT = ("continue", "next", "get quote", "see my quote", "start quote")
    BACK_TEXT = ("back", "previous", "cancel")

    def forward_button(self, scan: dict | None = None) -> str | None:
        scan = scan or self.scan()
        for b in scan.get("buttons", []):
            if b["id"] == "btnSave" and not b["disabled"]:
                return "#btnSave"
        for b in scan.get("buttons", []):
            text = (b["text"] or "").lower()
            if b["disabled"] or any(x in text for x in self.BACK_TEXT):
                continue
            if any(x in text for x in self.FORWARD_TEXT) or "btn-primary" in b["cls"]:
                return f"#{b['id']}" if b["id"] else f"button:has-text(\"{b['text']}\")"
        return None

    def advance(self) -> bool:
        """Click Continue; True when the step actually changed."""
        self.dismiss_overlay()
        before = signature(self.scan())
        # Continue is re-rendered between steps, so it can be briefly absent;
        # waiting for it beats failing the record on a rendering gap.
        target, waited = None, 0
        while waited < RENDER_MS:
            target = self.forward_button()
            if target:
                break
            self.page.wait_for_timeout(2000)
            waited += 2000
        if not target:
            scan = self.scan()
            raise Retryable(
                f"no forward button at {self.page.url} — buttons="
                f"{[b['text'] for b in scan.get('buttons', [])]}")
        for attempt in (1, 2):
            try:
                self.page.click(target, timeout=15000)
                break
            except Exception as exc:
                if attempt == 2:
                    raise UIFlowError(
                        f"Continue not clickable: {type(exc).__name__}") from exc
                self.dismiss_overlay()
                self.page.wait_for_timeout(2000)
        deadline = STEP_TIMEOUT_MS
        while deadline > 0:
            self.page.wait_for_timeout(2000)
            deadline -= 2000
            if signature(self.scan()) != before:
                return True
        return False


# The journey asks for annual kilometres as a band, while the input carries a
# number, so the number has to be placed into the band the UI offers.
KM_BANDS = (
    (5000, "0-5,000km"),
    (10000, "5,001-10,000km"),
    (15000, "10,001-15,000km"),
    (20000, "15,001-20,000km"),
    (30000, "20,001-30,000km"),
)


def _norm_option(text: str) -> str:
    """Compare option text ignoring case, punctuation and filler words."""
    words = re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()).split()
    drop = {"a", "an", "the", "in", "on", "at", "of", "and", "km", "kms"}
    return " ".join(w for w in words if w not in drop)


def km_band(raw: str | int | float | None) -> str:
    """Return the band label for an annual-kilometres input.

    The input usually already IS a band ("5,001-10,000KM"), which float() cannot
    parse — it fell back to 0 and selected "0-5,000km", understating mileage on
    every record and changing the premium. A band is passed through; only a bare
    number gets bucketed.
    """
    text = str(raw or "").strip()
    if "-" in text or "+" in text:
        return text
    try:
        km = float(text.replace(",", "") or 0)
    except ValueError:
        km = 0
    for ceiling, label in KM_BANDS:
        if km <= ceiling:
            return label
    return "30,001+km"


MONTHS = ("January", "February", "March", "April", "May", "June",
          "July", "August", "September", "October", "November", "December")


def _int_str(raw: str) -> str:
    """"2026.0" -> "2026"; the input carries these as floats."""
    try:
        return str(int(float(raw)))
    except (TypeError, ValueError):
        return ""


def _month_name(raw: str) -> str:
    """"7.0" -> "July"; the journey lists months by name."""
    try:
        return MONTHS[int(float(raw)) - 1]
    except (TypeError, ValueError, IndexError):
        return ""


def _age_started(age: str, years_held: str) -> str:
    """Age they began driving = current age - years licensed."""
    try:
        started = int(float(age or 0)) - int(float(years_held or 0))
    except ValueError:
        return ""
    return str(started) if started >= 15 else ""


def build_answers(rec: dict) -> dict:
    """Map input columns onto the journey's control ids.

    Keys are matched against a control's id by `MotorsJourney.value_for` —
    exact, then longest suffix, then longest substring. Values are the visible
    option text, since that is what the UI matches on.

    The constants mirror what the July API flow sent (motors_v2.py), so the UI
    journey answers the same questions the same way rather than inventing new
    ones. Anything the journey asks that is not here is reported as UNMAPPED —
    silence would mean an unanswered question and a stuck quote.
    """
    g = lambda k, d="": (rec.get(k) or d).strip()

    return {
        # ── find your car ─────────────────────────────────────────────
        "selected-lookup-option": "Make & model",
        "-make": g("PG_GG_MAKE"),
        "-year": g("VEHICLE_YEAR"),
        "-model": g("PG_GG_FAMILY"),
        "-type": g("PG_GG_VARIANT"),
        "-colour": g("COLOUR"),

        # ── tell us about the car ─────────────────────────────────────
        "car-condition": "Used - Already Owner",
        "current-vehicle-location": "Yes",
        "current-insurance-cover": g("PREVIOUSINSURER_COVER"),
        "current-existing-damage": "No",

        # ── cover ─────────────────────────────────────────────────────
        "-cover-type": g("COVER_TYPE"),
        "cover-type-code": g("COVER_TYPE"),
        # One date field, not three parts as the API flow sent it.
        "risk-start-date": "{:02d}/{:02d}/{}".format(
            int(float(g("POLICY_START_DAY", "1"))),
            int(float(g("POLICY_START_MONTH", "1"))),
            g("POLICY_START_YEAR", "2026")),

        # ── usage / kilometres ────────────────────────────────────────
        "-vehicle-use": g("PG_VEHICLEUSE1"),
        # Radio groups are named "<page>-selected-<thing>", so key on that shape.
        "selected-usage": g("PG_VEHICLEUSE1"),
        "selected-parking": g("PG_GARAGE"),
        "selected-licence": g("LICENCE_TYPE"),
        "selected-employment": g("EMPLOYMENT"),
        "-days-per-week": g("DAYSPERWEEK"),
        "how-many-days-a-week-commuting": str(int(float(g("DAYSPERWEEK", "5") or 5))),
        # The UI words this as Regular/No commuting, not Yes/No — the API flow's
        # COMMUTING=Yes has no matching option text.
        "used-for-commuting": ("Regular commuting" if g("DAYSPERWEEK") not in ("", "0")
                               else "No commuting"),
        "-commute-distance": "25 Up To 50km",
        "-distance-to-work": "25 Up To 50km",
        "-annual-km": g("PG_ANNUALKM"),
        "selected-kilometers-per-year": km_band(g("PG_ANNUALKM")),
        "-kilometres": g("PG_ANNUALKM"),

        # ── parking ───────────────────────────────────────────────────
        # Parking location dropdowns take the garage description; the address and
        # suburb autocompletes on the same step take an address. Keying both on
        # "-parking" typed "GARAGE - PRIVATE AND LOCKED" into the address lookup,
        # which unsurprisingly found nothing.
        "parking-location": g("PG_GARAGE"),
        "night-parking-location": g("PG_GARAGE"),
        "daytime-parking-location": g("PG_GARAGE"),
        "-garage": g("PG_GARAGE"),
        "-address": f'{g("ADDRESS")} {g("SUBURB")} {g("STATE")} {g("POSTCODE")}'.strip(),
        "-suburb": f'{g("SUBURB")} {g("STATE")} {g("POSTCODE")}'.strip(),
        "-postcode": g("POSTCODE"),
        "-state": g("STATE"),

        # ── policyholder ──────────────────────────────────────────────
        "-first-name": g("FIRST_NAME_DUMMY"),
        "-last-name": g("LAST_NAME_DUMMY"),
        "-gender": g("PG_GENDER"),
        # One field, not three parts as the API flow sent it.
        "date-of-birth": "{:02d}/{:02d}/{}".format(
            int(float(g("DOB_DUMMY_DAY", "1"))),
            int(float(g("DOB_DUMMY_MONTH", "1"))),
            g("DOB_DUMMY_YEAR", "1980")),
        "-dob-day": g("DOB_DUMMY_DAY"),
        "-dob-month": g("DOB_DUMMY_MONTH"),
        "-dob-year": g("DOB_DUMMY_YEAR"),
        # The UI journey demands contact details the API flow never needed, and
        # the motors input carries none. These are deliberately unusable: the
        # domain is IANA-reserved so the address cannot receive mail, and the
        # number is the conventional all-zeros placeholder — nothing here can
        # reach a real person. Override with YOUI_QUOTE_EMAIL / YOUI_QUOTE_MOBILE.
        # Plus-addressing and dots in the local part were rejected as invalid, so
        # the sample number is reduced to alphanumerics.
        "-email": os.getenv(
            "YOUI_QUOTE_EMAIL",
            "wmcquote{}@example.com".format(
                re.sub(r"[^A-Za-z0-9]", "", g("Sample_Number")) or "sample")),
        "-mobile-number": os.getenv("YOUI_QUOTE_MOBILE", "0400000000"),
        "-phone-number": os.getenv("YOUI_QUOTE_MOBILE", "0400000000"),

        # ── driving details ───────────────────────────────────────────
        "-licence-type": g("LICENCE_TYPE"),
        "-years-held-licence": g("PC_YEARSHELDLICENSE"),
        # The journey asks the age they started driving; the input carries current
        # age and years held, so it is the difference.
        "age-start-driving": _age_started(g("PC_AGE"), g("PC_YEARSHELDLICENSE")),
        # The API flow answered No to both of these.
        "license-suspended": "No",
        "licence-suspended": "No",
        "own-another-vehicle": "No",
        "-demerit": "0",
        "-mobile-phone": "Never",

        # ── incidents ─────────────────────────────────────────────────
        # INCIDENT_CAUSE1 already uses Youi's own wording ("NOT AT FAULT
        # COLLISION" -> "Not At Fault Collision"), so option matching handles it
        # once case and punctuation are normalised. "NO CLAIM" means no incidents,
        # in which case the follow-up fields never appear.
        "incident-history-group-has-any": (
            "No" if g("INCIDENT_CAUSE1") in ("", "NO CLAIM") else "Yes"),
        "-incidenttype": g("INCIDENT_CAUSE1"),
        "-incidentyearkey": _int_str(g("DATE_INCIDENT_YEAR")),
        "-incidentmonthkey": _month_name(g("DATE_INCIDENT_MONTH")),
        "-incident-cause": g("INCIDENT_CAUSE1"),
        "-claim-type": g("NPG_TYPE_OF_CLAIM"),

        # ── employment ────────────────────────────────────────────────
        "-employment": g("EMPLOYMENT"),
        "-occupation": g("OCCUPATION"),

        # ── insurance history ─────────────────────────────────────────
        "-previous-insurer": g("PG_PREVIOUSINSURER"),
        "-years-insured": g("PC_YRSINSURED"),
        "-held-insurance": g("HELD_INS_2W"),
        "never-been-covered": "Never Been Covered",

        # ── declarations: the July flow answered No to all of these ───
        "-modifications": "No",
        "-finance": "No",
        "-duty-of-disclosure": "No",
    }

# ── vehicle identification ────────────────────────────────────────────
# The find-your-car dropdowns are dynamic: for one car "Type" reads
# "GX - 5 dr - Hatchback - FWD" (badge + body) and for another
# "4 dr - Auto - CVT Auto - 2.4L" (body + transmission + engine). No single input
# column can drive them, so options are scored against every vehicle attribute we
# hold and an ambiguous result is refused rather than guessed — picking the wrong
# variant yields a plausible premium for the wrong car, which is worse than no row.

# Scoped to the find-your-car section: a bare "-type" would also catch, say, a
# licence-type dropdown and score it as if it were a vehicle.
VEHICLE_SECTION = ("find-your-car", "lookup-by-make-and-model")
VEHICLE_SELECT_HINTS = ("-type", "-style", "-model-code", "-description",
                        "-badge", "-series", "-variant")


def is_vehicle_select(control_id: str) -> bool:
    cid = (control_id or "").lower()
    return (any(sec in cid for sec in VEHICLE_SECTION)
            and any(h in cid for h in VEHICLE_SELECT_HINTS))


def _trans_class(raw: str) -> str:
    t = (raw or "").upper()
    if "CVT" in t or "CONTINUOUS" in t or "CONSTANT VARIABLE" in t:
        return "CVT"
    if "DUAL CLUTCH" in t or "DCT" in t:
        return "DCT"
    if "MANUAL" in t:
        return "MANUAL"
    if "AUTO" in t:
        return "AUTO"
    return ""


def _drive_class(variant: str, body: str) -> str:
    """FWD vs AWD from the variant text.

    The Style dropdown can differ only by drivetrain ("Wagon - AWD - Petrol" vs
    "Wagon - FWD - Petrol"), and the input spells it inside the variant as 2WD or
    AWD. Drivetrain is a rating factor, so getting it wrong misprices the quote.
    """
    t = f"{variant} {body}".upper()
    if re.search(r"\bAWD\b|\b4WD\b|\b4X4\b|ALL WHEEL", t):
        return "AWD"
    if re.search(r"\b2WD\b|\bFWD\b|\bRWD\b|FRONT WHEEL", t):
        return "FWD"
    return ""


def vehicle_signals(rec: dict) -> dict:
    g = lambda k: (rec.get(k) or "").strip().upper()
    body = g("PG_GG_BODYSTYLE")
    doors = ""
    m = re.match(r"(\d)\s*D\b", body)
    if m:
        doors = m.group(1)
    badge = [w for w in re.split(r"[^A-Z0-9]+", g("PG_GG_VARIANT")) if len(w) > 1]
    return {
        "engsize": g("PG_GG_ENGSIZE"),
        "trans": _trans_class(g("PG_GG_TRANS")),
        "doors": doors,
        "body_words": [w for w in re.split(r"[^A-Z]+", body) if len(w) > 2],
        "badge": badge,
        "variant_full": g("PG_GG_VARIANT"),
        "drive": _drive_class(g("PG_GG_VARIANT"), body),
        "series": g("PG_GG_SERIES"),
    }


def score_vehicle_option(text: str, sig: dict) -> int:
    """Score one dropdown option against the vehicle we are trying to match."""
    t = (text or "").upper()
    score = 0
    if sig["engsize"] and sig["engsize"] in t:
        score += 4                      # engine size is the sharpest signal
    if sig["trans"]:
        if sig["trans"] == "CVT" and "CVT" in t:
            score += 3
        elif sig["trans"] == "MANUAL" and "MANUAL" in t:
            score += 3
        elif sig["trans"] == "DCT" and ("DCT" in t or "DUAL CLUTCH" in t):
            score += 3
        elif sig["trans"] == "AUTO" and "AUTO" in t and "CVT" not in t:
            score += 2
        # a manual option for an automatic car (or vice versa) is disqualifying
        if sig["trans"] == "MANUAL" and "MANUAL" not in t and "AUTO" in t:
            score -= 4
        if sig["trans"] in ("AUTO", "CVT", "DCT") and "MANUAL" in t:
            score -= 4
    if sig.get("drive"):
        drive_words = {"FWD": r"\b(FWD|2WD)\b", "AWD": r"\b(AWD|4WD|4X4)\b"}
        if re.search(drive_words.get(sig["drive"], rf"\b{sig['drive']}\b"), t):
            score += 3
        elif re.search(r"\b(AWD|FWD|RWD|4WD|2WD)\b", t):
            score -= 4          # option states a different drivetrain
    if sig["doors"] and re.search(rf"\b{sig['doors']}\s*(DR|DOOR)", t):
        score += 1
    for word in sig["body_words"]:
        if word in t:
            score += 1
    for word in sig["badge"]:
        if re.search(rf"\b{re.escape(word)}\b", t):
            score += 3
    # "GX" and "GX Hybrid" both contain the badge, so an exact match on the
    # option's leading segment is what separates the base variant from its
    # siblings; without it the pair ties and the choice is (rightly) refused.
    if sig["variant_full"]:
        lead = t.split(" - ")[0].strip()
        if _norm_option(lead) == _norm_option(sig["variant_full"]):
            score += 5
    if sig["series"]:
        if sig["series"] in t:
            score += 4              # full series, e.g. "ZL MY19"
        elif sig["series"].split()[0] in t:
            score += 1              # family only, e.g. "ZL"
    return score


def best_vehicle_option(options: list[str], sig: dict) -> str | None:
    """Highest-scoring option, or None when the result is not clear-cut."""
    if not options:
        return None
    scored = sorted(((score_vehicle_option(o, sig), o) for o in options),
                    key=lambda x: -x[0])
    if len(scored) == 1:
        return scored[0][1] if scored[0][0] > 0 else None
    (top, best), (runner, _) = scored[0], scored[1]
    if top <= 0 or top == runner:
        return None                     # ambiguous — refuse rather than guess
    return best

