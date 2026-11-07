//! NTP configuration shared by the settings API and boot helper.

use crate::sys::{ipv4, Result};
use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Default, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Mode {
    #[default]
    Auto,
    Manual,
}

#[derive(Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TimeConfig {
    pub mode: Mode,
    pub servers: Vec<String>,
}

impl TimeConfig {
    pub fn validate(&self) -> Result<()> {
        if self.servers.len() > 16 || (self.mode == Mode::Manual && self.servers.is_empty()) {
            return Err(());
        }
        for server in &self.servers {
            let ip = ipv4(server.as_bytes())?;
            if !(ip[0] == 10
                || (ip[0] == 172 && (16..=31).contains(&ip[1]))
                || (ip[0] == 192 && ip[1] == 168))
            {
                return Err(());
            }
        }
        Ok(())
    }

    pub fn read(path: &std::ffi::OsStr) -> Result<Self> {
        let bytes = std::fs::read(path).map_err(|_| ())?;
        let config: Self = serde_json::from_slice(&bytes).map_err(|_| ())?;
        config.validate()?;
        Ok(config)
    }
}

pub fn print(path: &std::ffi::OsStr) -> Result<()> {
    let config = TimeConfig::read(path)?;
    println!(
        "{}",
        if config.mode == Mode::Manual {
            "manual"
        } else {
            "auto"
        }
    );
    println!("{}", config.servers.join(" "));
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn manual_time_requires_private_servers() {
        for input in [
            r#"{"mode":"manual","servers":[]}"#,
            r#"{"mode":"manual","servers":["8.8.8.8"]}"#,
        ] {
            let config: TimeConfig = serde_json::from_str(input).unwrap();
            assert!(config.validate().is_err());
        }
        let config: TimeConfig =
            serde_json::from_str(r#"{"mode":"manual","servers":["192.168.1.1"]}"#).unwrap();
        config.validate().unwrap();
        assert!(serde_json::from_str::<TimeConfig>(r#"{"mode":"other","servers":[]}"#).is_err());
    }
}
