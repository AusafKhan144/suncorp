#!/usr/bin/env python3
"""
NRMA Motor — Offer Replay
=========================================================================
Takes an ALREADY-AUTHENTICATED, captured `Experience3OfferProcessing` curl
(valid session cookies + quoteId + a complete `answers` body) as a reference
and, instead of driving a full quote from scratch, fires the follow-up offer
requests itself to collect the full pricing set — reusing the exact extraction
logic from `motors.py`.

It subclasses `NrmaExtractor` and overrides only:
  * transport   (`_request`)              — direct HTTP with the captured
                                            cookies/headers (no Zyte bootstrap)
  * offer body  (`_build_offer_answers`)  — clone the captured `answers` and
                                            mutate just the pricing knobs

Everything else (the base/extras/roadside/excess-ladder collection loop and
every billing extractor) is `motors.py`'s code, unchanged.

Usage:
    python -m NRMA.motors.offer_replay \
        --curl-file offer.curl \
        --sample 20021.2025 \
        [--toggle | --main] \
        [--out results.csv]

The captured session is short-lived (Akamai + x-iag-* tokens expire within
minutes) — run this soon after capturing the curl.
"""

import argparse
import copy
import json
import logging
import os
import re
import shlex
import sys
import time

import pandas as pd
import requests

from NRMA.motors.motors import NrmaExtractor, log

# Project root = .../Suncorp (this file is at .../Suncorp/NRMA/motors/offer_replay.py).
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_CURL = os.path.join(ROOT, "offer.curl")
DEFAULT_PERIOD = "202608"


def default_output(period: str, toggle: bool) -> str:
    """The real Motor output file for the given period + mode."""
    mode = "toggle" if toggle else "main"
    return os.path.join(
        ROOT, "NRMA", "outputs", period,
        f"Motor_NRMA_quarterly_{period}_{mode}.csv",
    )


# ─────────────────────────────────────────────────────────────────────────────
# curl parsing
# ─────────────────────────────────────────────────────────────────────────────
def parse_curl(curl_text: str) -> dict:
    """Parse a copy-as-cURL (bash) string into url / headers / cookies / body."""
    # Drop a leading `curl` token; shlex handles quotes and `\`-newline joins.
    tokens = shlex.split(curl_text.strip(), posix=True)
    if tokens and tokens[0] == "curl":
        tokens = tokens[1:]

    url = None
    headers: dict[str, str] = {}
    cookie_blob = ""
    body = None

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("-H", "--header"):
            i += 1
            raw = tokens[i]
            if ":" in raw:
                k, v = raw.split(":", 1)
                headers[k.strip().lower()] = v.strip()
        elif tok in ("-b", "--cookie"):
            i += 1
            cookie_blob = tokens[i]
        elif tok in ("--data-raw", "--data", "-d", "--data-binary"):
            i += 1
            body = tokens[i]
        elif tok in ("-X", "--request"):
            i += 1  # method — derived from the endpoint instead, skip
        elif tok.startswith("http://") or tok.startswith("https://"):
            url = tok
        i += 1

    if not url:
        raise ValueError("No URL found in curl")
    if body is None:
        raise ValueError("No --data-raw body found in curl")

    cookies = _parse_cookie_blob(cookie_blob)
    return {"url": url, "headers": headers, "cookies": cookies, "body": body}


def _parse_cookie_blob(blob: str) -> dict[str, str]:
    """`name=value; name2=value2; ...` → dict. Values may themselves contain
    `=` (e.g. base64/JWT cookies), so split on the FIRST `=` only."""
    cookies: dict[str, str] = {}
    for part in blob.split("; "):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            k, v = part.split("=", 1)
            cookies[k.strip()] = v
    return cookies


def _extract_quote_id(url: str) -> str:
    m = re.search(r"/nrma/motor/(QTEN\d+)/targetStep/", url)
    if not m:
        raise ValueError(f"Could not extract quoteId from URL: {url}")
    return m.group(1)


