//! Python bindings for the `date-parser` extension.
//!
//! The placeholder `parse_date` function exists so that the build pipeline
//! (maturin → cargo → wheel) can be verified end-to-end before the real
//! date-parsing logic lands.

use pyo3::prelude::*;

#[pyfunction]
fn parse_date(text: &str) -> PyResult<String> {
    // TODO: replace with actual date-parsing logic.
    Ok(text.to_owned())
}

#[pymodule]
mod date_parser {
    #[pymodule_export]
    use super::parse_date;
}
