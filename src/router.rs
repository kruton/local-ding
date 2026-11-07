//! Register a diagnostic ROUT client and relay native doorbell notifications.

type Relay = Option<std::rc::Rc<dyn Fn(&[u8; 32])>>;
use crate::sys::*;
use core::mem;
use libc::*;

const MAGIC: u32 = 0xa5f32c6d;
const MODULE: u32 = 144;
const MESSAGE: u32 = 14016;

pub fn get32(b: &[u8]) -> u32 {
    u32::from_le_bytes([b[0], b[1], b[2], b[3]])
}

fn put(b: &mut [u8], v: u32) {
    b[..4].copy_from_slice(&v.to_le_bytes());
}

pub fn event_dir() -> Vec<u8> {
    env(
        b"LOCAL_DING_NATIVE_EVENT_DIR\0",
        "/mnt/tmp/local-ding-events",
    )
}

pub fn ready() -> bool {
    let Ok(p) = path(event_dir().as_slice(), b"subscriber.ready") else {
        return false;
    };
    let mut data = Vec::new();
    if read_private(&p, &mut data).is_err() {
        return false;
    }
    let s = data.strip_suffix(b"\n").unwrap_or(data.as_slice());
    let Ok(pid) = decimal(s) else { return false };
    pid > 1 && unsafe { kill(pid as i32, 0) } == 0
}

struct Frame {
    header: [u32; 9],
    message: u32,
    mask: u64,
    len: usize,
    body: Vec<u8>,
}

impl Frame {
    fn new() -> Self {
        Self {
            header: [0; 9],
            message: 0,
            mask: 0,
            len: 0,
            body: Vec::new(),
        }
    }

    async fn receive(&mut self, f: i32, end: i64) -> Result<()> {
        let mut h = [0; 36];

        recv_all(f, &mut h, end).await?;

        for (i, w) in self.header.iter_mut().enumerate() {
            *w = get32(&h[i * 4..]);
        }

        let n = self.header[8] as usize;
        if self.header[0] != MAGIC || !(24..=32768).contains(&n) {
            return Err(());
        }

        self.body.resize(n, 0);
        recv_all(f, &mut self.body, end).await?;

        self.message = get32(&self.body);
        self.len = get32(&self.body[16..]) as usize;
        if self.len != n - 24 {
            return Err(());
        }

        self.mask = u64::from_le_bytes(self.body[8..16].try_into().map_err(|_| ())?);
        Ok(())
    }
}

async fn command(f: i32, seq: u32, msg: u32, data: &[u8]) -> Result<()> {
    if data.len() > 2616 {
        return Err(());
    }

    let mut b = [0; 2676];

    for (i, v) in [MAGIC, seq, 0, 2, MODULE, 51, 0, 0, 24 + data.len() as u32]
        .iter()
        .enumerate()
    {
        put(&mut b[i * 4..], *v);
    }

    put(&mut b[36..], msg);
    b[44..52].fill(255);
    put(&mut b[52..], data.len() as u32);
    b[56..56 + data.len()].copy_from_slice(data);

    send_all(f, &b[..60 + data.len()], now() + 3000).await
}

async fn dispatch(
    f: i32,
    frame: &Frame,
    sink: i32,
    address: &sockaddr_un,
    seq: &mut u32,
    relay: &Relay,
) -> Result<()> {
    if frame.header[6] != 0 {
        return Ok(());
    }

    let valid = frame.message == MESSAGE
        && frame.len == 8
        && get32(&frame.body[20..]) == 0
        && get32(&frame.body[24..]) <= 2;

    if valid {
        let mut e = [0; 32];
        put(&mut e, 0x544e5645);
        put(&mut e[4..], MESSAGE);
        put(&mut e[12..], get32(&frame.body[24..]));

        let mut t: timespec = unsafe { mem::zeroed() };

        unsafe {
            clock_gettime(CLOCK_MONOTONIC, &mut t);
        }

        let ns = t.tv_sec as u64 * 1_000_000_000 + t.tv_nsec as u64;

        e[16..24].copy_from_slice(&ns.to_le_bytes());
        put(&mut e[24..], unsafe { getpid() } as u32);
        *seq = seq.wrapping_add(1);
        put(&mut e[28..], *seq);

        if let Some(deliver) = relay {
            deliver(&e);
        } else {
            unsafe {
                sendto(
                    sink,
                    e.as_ptr().cast(),
                    e.len(),
                    MSG_DONTWAIT | MSG_NOSIGNAL,
                    (address as *const sockaddr_un).cast(),
                    mem::size_of_val(address) as u32,
                );
            }
        }
    }

    if frame.header[1] != u32::MAX && frame.header[3] == 2 {
        let mut b = [0; 60];

        for (i, v) in [
            MAGIC,
            frame.header[1],
            0,
            0,
            0,
            0,
            1,
            if valid { 0 } else { u32::MAX },
            24,
        ]
        .iter()
        .enumerate()
        {
            put(&mut b[i * 4..], *v);
        }

        put(&mut b[36..], frame.message);
        b[44..52].copy_from_slice(&frame.mask.to_le_bytes());

        send_all(f, &b, now() + 1000).await?;
    }
    Ok(())
}

struct ExpectedAck {
    sequence: u32,
    message: u32,
}

async fn ack(
    f: i32,
    frame: &mut Frame,
    expected: ExpectedAck,
    sink: i32,
    address: &sockaddr_un,
    sequence: &mut u32,
    relay: &Relay,
) -> Result<i32> {
    let end = now() + 3000;

    loop {
        frame.receive(f, end).await?;

        if frame.header[6] == 1
            && frame.header[1] == expected.sequence
            && frame.message == expected.message
        {
            return Ok(frame.header[7] as i32);
        }

        dispatch(f, frame, sink, address, sequence, relay).await?;
    }
}

