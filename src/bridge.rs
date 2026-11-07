//! Publish camera detection states and native doorbell events over MQTT.

use crate::{camera::Camera, config::Config, mqtt::Mqtt, router, sys::*};
use libc::*;

const NAMES: [&[u8]; 5] = [
    b"motion/state",
    b"person/state",
    b"vehicle/state",
    b"animal/state",
    b"package/state",
];

fn state(s: i8) -> &'static [u8] {
    if s < 0 {
        b"unavailable"
    } else if s == 0 {
        b"OFF"
    } else {
        b"ON"
    }
}

struct Child(tokio::task::JoinHandle<Result<()>>);
impl Child {
    fn running(&mut self) -> bool {
        let running = !self.0.is_finished();
        if !running {
            STOP.store(true, core::sync::atomic::Ordering::Relaxed);
        }
        running
    }
}

impl Drop for Child {
    fn drop(&mut self) {
        self.0.abort();
    }
}

pub async fn run(path: &[u8]) -> Result<()> {
    let cp = path.to_vec();
    let (c, initial) = storage(move || Config::load(&cp)).await?;
    let mut boot = [0; 8];
    unsafe {
        let f = fd(open(c"/dev/urandom".as_ptr(), O_RDONLY | O_CLOEXEC))?;
        if read(f.0, boot.as_mut_ptr().cast(), boot.len()) != boot.len() as isize {
            return Err(());
        }
    }
    let cp = path.to_vec();
    let cc = c.clone();
    let ci = initial;
    let mut worker = Child(tokio::task::spawn_local(async move {
        doorbell(&cp, &cc, &ci, &boot).await
    }));

    let mut camera = Camera::new();
    let mut backoff = 1;
    while !stopped() && !changed(path, &initial).await {
        if !worker.running() {
            return Err(());
        }
        let Ok(mut m) = Mqtt::connect(&c, false).await else {
            let end = now() + backoff * 1000;
            while !stopped() && !changed(path, &initial).await && now() < end {
                delay(250).await;
            }
            backoff = (backoff * 2).min(30);
            continue;
        };
        backoff = 1;
        let mut previous = [-2; 6];
        let mut online = false;
        let mut ping = now();
        let mut api_delay = 0;
        let _ = (async {
            m.publish(&c, b"availability", b"offline", true).await?;
            log("MQTT connected\n");
            while !stopped() && !changed(path, &initial).await {
                let begin = now();
                let result = (async {
                    if camera.token.is_empty() {
                        camera.login(&c).await?;
                    }
                    camera.states(&c).await
                })
                .await;
                match result {
                    Ok(current) => {
                        for i in 0..5 {
                            if previous[i] != current[i] {
                                m.publish(&c, NAMES[i], state(current[i]), true).await?;
                            }
                        }
                        if !online {
                            m.publish(&c, b"availability", b"online", true).await?;
                            online = true;
                            log("Local event source ready\n");
                        }
                        previous = current;
                        api_delay = 0;
                    }
                    Err(_) => {
                        camera.token.clear();
                        if online {
                            m.publish(&c, b"availability", b"offline", true).await?;
                            online = false;
                        }
                        previous = [-2; 6];
                        api_delay = if api_delay == 0 {
                            1000
                        } else {
                            (api_delay * 2).min(10000)
                        };
                    }
                }
                if now() - ping >= 10000 {
                    m.check_connection()?;
                    ping = now();
                }
                let interval = if api_delay > 0 {
                    api_delay
                } else {
                    c.interval as i64
                };
                let remaining = interval - (now() - begin);
                if remaining > 0 {
                    delay(remaining as i32).await;
                }
                if !worker.running() {
                    return Err(());
                }
            }
            Ok(())
        })
        .await;
        drop(m);
        if !stopped() && !changed(path, &initial).await {
            delay(1000).await;
        }
    }
    Ok(())
}

#[derive(Clone, Copy)]
struct Event {
    state: u32,
    ns: u64,
    pid: u32,
    sequence: u32,
}

