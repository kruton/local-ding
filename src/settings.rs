//! Serve authenticated local settings and persist configuration securely.

use crate::sys::wait;
use crate::{
    camera::Camera,
    config::Config,
    json::{self, text},
    router, store,
    sys::*,
    time_config::{Mode, TimeConfig},
};
#[cfg(not(feature = "settings-test"))]
use core::mem;
use libc::*;
use serde_json::{json, Value};

#[cfg(not(feature = "settings-test"))]
const DATA: &str = "/mnt/para";
#[cfg(feature = "settings-test")]
const DATA: &str = "/tmp/local-ding-settings-test";
#[cfg(not(feature = "settings-test"))]
const PORT: u16 = 9588;
#[cfg(feature = "settings-test")]
const PORT: u16 = 19588;
#[cfg(not(feature = "settings-test"))]
const CAMERA_PORT: u16 = 80;
#[cfg(feature = "settings-test")]
const CAMERA_PORT: u16 = 18080;

fn config_path() -> Result<Vec<u8>> {
    path(DATA.as_bytes(), b"mqtt/events.json")
}

fn keys_path() -> Result<Vec<u8>> {
    path(DATA.as_bytes(), b"dropbear/authorized_keys")
}

async fn authorize(user: &[u8], pass: &[u8]) -> Result<()> {
    let mut cfg = Config::new();
    cfg.camera_port = CAMERA_PORT;
    cfg.camera_user = text(user)?.to_owned();
    cfg.camera_pass = text(pass)?.to_owned();

    let mut camera = Camera::new();
    camera.login(&cfg).await?;

    let query = camera.query(b"GetUser")?;
    let result = (async {
        let j = camera
            .http(
                CAMERA_PORT,
                query.as_slice(),
                b"[{\"cmd\":\"GetUser\",\"action\":0,\"param\":{}}]",
            )
            .await?;

        let value = json::camera_response(j)?;
        let users = value["User"].as_array().ok_or(())?;
        for user_entry in users {
            if user_entry["userName"].as_str() == Some(text(user)?)
                && user_entry["level"].as_str() == Some("admin")
            {
                return Ok(());
            }
        }
        Err(())
    })
    .await;

    camera.logout(CAMERA_PORT).await;
    cfg.camera_pass.clear();
    result
}

fn time_status() -> Result<Value> {
    let mut runtime = Vec::new();
    let mut p = Vec::new();
    p.extend_from_slice(b"/mnt/tmp/local-ding-time/status.json");
    if read_private(&p, &mut runtime).is_err() {
        runtime = (b"{\"mode\":\"auto\",\"servers\":\"\",\"dhcp_servers\":\"\",\"running\":false}")
            .to_vec();
    }

    let j = json::parse(runtime.as_slice())?;
    let configured: TimeConfig = store::load(&path(DATA.as_bytes(), b"local-ding/time.json")?)
        .map(|(config, _)| config)
        .or_else(|_| TimeConfig::read(std::ffi::OsStr::new("/etc/local-only/time.json")))
        .unwrap_or_default();
    configured.validate()?;
    let servers = if configured.mode == Mode::Manual {
        configured.servers.join(" ")
    } else {
        json::field(&j, "servers")?
    };

    let mut sync = Vec::new();
    p = (b"/mnt/tmp/local-ding-time/sync.json").to_vec();
    let last_sync = if read_private(&p, &mut sync).is_ok() {
        json::parse(sync.as_slice()).unwrap_or(Value::Null)
    } else {
        Value::Null
    };
    Ok(json!({
        "mode": configured.mode,
        "servers": servers,
        "effective_servers": j["servers"],
        "dhcp_servers": j["dhcp_servers"],
        "running": j["running"],
        "last_sync": last_sync,
    }))
}