# ─────────────────────────────────────────────────────────────────────────────
# Replay subclass — reuse all of Motor's collection + extraction logic
# ─────────────────────────────────────────────────────────────────────────────
class OfferReplay(NrmaExtractor):
    def __init__(self, parsed_curl: dict, sample_number: str, toggle: bool):
        answers = json.loads(parsed_curl["body"]).get("answers", {})
        if not answers:
            raise ValueError("Captured body has no 'answers' object")

        # Minimal input_data: Sample_Number + the driver-history fields that the
        # reused roadside builder reads. (The offer builder is overridden and
        # clones the template, so it needs nothing from here.)
        input_data = {
            "Sample_Number": sample_number,
            "Age_licensed": answers.get("Parties.0.AgeLicenseAcquired", "18"),
            "Claim_5yrs": answers.get("Parties.0.HowManyPriorClaims", "0"),
            "Licence_disqualified": answers.get(
                "Parties.0.HowManyLicenseEvents", "0"
            ),
            "Second_driver_claim_5yrs": answers.get(
                "Parties.1.HowManyPriorClaims", "0"
            ),
            "Second_driver_licence_disqlf": answers.get(
                "Parties.1.HowManyLicenseEvents", "0"
            ),
            "Second_Driver_Age_Licensed": answers.get(
                "Parties.1.AgeLicenseAcquired", "18"
            ),
        }
        super().__init__(input_data, toggle=toggle)

        # Transport state (used by the overridden _request).
        self._captured_headers = self._clean_headers(parsed_curl["headers"])
        self._captured_cookies = parsed_curl["cookies"]
        self._http = requests.Session()

        # Reference the pre-built quote directly — skip init_session/steps 0-9.
        self.quote_id = _extract_quote_id(parsed_curl["url"])
        self._answers_template = answers

        # Seed the state that the reused collection loop + roadside payload read.
        self.default_excess = str(answers.get("BaseExcess") or "1000")

        # Vehicle / cover context (roadside PUT payload rebuilds from these).
        self.vehicle_info = answers.get("Vehicle")
        self.variant_search = answers.get("VariantSearch")
        self.fmt = answers.get("CoverStartDate")
        self.matched_address = answers.get("AddressLookup")
        self.parking_mapped = answers.get("WhereIsYourCarParked")
        self.yearly_km_mapped = answers.get("KilometresDriven")
        use = answers.get("WhatYouUseYourCarFor") or [None]
        self.vehicle_use_mapped = use[0] if isinstance(use, list) else use
        self.registered_gst = answers.get("RegisteredForGst", False)
        self.input_tax_claim = answers.get("ITCAmount")
        self.finance_info = answers.get("IsThereFinance", False)
        self.held = answers.get("HasCurrentInsurance", False)
        self.insurer = answers.get("CurrentInsurer")
        self.tenure = answers.get("CurrentInsurerTenure")

        # Primary party.
        self.party_id = answers.get("Parties.0.PartyId")
        self.temp_id = answers.get("Parties.0.TempId")
        self.drive = bool(answers.get("Parties.0.IsDriver", True))
        self.owner_type = answers.get("Parties.0.PartyType", "Individual")
        self.membership = bool(answers.get("Parties.0.IsCustomer", False))
        self.gender = answers.get("Parties.0.PartyGender")
        self.dob = answers.get("Parties.0.PartyDOB")
        self._primary_claim_list = self._claim_list(answers, "0")

        # Optional second driver.
        self.add_second_driver = "Parties.1.PartyId" in answers
        if self.add_second_driver:
            self.party_id_second = answers.get("Parties.1.PartyId")
            self.temp_id_second = answers.get("Parties.1.TempId")
            self.second_drive = bool(answers.get("Parties.1.IsDriver", True))
            self.second_owner_type = answers.get("Parties.1.PartyType", "Individual")
            self.second_membership = bool(answers.get("Parties.1.IsCustomer", False))
            self.second_gender = answers.get("Parties.1.PartyGender")
            self.second_dob = answers.get("Parties.1.PartyDOB")
            self._second_claim_list = self._claim_list(answers, "1")

        # Fill the profile columns from the curl so the upserted row stays
        # complete even for a sample not yet present in the output file.
        dd, mm, yyyy = self._split_dob(self.dob)
        self.results["DOB_DD"] = dd
        self.results["DOB_MM"] = mm
        self.results["DOB_YYYY"] = yyyy
        postcode = (answers.get("AddressLookup") or {}).get("postcode")
        if postcode:
            self.results["Postcode"] = postcode

        log.info(
            f"OfferReplay ready — quote={self.quote_id}, "
            f"sample={sample_number}, toggle={toggle}, "
            f"default_excess={self.default_excess}"
        )

    # ── helpers ──────────────────────────────────────────────────────────────
    @staticmethod
    def _split_dob(dob):
        """'1988-12-07' → ('7', '12', '1988') to match the CSV's DOB_* columns
        (which are integer-like, no leading zeros)."""
        if not dob or "-" not in str(dob):
            return "", "", ""
        try:
            y, m, d = str(dob).split("-")
            return str(int(d)), str(int(m)), str(int(y))
        except (ValueError, TypeError):
            return "", "", ""

    @staticmethod
    def _claim_list(answers: dict, prefix: str) -> list:
        event = answers.get(f"Parties.{prefix}.PriorClaimsEvent.0.Event")
        year = answers.get(f"Parties.{prefix}.PriorClaimsEvent.0.YearOccurred")
        if event and year:
            return [{"Event": event, "YearOccurred": year}]
        return []

    @staticmethod
    def _clean_headers(headers: dict) -> dict:
        """Keep the captured headers, but let requests manage the ones that must
        reflect this process's actual request."""
        drop = {"content-length", "host", "cookie"}
        return {k: v for k, v in headers.items() if k not in drop}

    # ── transport override — direct HTTP with the captured session ───────────
    def _request(self, url, method, payload, headers=None, cookies=None) -> dict:
        body = json.dumps(payload) if payload is not None else None
        self._request_count += 1
        short = url.split("/api/")[-1] if "/api/" in url else url

        attempts = 0
        while True:
            resp = self._http.request(
                method,
                url,
                headers=self._captured_headers,
                cookies=self._captured_cookies,
                data=body,
                timeout=60,
                verify=False,
            )
            # Carry any refreshed cookies forward within this session.
            if resp.cookies:
                self._captured_cookies.update(resp.cookies.get_dict())

            status = resp.status_code
            if status in self.RETRY_ON_STATUS and attempts < self.RETRY_MAX_ATTEMPTS - 1:
                attempts += 1
                log.warning(
                    f"  [REQ #{self._request_count}] HTTP {status} "
                    f"({attempts}/{self.RETRY_MAX_ATTEMPTS - 1}) — {short}"
                )
                time.sleep(self.RETRY_BACKOFF_BASE ** attempts)
                continue

            if status in (401, 403):
                raise RuntimeError(
                    f"HTTP {status} — {short}: session likely expired. "
                    f"Re-capture the curl and retry. Body: {resp.text[:200]}"
                )
            if status >= 400:
                raise RuntimeError(f"HTTP {status} — {short}: {resp.text[:300]}")

            return resp.json() if resp.text.strip() else {}

    # ── offer-body override — clone the captured template, vary pricing knobs ─
    def _build_offer_answers(
        self, cover_enum, excess, extras=None, agreed_value=None
    ) -> dict:
        extras = extras or []
        answers = copy.deepcopy(self._answers_template)

        answers["SelectCoverType"] = cover_enum
        answers["BaseExcess"] = str(excess)

        if cover_enum == "COMPREHENSIVE_PLUS":
            answers["SumInsured"] = "AGREED_VALUE"
            if agreed_value:
                answers["CarAgreedValue"] = str(agreed_value)
        else:
            answers["SumInsured"] = "MARKET_VALUE"
            answers.pop("CarAgreedValue", None)

        answers["SelectAdditionalCoverages"] = extras
        for key in self.EXTRAS_MAPPING:
            answers[f"AdditionalCoverages.{key}"] = ["true"] if key in extras else []

        return answers


