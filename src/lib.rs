//! Python bindings for the `date_parser` extension.
//!
//! Exposes three functions:
//!
//! - [`parse`] parses a single raw date string and returns its ISO-8601
//!   representation (or `None` if the input cannot be parsed).
//! - [`parse_list`] takes a Python list of raw date strings and returns
//!   a list of ISO-8601 strings (or `None` for inputs that can't be
//!   parsed). The returned list matches the input length and order.
//! - [`parse_series`] takes a Polars `Series` of string dtype and
//!   returns a Polars `Series` of `pl.Datetime("ns")`. It works
//!   directly on the underlying Arrow buffer via `pyo3-polars`, so
//!   there is no Python-level iteration. Unparseable strings become
//!   null values in the output series.
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
use polars::prelude::*;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3_polars::PySeries;

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

/// Parse one raw date string into a UTC datetime, or ``None`` on failure.
///
/// Centralizes the parse pipeline so [`parse`] and [`parse_series`] stay
/// in sync: ISO-8601 separator normalization, Unix-timestamp fast path,
/// then `dateparser::parse_with` as the final fallback.
fn parse_one(raw: &str, midnight: NaiveTime) -> Option<DateTime<Utc>> {
    let normalized = normalize_iso8601_separator(raw);
    try_unix_timestamp(&normalized)
        .or_else(|| dateparser::parse_with(&normalized, &Utc, midnight).ok())
}

/// Parse a single raw date string and return its ISO-8601 representation.
///
/// Returns ``None`` if the input cannot be parsed by the underlying
/// parser. The same UTC normalization rules as [`parse_list`] apply:
/// date-only inputs default to midnight UTC, pure-numeric inputs are
/// treated as Unix timestamps, and ISO-8601 ``T``-separated datetimes
/// without an offset are normalized to a space separator first.
#[pyfunction]
fn parse(raw: &str) -> Option<String> {
    let midnight = NaiveTime::from_hms_opt(0, 0, 0)
        .expect("00:00:00 is a valid NaiveTime; qed");
    parse_one(raw, midnight).map(|dt| format_datetime(&dt))
}

/// Parse a list of raw date strings and return a list of ISO-8601 strings.
///
/// Each input is parsed independently by the [`dateparser`] crate; inputs
/// that fail to parse produce ``None`` in the corresponding output slot.
/// The returned list matches the length and order of the input list.
///
/// All parsed datetimes are normalized to UTC. Date-only inputs (e.g.
/// `"2026-09-18"`) default to midnight UTC rather than the current time.
/// Pure-numeric inputs are detected as Unix timestamps and treated as UTC.
/// ISO-8601 inputs that use the ``T`` separator with no offset (e.g.
/// `"2026-09-18T01:02:03"`) are normalized to a space separator before
/// being handed to the parser.
#[pyfunction]
fn parse_list(raw_dates: Vec<String>) -> Vec<Option<String>> {
    let midnight = NaiveTime::from_hms_opt(0, 0, 0)
        .expect("00:00:00 is a valid NaiveTime; qed");
    raw_dates
        .iter()
        .map(|raw| parse_one(raw, midnight).map(|dt| format_datetime(&dt)))
        .collect()
}

/// Parse a Polars string Series into a Datetime Series at Arrow speed.
///
/// The input must be a Series with `pl.String` dtype. The output is a
/// Series with `pl.Datetime("ns")` (naive UTC, nanosecond precision):
/// the same instant as the existing JSON-returning [`parse`] function,
/// just expressed as native Polars/Arrow storage instead of ISO-8601
/// strings. Unparseable strings become null values rather than
/// rejecting the whole Series.
///
/// This entry point goes through the `pyo3-polars` FFI, so the
/// underlying Arrow buffer is handed to Rust without any Python-level
/// iteration or list materialization. The Rust side walks the
/// `StringChunked` directly, builds an `Int64` chunked array of
/// nanosecond timestamps, then casts it to the `Datetime` dtype.
#[pyfunction]
fn parse_series(pys: PySeries) -> PyResult<PySeries> {
    let series: Series = pys.into();
    let str_ca = series
        .str()
        .map_err(
            |e| PyValueError::new_err(
                format!("expected String Series, got {:?}: {e}", series.dtype())
            )
        )?;

    let midnight = NaiveTime::from_hms_opt(0, 0, 0)
        .expect("00:00:00 is a valid NaiveTime; qed");

    // Build nanoseconds directly. ``None`` becomes a real Arrow null,
    // not a sentinel zero, so the resulting Datetime Series preserves
    // the null bitmap.
    let mut builder = PrimitiveChunkedBuilder::<Int64Type>::new(
        PlSmallStr::from_static("parsed_datetime"),
        str_ca.len(),
    );
    for opt_s in str_ca.iter() {
        match opt_s.and_then(|s| parse_one(s, midnight)) {
            Some(dt) => match dt.timestamp_nanos_opt() {
                Some(nanos) => builder.append_value(nanos),
                None => builder.append_null(), // year > ~2262, beyond i64 nanos
            },
            None => builder.append_null(),
        }
    }

    let int_series: Series = builder.finish().into_series();
    let datetime_dtype = DataType::Datetime(TimeUnit::Nanoseconds, None);
    let out = int_series
        .cast(&datetime_dtype)
        .map_err(|e| PyValueError::new_err(format!("cast to Datetime failed: {e}")))?;

    Ok(PySeries(out))
}

#[pymodule]
mod date_parser {
    #[pymodule_export]
    use super::parse;
    #[pymodule_export]
    use super::parse_list;
    #[pymodule_export]
    use super::parse_series;
}