fn save_time(request: &Value) -> Result<()> {
    #[derive(serde::Deserialize)]
    struct TimeRequest {
        mode: Mode,
        servers: String,
    }
    let request: TimeRequest = serde_json::from_value(request.clone()).map_err(|_| ())?;
    let config = TimeConfig {
        mode: request.mode,
        servers: request
            .servers
            .split_whitespace()
            .map(str::to_owned)
            .collect(),
    };
    config.validate()?;
    directory(&path(DATA.as_bytes(), b"local-ding")?)?;
    store::save(&path(DATA.as_bytes(), b"local-ding/time.json")?, &config)
}

fn status() -> Result<Vec<u8>> {
    let saved = Config::load(&config_path()?).ok();
    let defaults = Config::new();
    let config = saved.as_ref().map_or(&defaults, |saved| &saved.0);
    let mut keys = Vec::new();
    if read_private(&keys_path()?, &mut keys).is_err() && errno() != ENOENT {
        return Err(());
    }
    let authorized_keys: Vec<&str> = text(keys.as_slice())?
        .lines()
        .filter(|line| !line.is_empty())
        .collect();
    json::encode(&json!({
        "configured": saved.is_some(),
        "broker_host": text(config.host.as_bytes())?,
        "broker_port": config.port,
        "broker_user": text(config.user.as_bytes())?,
        "broker_password_set": !config.pass.is_empty(),
        "topic_prefix": text(config.prefix.as_bytes())?,
        "client_id": text(config.client.as_bytes())?,
        "poll_ms": config.interval,
        "authorized_keys": authorized_keys,
        "doorbell_source": "router",
        "doorbell_subscriber_ready": router::ready(),
        "time": time_status()?,
    }))
}

fn save_mqtt(j: &Value, admin: &[u8], pass: &[u8]) -> Result<()> {
    let old = Config::load(&config_path()?).ok();
    let mut password = json::field(j, "broker_password")?;

    if password.is_empty() {
        if let Some((old, _)) = old.as_ref() {
            password = old.pass.clone();
        }
    }

    let config = Config {
        host: json::field(j, "broker_host")?,
        port: json::field(j, "broker_port")?.parse().map_err(|_| ())?,
        user: json::field(j, "broker_user")?,
        pass: password,
        prefix: json::field(j, "topic_prefix")?,
        client: json::field(j, "client_id")?,
        interval: json::field(j, "poll_ms")?.parse().map_err(|_| ())?,
        camera_user: text(admin)?.to_owned(),
        camera_pass: text(pass)?.to_owned(),
        ..Config::default()
    };
    config.validate()?;
    directory(&path(DATA.as_bytes(), b"mqtt")?)?;
    store::save(&config_path()?, &config)
}

fn add_key(j: &Value) -> Result<()> {
    let key = json::field(j, "public_key")?;
    let raw = key.as_bytes().strip_prefix(b"ssh-ed25519 ").ok_or(())?;
    let b64 = raw.split(|b| *b == b' ').next().ok_or(())?;

    if b64.len() != 68 {
        return Err(());
    }

    let alphabet = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut decoded = [0; 51];
    let (mut bits, mut count, mut used) = (0u32, 0, 0);

    for c in b64 {
        let n = alphabet.iter().position(|v| v == c).ok_or(())?;
        bits = bits.wrapping_shl(6) | n as u32;
        count += 6;
        if count >= 8 {
            count -= 8;
            decoded[used] = (bits >> count) as u8;
            used += 1;
        }
    }

    if &decoded[..19] != b"\0\0\0\x0bssh-ed25519\0\0\0\x20" {
        return Err(());
    }

    let mut line = Vec::new();
    line.extend_from_slice(b"ssh-ed25519 ");
    line.extend_from_slice(b64);
    directory(&path(DATA.as_bytes(), b"dropbear")?)?;

    let keyfile = keys_path()?;
    let mut keys = Vec::new();
    match read_private(&keyfile, &mut keys) {
        Ok(_) => {}
        Err(_) => {
            if errno() != ENOENT {
                return Err(());
            }
        }
    }

    if keys.split(|c| *c == b'\n').any(|s| {
        s == line.as_slice()
            || s.strip_prefix(line.as_slice())
                .is_some_and(|s| s.starts_with(b" "))
    }) {
        return Ok(());
    }

    if !keys.is_empty() && !keys.ends_with(b"\n") {
        keys.extend_from_slice(b"\n");
    }

    keys.extend_from_slice(line.as_slice());
    keys.extend_from_slice(b"\n");

    atomic(&keyfile, keys.as_slice())
}

