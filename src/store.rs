//! Typed JSON persistence with private reads and atomic file replacement.

use crate::sys::{self, Result};
use serde::{de::DeserializeOwned, Serialize};

pub fn load<T: DeserializeOwned>(path: &[u8]) -> Result<(T, libc::stat)> {
    let mut bytes = Vec::new();
    let metadata = sys::read_private(path, &mut bytes)?;
    let value = serde_json::from_slice(&bytes).map_err(|_| ())?;
    Ok((value, metadata))
}

pub fn save(path: &[u8], value: &impl Serialize) -> Result<()> {
    let bytes = serde_json::to_vec_pretty(value).map_err(|_| ())?;
    sys::atomic(path, &bytes)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::{
        ffi::OsStrExt,
        fs::{symlink, PermissionsExt},
    };

    #[test]
    fn private_atomic_json_round_trip_and_symlink_rejection() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("settings.json");
        let bytes = path.as_os_str().as_bytes();
        let expected = serde_json::json!({"password": "quote\"\\secret", "port": 1883});
        save(bytes, &expected).unwrap();
        let (actual, _): (serde_json::Value, _) = load(bytes).unwrap();
        assert_eq!(actual, expected);
        assert_eq!(
            std::fs::metadata(&path).unwrap().permissions().mode() & 0o777,
            0o600
        );
        save(bytes, &serde_json::json!({"port": 1884})).unwrap();
        let (actual, _): (serde_json::Value, _) = load(bytes).unwrap();
        assert_eq!(actual["port"], 1884);
        let link = directory.path().join("link.json");
        symlink(&path, &link).unwrap();
        assert!(load::<serde_json::Value>(link.as_os_str().as_bytes()).is_err());
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644)).unwrap();
        assert!(load::<serde_json::Value>(bytes).is_err());
    }
}