# ─────────────────────────────────────────────────────────────────────────────
# Output
# ─────────────────────────────────────────────────────────────────────────────
def _is_blank(v) -> bool:
    return v is None or str(v).strip().lower() in ("", "nan", "none")


def upsert_row(path: str, sample: str, fields_order: list, results: dict):
    """Find the row for `sample` in the real output CSV and update its collected
    columns in place, preserving every other column and row. If the sample is
    not present, the row is appended. Non-destructive: only non-blank collected
    values overwrite, and existing columns outside `fields_order` are kept (so
    updating a toggle file in --main mode never drops the excess-ladder columns).
    """
    sample = str(sample).strip()
    if os.path.exists(path) and os.path.getsize(path) > 0:
        df = pd.read_csv(path, dtype=str).fillna("")
    else:
        df = pd.DataFrame(columns=fields_order)

    # Ensure every column we might write exists, preserving current order.
    for col in fields_order:
        if col not in df.columns:
            df[col] = ""

    if "Sample_Number" not in df.columns:
        df["Sample_Number"] = ""
    mask = df["Sample_Number"].astype(str).str.strip() == sample
    updates = {c: results.get(c) for c in fields_order if not _is_blank(results.get(c))}

    if mask.any():
        idx = df.index[mask][0]
        changed = 0
        for col, val in updates.items():
            df.at[idx, col] = str(val)
            changed += 1
        log.info(f"Updated sample {sample} in {path} ({changed} column(s))")
    else:
        log.warning(f"Sample {sample} not found in {path} — appending a new row")
        new_row = {c: "" for c in df.columns}
        new_row["Sample_Number"] = sample
        new_row.update({c: str(v) for c, v in updates.items()})
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)

    df.to_csv(path, index=False)


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Replay a captured NRMA offer curl and collect full pricing."
    )
    ap.add_argument(
        "--curl-file",
        default=DEFAULT_CURL,
        help=f"Captured curl file (default: {DEFAULT_CURL}). Use '-' to read stdin.",
    )
    ap.add_argument("--sample", required=True, help="Sample number to collect/update.")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument(
        "--toggle",
        dest="toggle",
        action="store_true",
        help="Toggle mode: also collect the excess ladder, per-extra premiums, "
        "and roadside. Updates the toggle output file by default.",
    )
    mode.add_argument(
        "--main",
        dest="toggle",
        action="store_false",
        help="Main mode (default): base cover prices only. Updates the main "
        "output file by default.",
    )
    ap.set_defaults(toggle=False)
    ap.add_argument(
        "--period",
        default=DEFAULT_PERIOD,
        help=f"Quarter folder for the default output path (default: {DEFAULT_PERIOD}).",
    )
    ap.add_argument(
        "--out",
        help="Output CSV to update in place (row matched by --sample; other "
        "rows/columns untouched). Default: the real Motor output for the "
        "period + mode.",
    )
    ap.add_argument(
        "--stdout",
        action="store_true",
        help="Print the collected row as JSON instead of writing to a file.",
    )
    args = ap.parse_args(argv)

    if args.curl_file == "-":
        curl_text = sys.stdin.read()
    else:
        with open(args.curl_file) as f:
            curl_text = f.read()
    if not curl_text.strip():
        ap.error("No curl provided (empty --curl-file / stdin).")

    parsed = parse_curl(curl_text)
    replay = OfferReplay(parsed, sample_number=args.sample, toggle=args.toggle)

    replay.step_collect_premiums()

    if args.stdout:
        print(json.dumps(replay.results, indent=2))
    else:
        out = args.out or default_output(args.period, args.toggle)
        upsert_row(out, args.sample, replay.fields_order, replay.results)
    return 0


if __name__ == "__main__":
    logging.getLogger().setLevel(logging.INFO)
    raise SystemExit(main())
