//! Bounded requests to the camera’s loopback HTTP API.

use crate::{
    config::Config,
    json::{self, text},
    sys::*,
};

pub struct Camera {
    reply: Vec<u8>,
    request: Vec<u8>,
    pub token: String,
}

impl Camera {
    pub fn new() -> Self {
        Self {
            reply: Vec::new(),
            request: Vec::new(),
            token: String::new(),
        }
    }

    pub async fn http(
        &mut self,
        port: u16,
        query: &[u8],
        payload: &[u8],
    ) -> Result<serde_json::Value> {
        let connection = connect_tcp(b"127.0.0.1", port).await?;

        self.request.clear();
        self.request.extend_from_slice(b"POST /cgi-bin/api.cgi?");
        self.request.extend_from_slice(query);
        self.request.extend_from_slice(b" HTTP/1.0\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: ");
        self.request
            .extend_from_slice((payload.len() as u64).to_string().as_bytes());
        self.request.extend_from_slice(b"\r\n\r\n");
        self.request.extend_from_slice(payload);
        send_all(connection.0, self.request.as_slice(), now() + 3000).await?;
        self.reply.clear();

        let deadline = now() + 3000;

        loop {
            wait(connection.0, false, deadline).await?;
            let mut chunk = [0; 2048];
            let received = unsafe {
                libc::recv(
                    connection.0,
                    chunk.as_mut_ptr().cast(),
                    chunk.len(),
                    libc::MSG_DONTWAIT,
                )
            };

            if received < 0 && (errno() == libc::EINTR || errno() == libc::EAGAIN) {
                continue;
            }

            if received < 0 {
                return Err(());
            }

            if received == 0 {
                break;
            }

            if self.reply.len() + received as usize > 65536 {
                return Err(());
            }
            self.reply.extend_from_slice(&chunk[..received as usize]);
        }

        let raw = self.reply.as_slice();
        if !raw.starts_with(b"HTTP/1.0 200 ") && !raw.starts_with(b"HTTP/1.1 200 ") {
            return Err(());
        }

        let body_start = raw.windows(4).position(|w| w == b"\r\n\r\n").ok_or(())? + 4;
        json::parse(&self.reply.as_slice()[body_start..])
    }

    pub async fn login(&mut self, config: &Config) -> Result<()> {
        let request = json::encode(&serde_json::json!([{
            "cmd": "Login", "action": 0,
            "param": {"User": {
                "userName": text(config.camera_user.as_bytes())?,
                "password": text(config.camera_pass.as_bytes())?,
            }},
        }]))?;
        let response = self
            .http(config.camera_port, b"cmd=Login", &request)
            .await?;
        let value = json::camera_response(response)?;
        let token = json::field(&value["Token"], "name")?;

        if token.is_empty()
            || token
                .as_bytes()
                .iter()
                .any(|byte| !byte.is_ascii_alphanumeric() && *byte != b'-' && *byte != b'_')
        {
            return Err(());
        }

        self.token = token;

        Ok(())
    }

    pub fn query(&self, command: &[u8]) -> Result<Vec<u8>> {
        let mut request = Vec::new();

        request.extend_from_slice(b"cmd=");
        request.extend_from_slice(command);
        request.extend_from_slice(b"&token=");
        request.extend_from_slice(self.token.as_bytes());

        Ok(request)
    }

    pub async fn states(&mut self, config: &Config) -> Result<[i8; 6]> {
        let query = self.query(b"GetEvents")?;

        let response = self
            .http(
                config.camera_port,
                query.as_slice(),
                b"[{\"cmd\":\"GetEvents\",\"action\":0,\"param\":{\"channel\":0}}]",
            )
            .await?;

        let value = json::camera_response(response)?;
        let ai = &value["ai"];

        let mut result = [-1; 6];

        for (i, key) in [
            b"md".as_slice(),
            b"people",
            b"vehicle",
            b"dog_cat",
            b"package",
            b"visitor",
        ]
        .iter()
        .enumerate()
        {
            let event = &(if i == 0 || i == 5 { &value } else { ai })[text(key)?];
            let support = event["support"].as_u64().ok_or(())?;
            if support == 0 {
                continue;
            }
            let state = event["alarm_state"].as_u64().ok_or(())?;
            if support != 1 || !(0..=1).contains(&state) {
                return Err(());
            }
            result[i] = state as i8;
        }

        Ok(result)
    }

    pub async fn logout(&mut self, port: u16) {
        if !self.token.is_empty() {
            if let Ok(query) = self.query(b"Logout") {
                let _ = self
                    .http(
                        port,
                        query.as_slice(),
                        b"[{\"cmd\":\"Logout\",\"action\":0,\"param\":{}}]",
                    )
                    .await;
            }

            self.token.clear();
        }
    }
}
