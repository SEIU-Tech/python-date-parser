//! Python bindings for the `date_parser` extension.
//!
//! Exposes a single function, [`parse`], that takes a list of raw date
//! strings and returns a JSON-encoded array of ISO-8601 representations.
//! Inputs that fail to parse produce `null` in the output array.
//!
//! Timezone note: parsing goes through `dateparser::parse_with(..., &Utc,
//! midnight)`, so all results are normalized to UTC and date-only inputs
//! default to midnight UTC. Pure-numeric inputs are detected and treated
//! as Unix timestamps (seconds for 10 digits, milliseconds for 13,
//! microseconds for 16, nanoseconds for 19) since the underlying
//! `dateparser` crate would otherwise interpret them in the local
//! timezone. ISO-8601 datetimes written with the `T` separator and no
//! offset (e.g. `"2026-09-18T01:02:03"`) are normalized to the space
//! form before parsing, since the crate otherwise rejects them. These
//! are known differences from the Python `dateparser` reference, which
//! preserves the original offset and uses the current time of day for
//! date-only inputs.

use chrono::{DateTime, NaiveTime, Utc};
use pyo3::prelude::*;

/// Format a `chrono::DateTime<Utc>` as `YYYY-MM-DD HH:MM:SS[.fff]+HH:MM`.
///
/// Fractional seconds are included only when non-zero, and trailing zeros
/// are trimmed (e.g. `.052282000` -> `.052282`).
fn format_datetime(dt: &DateTime<Utc>) -> String {
    let base = dt.format("%Y-%m-%d %H:%M:%S%:z").to_string();
    let nanos = dt.timestamp_subsec_nanos();
    if nanos == 0 {
        return base;
    }
    // Build "fff" without trailing zeros, then splice it in before the
    // timezone offset.
    let frac_str = format!("{:09}", nanos);
    let frac = frac_str.trim_end_matches('0');
    // Find the trailing timezone offset (last '+' or '-' before the end).
    let tz_idx = base.rfind(['+', '-']).unwrap_or(base.len());
    let (head, tail) = base.split_at(tz_idx);
    format!("{}.{}{}", head, frac, tail)
}

/// Encode a list of optional strings as a JSON array.
///
/// Strings are escaped per RFC 8259; `None` values become `null`.
fn to_json_array(items: &[Option<String>]) -> String {
    let mut out = String::with_capacity(items.len() * 16);
    out.push('[');
    for (i, item) in items.iter().enumerate() {
        if i > 0 {
            out.push(',');
        }
        match item {
            Some(s) => {
                out.push('"');
                for c in s.chars() {
                    match c {
                        '"' => out.push_str("\\\""),
                        '\\' => out.push_str("\\\\"),
                        '\n' => out.push_str("\\n"),
                        '\r' => out.push_str("\\r"),
                        '\t' => out.push_str("\\t"),
                        c if (c as u32) < 0x20 => {
                            use std::fmt::Write as _;
                            let _ = write!(out, "\\u{:04x}", c as u32);
                        }
                        c => out.push(c),
                    }
                }
                out.push('"');
            }
            None => out.push_str("null"),
        }
    }
    out.push(']');
    out
}

/// Attempt to interpret ``raw`` as a Unix timestamp based on digit count.
///
/// The ``dateparser`` crate interprets pure-numeric strings in the local
/// timezone, which is rarely what we want. Detect them here and convert
/// explicitly to UTC.
fn try_unix_timestamp(raw: &str) -> Option<DateTime<Utc>> {
    if !raw.chars().all(|c| c.is_ascii_digit()) {
        return None;
    }
    let n: i64 = raw.parse().ok()?;
    // Heuristic by digit count: 10=seconds, 13=ms, 16=µs, 19=ns.
    let (secs, nanos) = match raw.len() {
        10 => (n, 0_u32),
        13 => (n / 1000, ((n % 1000) * 1_000_000) as u32),
        16 => (n / 1_000_000, ((n % 1_000_000) * 1_000) as u32),
        19 => (n / 1_000_000_000, (n % 1_000_000_000) as u32),
        _ => return None,
    };
    DateTime::<Utc>::from_timestamp(secs, nanos)
}

