//! JSON serialization and validation at configuration boundaries.

use crate::sys::Result;
use serde::{Deserialize, Serialize};
use serde_json::Value;

pub fn text(bytes: &[u8]) -> Result<&str> {
    std::str::from_utf8(bytes).map_err(|_| ())
}

pub fn parse(bytes: &[u8]) -> Result<Value> {
    serde_json::from_slice(bytes).map_err(|_| ())
}

pub fn encode(value: &impl Serialize) -> Result<Vec<u8>> {
    serde_json::to_vec(value).map_err(|_| ())
}

/// Reject control characters in credential and settings fields after JSON decoding.
pub fn field(value: &Value, key: &str) -> Result<String> {
    let text = value.get(key).and_then(Value::as_str).ok_or(())?;
    if text.bytes().any(|byte| byte < 32 || byte == 127) {
        return Err(());
    }
    Ok(text.to_owned())
}

#[derive(Deserialize)]
struct CameraResponse {
    code: u8,
    value: Value,
}

pub fn camera_response(value: Value) -> Result<Value> {
    let responses: Vec<CameraResponse> = serde_json::from_value(value).map_err(|_| ())?;
    let response = responses.into_iter().next().ok_or(())?;
    if response.code != 0 {
        return Err(());
    }
    Ok(response.value)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn unicode_and_escaped_text_round_trip() {
        let value = parse(br#"{"name":"\uD83D\uDD14 \"door\" \\"}"#).unwrap();
        let name = field(&value, "name").unwrap();
        assert_eq!(text(name.as_bytes()).unwrap(), "🔔 \"door\" \\");
        assert_eq!(parse(&encode(&value).unwrap()).unwrap(), value);
    }

    #[test]
    fn settings_fields_reject_control_characters() {
        for input in [
            br#"{"password":"safe\nother=value"}"#.as_slice(),
            br#"{"password":"safe\u000dother=value"}"#,
            br#"{"password":12}"#,
        ] {
            assert!(field(&parse(input).unwrap(), "password").is_err());
        }
    }

    #[test]
    fn settings_fields_use_dynamic_storage() {
        let long_value = "a".repeat(4096);
        assert_eq!(
            field(&json!({"value": long_value}), "value").unwrap(),
            long_value
        );
    }

    #[test]
    fn malformed_json_and_failed_camera_responses_are_rejected() {
        assert!(parse(br#"{"ok":true} trailing"#).is_err());
        assert!(parse(br#"{"name":"\uD800"}"#).is_err());
        assert!(camera_response(json!([{"code":1,"value":{}}])).is_err());
        assert!(camera_response(json!([])).is_err());
        assert_eq!(
            camera_response(json!([{"code":0,"value":{"state":1}}])).unwrap(),
            json!({"state":1})
        );
    }
}