async fn connect_router() -> Result<Fd> {
    let p = env(b"LOCAL_DING_ROUTER_SOCKET\0", "/dev/ROUT");
    let address = unix(p.as_slice())?;

    unsafe {
        let f = fd(socket(
            AF_UNIX,
            SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC,
            0,
        ))?;

        if connect(
            f.0,
            (&address as *const sockaddr_un).cast(),
            mem::size_of_val(&address) as u32,
        ) != 0
            && errno() != EINPROGRESS
        {
            return Err(());
        }

        let f = connected(f, now() + 2000).await?;
        let mut cred: ucred = mem::zeroed();
        let mut n = mem::size_of_val(&cred) as u32;

        if getsockopt(
            f.0,
            SOL_SOCKET,
            SO_PEERCRED,
            (&mut cred as *mut ucred).cast(),
            &mut n,
        ) != 0
            || cred.uid != geteuid()
        {
            return Err(());
        }

        Ok(f)
    }
}

struct ReadyGuard(Vec<u8>);

impl Drop for ReadyGuard {
    fn drop(&mut self) {
        unlink_path(&self.0);
    }
}

pub async fn run() -> Result<()> {
    run_with(None).await
}

pub async fn run_with(relay: Relay) -> Result<()> {
    let dir = event_dir();

    directory(&dir)?;

    let marker = path(dir.as_slice(), b"subscriber.ready")?;
    let _ready_guard = ReadyGuard(marker.clone());
    let lock = path(dir.as_slice(), b"subscriber.lock")?;

    let _lock = unsafe {
        let f = fd(open(
            c_path(&lock)?.as_ptr(),
            O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC,
            0o600,
        ))?;

        let mut stat = mem::zeroed();

        if fstat(f.0, &mut stat) != 0 || !regular(&stat) {
            return Err(());
        }

        let mut l: flock = mem::zeroed();
        l.l_type = F_WRLCK as i16;
        l.l_whence = SEEK_SET as i16;

        if fcntl(f.0, F_SETLK, &l) != 0 {
            return Err(());
        }

        f
    };

    let sink = unsafe {
        fd(socket(
            AF_UNIX,
            SOCK_DGRAM | SOCK_NONBLOCK | SOCK_CLOEXEC,
            0,
        ))?
    };

    let address = unix(path(dir.as_slice(), b"doorbell.sock")?.as_slice())?;

    unlink_path(&marker);

    let mut frame = Frame::new();
    let mut sequence = 0;
    let mut backoff = 1;

    while !stopped() {
        let Ok(f) = connect_router().await else {
            delay(backoff * 1000).await;
            backoff = (backoff * 2).min(30);
            continue;
        };

        let result = (async {
            command(f.0, 1, 5, &MODULE.to_le_bytes()).await?;
            if ack(
                f.0,
                &mut frame,
                ExpectedAck {
                    sequence: 1,
                    message: 5,
                },
                sink.0,
                &address,
                &mut sequence,
                &relay,
            )
            .await?
                != -1
            {
                return Err(());
            }

            let mut reg = [0; 2616];

            put(&mut reg, u32::MAX);
            put(&mut reg[4..], MODULE);
            put(&mut reg[16..], MODULE);
            put(&mut reg[20..], 1);
            put(&mut reg[24..], MESSAGE);
            reg[32..40].fill(255);

            command(f.0, 2, 2, &reg).await?;

            if ack(
                f.0,
                &mut frame,
                ExpectedAck {
                    sequence: 2,
                    message: 2,
                },
                sink.0,
                &address,
                &mut sequence,
                &relay,
            )
            .await?
                != 0
            {
                return Err(());
            }

            let mut pid = Vec::new();
            pid.extend_from_slice((unsafe { getpid() } as u64).to_string().as_bytes());
            pid.extend_from_slice(b"\n");
            atomic(&marker, pid.as_slice())?;

            log("Router subscriber registered in unused diagnostic slot 144\n");

            backoff = 1;

            let mut heartbeat = now();
            let mut seq = 3;

            while !stopped() {
                let ret = match tokio::time::timeout(
                    std::time::Duration::from_millis(250),
                    crate::sys::wait(f.0, false, now() + 1000),
                )
                .await
                {
                    Ok(Ok(_)) => 1,
                    Ok(Err(_)) => -1,
                    Err(_) => 0,
                };
                if ret < 0 {
                    if errno() == EINTR {
                        continue;
                    }
                    return Err(());
                }
                if ret > 0 {
                    frame.receive(f.0, now() + 3000).await?;
                    dispatch(f.0, &frame, sink.0, &address, &mut sequence, &relay).await?;
                }
                if now() - heartbeat >= 10000 {
                    command(f.0, seq, 5, &MODULE.to_le_bytes()).await?;
                    if ack(
                        f.0,
                        &mut frame,
                        ExpectedAck {
                            sequence: seq,
                            message: 5,
                        },
                        sink.0,
                        &address,
                        &mut sequence,
                        &relay,
                    )
                    .await?
                        != 0
                    {
                        return Err(());
                    }
                    seq = seq.wrapping_add(1);
                    heartbeat = now();
                }
            }
            Ok(())
        })
        .await;

        let _ = result;

        unlink_path(&marker);
        drop(f);
        delay(1000).await;
    }

    // Never unregister by module ID: that might remove a replacement diagnostic.
    unlink_path(&marker);

    Ok(())
}