fn header<'a>(headers: &'a [u8], name: &[u8]) -> Result<Option<&'a [u8]>> {
    let mut found = None;

    for line in headers.split(|b| *b == b'\n').skip(1) {
        let line = line.strip_suffix(b"\r").unwrap_or(line);
        if line.is_empty() {
            break;
        }

        let colon = line.iter().position(|b| *b == b':').ok_or(())?;
        if line[..colon].eq_ignore_ascii_case(name) {
            if found.is_some() {
                return Err(());
            }
            let mut value = &line[colon + 1..];
            while value.first() == Some(&b' ') {
                value = &value[1..];
            }
            found = Some(value);
        }
    }

    Ok(found)
}

async fn respond(f: i32, status: u16, body: &[u8]) {
    let header = format!("HTTP/1.1 {status} Local Settings\r\nContent-Type: application/json\r\nCache-Control: no-store\r\nX-Content-Type-Options: nosniff\r\nConnection: close\r\nContent-Length: {}\r\n\r\n", body.len());
    if send_all(f, header.as_bytes(), now() + 3000).await.is_ok() {
        let _ = send_all(f, body, now() + 3000).await;
    }
}

async fn error(f: i32, status: u16, text: &[u8]) {
    if let Ok(body) = json::encode(&json!({"error": String::from_utf8_lossy(text)})) {
        respond(f, status, &body).await;
    }
}