async fn doorbell(path: &[u8], c: &Config, initial: &stat, boot: &[u8; 8]) -> Result<()> {
    let dir = router::event_dir();
    directory(&dir)?;

    let socket_path = crate::sys::path(dir.as_slice(), b"doorbell.sock")?;
    let (send, mut receive) = tokio::sync::mpsc::channel(64);
    let slots = std::sync::Arc::new(tokio::sync::Semaphore::new(64));
    let latest = std::rc::Rc::new(std::cell::Cell::new(-1i8));
    let observed = latest.clone();
    let active = std::cell::Cell::new(false);

    let relay = std::rc::Rc::new(move |b: &[u8; 32]| {
        let e = Event {
            state: router::get32(&b[12..]),
            ns: u64::from_le_bytes(b[16..24].try_into().unwrap()),
            pid: router::get32(&b[24..]),
            sequence: router::get32(&b[28..]),
        };

        observed.set(e.state as i8);

        if e.state > 0 && !active.get() {
            match slots.clone().try_acquire_owned() {
                Ok(permit) => {
                    let _ = send.try_send((e, permit));
                }
                Err(_) => log("Doorbell MQTT queue full; ring dropped\n"),
            }
        }

        active.set(e.state > 0);
    });

    let mut ingress = Child(tokio::task::spawn_local(router::run_with(Some(relay))));
    let mut pending = None;
    let mut current = -1;
    let mut dirty = true;
    let mut available = None;
    let mut m: Option<Mqtt> = None;
    let mut retry = 0;
    let mut ping = 0;
    let mut backoff = 1;
    let mut camera = Camera::new();

    if camera.login(c).await.is_ok() {
        if let Ok(states) = camera.states(c).await {
            current = states[5];
        }
        camera.logout(c.camera_port).await;
    }

    while !stopped() && !changed(path, initial).await {
        if !ingress.running() {
            return Err(());
        }

        delay(25).await;

        let observed = latest.get();

        if observed >= 0 && current != observed {
            current = observed;
            dirty = true;
        }

        if m.is_none() && now() >= retry {
            match Mqtt::connect(c, true).await {
                Ok(client) => {
                    m = Some(client);
                    dirty = true;
                    available = None;
                    ping = now();
                    backoff = 1;
                }
                Err(_) => {
                    retry = now() + backoff * 1000;
                    backoff = (backoff * 2).min(30);
                }
            }
        }

        let Some(client) = m.as_mut() else { continue };

        let result: Result<()> = (async {
            if available.is_none() {
                client
                    .publish(c, b"doorbell/source", b"router", true)
                    .await?;
            }

            let ready = router::ready();

            if available != Some(ready) {
                client
                    .publish(
                        c,
                        b"doorbell/availability",
                        if ready { b"online" } else { b"offline" },
                        true,
                    )
                    .await?;
                available = Some(ready);
            }

            if dirty {
                client
                    .publish(c, b"visitor/state", state(current), true)
                    .await?;
                dirty = false;
            }

            loop {
                if pending.is_none() {
                    pending = receive.try_recv().ok();
                }

                let Some((e, _)) = pending.as_ref() else {
                    break;
                };

                let e = *e;
                let timestamp = unsafe { time(core::ptr::null_mut()) } as i64
                    - (now() - e.ns as i64 / 1_000_000) / 1000;
                let boot_id: String = boot.iter().map(|byte| format!("{byte:02x}")).collect();
                let payload = crate::json::encode(&serde_json::json!({
                    "event": "doorbell",
                    "source": "router",
                    "timestamp": timestamp,
                    "boot_id": boot_id,
                    "source_pid": e.pid,
                    "sequence": e.sequence,
                    "native_monotonic_ms": e.ns / 1_000_000,
                }))?;

                client
                    .publish(c, b"doorbell/ring", payload.as_slice(), false)
                    .await?;

                pending = None;
            }

            if now() - ping >= 10000 {
                client.check_connection()?;
                ping = now();
            }

            Ok(())
        })
        .await;

        if result.is_err() {
            m = None;
            retry = now() + 1000;
        }
    }
    unlink_path(&socket_path);
    Ok(())
}
