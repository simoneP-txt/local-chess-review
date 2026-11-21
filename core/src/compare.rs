//! The first difference between two analyses read as JSON.
use serde_json::Value;

/// None when the values are equal with their JSON types (1 is not 1.0) and keys in any order;
/// else `<path>: <expected> != <actual>`, `<path>: missing`, `<path>: unexpected` or
/// `<path>: length <expected> != <actual>`, with a path like `moves[12].wp_drop`.
pub fn first_difference(expected: &Value, actual: &Value) -> Option<String> {
    difference_at("", expected, actual)
}

fn difference_at(path: &str, expected: &Value, actual: &Value) -> Option<String> {
    let key_path = |key: &str| {
        if path.is_empty() {
            key.to_string()
        } else {
            format!("{path}.{key}")
        }
    };
    match (expected, actual) {
        (Value::Object(expected), Value::Object(actual)) => {
            for (key, value) in expected {
                let found = match actual.get(key) {
                    None => Some(format!("{}: missing", key_path(key))),
                    Some(other) => difference_at(&key_path(key), value, other),
                };
                if found.is_some() {
                    return found;
                }
            }
            let extra = actual.keys().find(|key| !expected.contains_key(*key))?;
            Some(format!("{}: unexpected", key_path(extra)))
        }
        (Value::Array(expected), Value::Array(actual)) => {
            if expected.len() != actual.len() {
                return Some(format!(
                    "{path}: length {} != {}",
                    expected.len(),
                    actual.len()
                ));
            }
            expected
                .iter()
                .zip(actual)
                .enumerate()
                .find_map(|(i, (e, a))| difference_at(&format!("{path}[{i}]"), e, a))
        }
        _ if expected == actual => None,
        _ => Some(format!("{path}: {expected} != {actual}")),
    }
}