async fn serve(f: i32) -> Result<()> {
    let mut input = Vec::new();
    let mut head = 0;
    let mut length = 0;
    let end = now() + 4000;

    loop {
        wait(f, false, end).await?;
        let mut block = [0; 2048];
        let n = unsafe { recv(f, block.as_mut_ptr().cast(), block.len(), MSG_DONTWAIT) };

        if n < 0 && (errno() == EINTR || errno() == EAGAIN) {
            continue;
        }

        if n <= 0 {
            return Err(());
        }

        if input.len() + n as usize > 20000 {
            error(f, 413, b"Request too large").await;
            return Ok(());
        }
        input.extend_from_slice(&block[..n as usize]);

        if head == 0 {
            if let Some(split) = input.windows(4).position(|s| s == b"\r\n\r\n") {
                head = split + 4;
                let h = &input.as_slice()[..head];

                if header(h, b"Content-Type")? != Some(b"application/json")
                    || header(h, b"X-Local-Ding-Settings")? != Some(b"1")
                    || header(h, b"Transfer-Encoding")?.is_some()
                {
                    error(f, 400, b"Invalid request").await;
                    return Ok(());
                }

                length = decimal(header(h, b"Content-Length")?.ok_or(())?)? as usize;

                if !(2..=16000).contains(&length) {
                    error(f, 413, b"Request too large").await;
                    return Ok(());
                }

                if let Some(origin) = header(h, b"Origin")? {
                    let mut expected = Vec::new();
                    expected.extend_from_slice(b"https://");
                    expected.extend_from_slice(header(h, b"Host")?.ok_or(())?);
                    if expected.as_slice() != origin {
                        error(f, 403, b"Origin rejected").await;
                        return Ok(());
                    }
                }
            }
        }

        if head > 0 && input.len() >= head + length {
            break;
        }
    }

    if input.len() != head + length {
        error(f, 400, b"Invalid request").await;
        return Ok(());
    }

    let raw = input.as_slice();
    let first = raw.split(|b| *b == b'\r').next().ok_or(())?;
    let mut parts = first.split(|b| *b == b' ');

    if parts.next() != Some(b"POST") {
        error(f, 405, b"POST required").await;
        return Ok(());
    }

    let route = parts.next().ok_or(())?;

    let j = match json::parse(&raw[head..]) {
        Ok(j) => j,
        Err(_) => {
            error(f, 400, b"Invalid credentials format").await;
            return Ok(());
        }
    };

    let (admin, mut password) = match (
        json::field(&j, "admin_user"),
        json::field(&j, "admin_password"),
    ) {
        (Ok(a), Ok(p)) if j.is_object() => (a, p),
        _ => {
            error(f, 400, b"Invalid credentials format").await;
            return Ok(());
        }
    };

    if authorize(admin.as_bytes(), password.as_bytes())
        .await
        .is_err()
    {
        delay(1000).await;
        error(f, 403, b"Camera admin login required").await;
    } else {
        match route {
            b"/status" => match storage(status).await {
                Ok(body) => respond(f, 200, &body).await,
                Err(_) => error(f, 500, b"Cannot read settings").await,
            },
            b"/time" => {
                let request = j;

                if storage(move || save_time(&request)).await.is_ok() {
                    respond(f, 200, b"{\"ok\":true,\"message\":\"Time settings saved\"}").await;
                } else {
                    error(f, 400, b"Invalid time settings").await;
                }
            }
            b"/mqtt" => {
                let request = j;
                let a = admin.clone();
                let p = password.clone();

                if storage(move || save_mqtt(&request, a.as_bytes(), p.as_bytes()))
                    .await
                    .is_ok()
                {
                    respond(
                        f,
                        200,
                        b"{\"ok\":true,\"message\":\"MQTT settings saved; client will restart\"}",
                    )
                    .await;
                } else {
                    error(f, 400, b"Invalid MQTT settings or storage unavailable").await;
                }
            }

            b"/key" => {
                let request = j;

                if storage(move || add_key(&request)).await.is_ok() {
                    respond(
                        f,
                        200,
                        b"{\"ok\":true,\"message\":\"SSH key added; existing keys preserved\"}",
                    )
                    .await;
                } else {
                    error(f, 400, b"Valid Ed25519 public key required").await;
                }
            }
            _ => error(f, 404, b"Unknown action").await,
        }
    }

    password.clear();
    input.clear();

    Ok(())
}

pub async fn run() -> Result<()> {
    let mut actual = Vec::new();
    actual.extend_from_slice(DATA.as_bytes());

    #[cfg(not(feature = "settings-test"))]
    unsafe {
        let mut fs: statfs = mem::zeroed();
        if statfs(c_path(&actual)?.as_ptr(), &mut fs) != 0 || fs.f_type != 0x24051905 {
            log("Persistent UBIFS storage required\n");
            return Err(());
        }
    }

    #[cfg(feature = "settings-test")]
    directory(&actual)?;
    let listener = tokio::net::TcpListener::bind((std::net::Ipv4Addr::LOCALHOST, PORT))
        .await
        .map_err(|_| ())?;
    let permits = std::sync::Arc::new(tokio::sync::Semaphore::new(4));

    while !stopped() {
        let Ok(Ok((socket, _))) =
            tokio::time::timeout(std::time::Duration::from_millis(250), listener.accept()).await
        else {
            continue;
        };

        let Ok(permit) = permits.clone().try_acquire_owned() else {
            continue;
        };

        tokio::task::spawn_local(async move {
            use std::os::fd::IntoRawFd;
            let f = Fd(socket.into_std().unwrap().into_raw_fd());
            let _permit = permit;
            if serve(f.0).await.is_err() {
                error(f.0, 400, b"Invalid request").await;
            }
        });
    }

    Ok(())
}