/// Detect the ISO-8601 datetime form ``YYYY-MM-DDTHH:MM:SS[.fff]`` that
/// lacks a timezone designator (``Z`` or ``±HH:MM``).
///
/// Returns ``true`` only for inputs that exactly match the canonical
/// layout — no other date formats (RFC-2822, free-form month names,
/// etc.) qualify, so we never accidentally rewrite inputs the underlying
/// parser would otherwise accept.
fn is_iso8601_no_tz(raw: &str) -> bool {
    let bytes = raw.as_bytes();
    // Minimum length is 19 chars for `YYYY-MM-DDTHH:MM:SS`.
    if bytes.len() < 19 {
        return false;
    }
    // `T` must sit at index 10 (right after the date).
    if bytes[10] != b'T' {
        return false;
    }
    // Date and time separators in the expected positions.
    if bytes[4] != b'-' || bytes[7] != b'-' {
        return false;
    }
    if bytes[13] != b':' || bytes[16] != b':' {
        return false;
    }
    // After the seconds, allow exactly one optional `.` followed by
    // digits. Anything else (``Z``, ``+``, ``-``, or a stray character)
    // means a timezone designator or a non-ISO layout — leave the input
    // alone in either case.
    let mut saw_dot = false;
    for &b in &bytes[19..] {
        if b == b'.' {
            if saw_dot {
                return false;
            }
            saw_dot = true;
        } else if !b.is_ascii_digit() {
            return false;
        }
    }
    true
}

/// Replace the ``T`` separator in an ISO-8601 datetime with a space,
/// leaving other inputs untouched.
///
/// The ``dateparser`` crate accepts ``YYYY-MM-DD HH:MM:SS`` but rejects
/// the ``T`` form unless a timezone designator (``Z`` or ``±HH:MM``) is
/// appended. The no-offset form is legal ISO-8601 and common in APIs,
/// so we normalize it before falling through to the parser.
fn normalize_iso8601_separator(raw: &str) -> std::borrow::Cow<'_, str> {
    if is_iso8601_no_tz(raw) {
        let mut s = String::with_capacity(raw.len());
        s.push_str(&raw[..10]);
        s.push(' ');
        s.push_str(&raw[11..]);
        std::borrow::Cow::Owned(s)
    } else {
        std::borrow::Cow::Borrowed(raw)
    }
}

/// Parse a list of raw date strings and return a JSON array of ISO-8601 strings.
///
/// Each input is parsed independently by the [`dateparser`] crate; inputs
/// that fail to parse produce `null` in the corresponding output slot.
///
/// All parsed datetimes are normalized to UTC. Date-only inputs (e.g.
/// `"2026-09-18"`) default to midnight UTC rather than the current time.
/// Pure-numeric inputs are detected as Unix timestamps and treated as UTC.
/// ISO-8601 inputs that use the ``T`` separator with no offset (e.g.
/// `"2026-09-18T01:02:03"`) are normalized to a space separator before
/// being handed to the parser.
#[pyfunction]
fn parse(raw_dates: Vec<String>) -> PyResult<String> {
    let midnight = NaiveTime::from_hms_opt(0, 0, 0)
        .expect("00:00:00 is a valid NaiveTime; qed");
    let results: Vec<Option<String>> = raw_dates
        .iter()
        .map(|raw| {
            let normalized = normalize_iso8601_separator(raw);
            try_unix_timestamp(&normalized)
                .or_else(|| dateparser::parse_with(&normalized, &Utc, midnight).ok())
                .map(|dt| format_datetime(&dt))
        })
        .collect();
    Ok(to_json_array(&results))
}

#[pymodule]
mod date_parser {
    #[pymodule_export]
    use super::parse;
}
