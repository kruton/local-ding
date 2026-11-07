//! Parse and validate the persistent MQTT and camera configuration.

use crate::{store, sys::*};
use serde::{Deserialize, Serialize};

#[derive(Clone, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Config {
    #[serde(rename = "broker_host")]
    pub host: String,
    #[serde(rename = "broker_user")]
    pub user: String,
    #[serde(rename = "broker_password")]
    pub pass: String,
    pub camera_user: String,
    #[serde(rename = "camera_password")]
    pub camera_pass: String,
    #[serde(rename = "topic_prefix")]
    pub prefix: String,
    #[serde(rename = "client_id")]
    pub client: String,
    #[serde(rename = "broker_port")]
    pub port: u16,
    pub camera_port: u16,
    #[serde(rename = "poll_ms")]
    pub interval: u32,
}

impl Default for Config {
    fn default() -> Self {
        let mut config = Self {
            host: String::new(),
            user: String::new(),
            pass: String::new(),
            camera_user: String::new(),
            camera_pass: String::new(),
            prefix: String::new(),
            client: String::new(),
            port: 1883,
            camera_port: 80,
            interval: 500,
        };

        config.prefix = "local-ding/frontdoor".to_owned();
        config.client = "local-ding-frontdoor".to_owned();

        config
    }
}

impl Config {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn load(path: &[u8]) -> Result<(Self, libc::stat)> {
        let (config, metadata): (Self, _) = store::load(path)?;
        config.validate()?;
        Ok((config, metadata))
    }

    pub fn validate(&self) -> Result<()> {
        let config = self;
        if config.port == 0 || config.camera_port == 0 || !(250..=10000).contains(&config.interval)
        {
            return Err(());
        }
        for value in [
            &config.host,
            &config.user,
            &config.pass,
            &config.camera_user,
            &config.camera_pass,
            &config.prefix,
            &config.client,
        ] {
            if value.bytes().any(|byte| byte < 32 || byte == 127) {
                return Err(());
            }
        }
        let ip = ipv4(config.host.as_bytes())?;

        if !(ip[0] == 10
            || ip[0] == 127
            || (ip[0] == 172 && (16..=31).contains(&ip[1]))
            || (ip[0] == 192 && ip[1] == 168))
        {
            return Err(());
        }

        if config.camera_user.is_empty()
            || config.prefix.is_empty()
            || config
                .prefix
                .as_bytes()
                .iter()
                .any(|b| b"+# \t".contains(b))
            || config.prefix.as_bytes().ends_with(b"/")
            || config.client.is_empty()
            || config.client.len() > 118
            || (!config.pass.is_empty() && config.user.is_empty())
        {
            return Err(());
        }

        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn typed_configuration_round_trip_and_validation() {
        let config = Config {
            host: "192.168.1.2".into(),
            camera_user: "admin".into(),
            camera_pass: "quote\"\\secret".into(),
            ..Config::default()
        };
        config.validate().unwrap();
        let bytes = serde_json::to_vec(&config).unwrap();
        let restored: Config = serde_json::from_slice(&bytes).unwrap();
        assert_eq!(restored.camera_pass, config.camera_pass);
        restored.validate().unwrap();
        assert!(serde_json::from_str::<Config>(r#"{"broker_port":"1883"}"#).is_err());
        assert!(serde_json::from_str::<Config>(r#"{"unknown":true}"#).is_err());
        let invalid = Config {
            port: 0,
            ..restored
        };
        assert!(invalid.validate().is_err());
    }
}
