//! Bounded network I/O and owner-only filesystem operations.
use core::{
    mem,
    sync::atomic::{AtomicBool, Ordering},
};
use libc::*;

pub type Result<T> = core::result::Result<T, ()>;

pub static STOP: AtomicBool = AtomicBool::new(false);

pub fn stopped() -> bool {
    STOP.load(Ordering::Relaxed)
}

pub fn signals() {
    for kind in [
        tokio::signal::unix::SignalKind::terminate(),
        tokio::signal::unix::SignalKind::interrupt(),
    ] {
        let mut signal = tokio::signal::unix::signal(kind).expect("signal registration");

        tokio::task::spawn_local(async move {
            signal.recv().await;
            STOP.store(true, Ordering::Relaxed);
        });
    }
}

pub fn log(s: &str) {
    static WRITTEN: core::sync::atomic::AtomicUsize = core::sync::atomic::AtomicUsize::new(0);

    if WRITTEN.fetch_add(s.len(), Ordering::Relaxed) > 16384 {
        return;
    }

    unsafe {
        write(2, s.as_ptr().cast(), s.len());
    }
}

pub fn errno() -> c_int {
    unsafe { *__errno_location() }
}

pub fn now() -> i64 {
    unsafe {
        let mut t = mem::zeroed();
        clock_gettime(CLOCK_MONOTONIC, &mut t);
        t.tv_sec as i64 * 1000 + t.tv_nsec as i64 / 1_000_000
    }
}

pub async fn delay(ms: i32) {
    let end = now() + ms as i64;

    while !stopped() && now() < end {
        tokio::time::sleep(std::time::Duration::from_millis(
            (end - now()).clamp(1, 50) as u64
        ))
        .await;
    }
}

pub fn env(name: &'static [u8], default: &str) -> Vec<u8> {
    use std::os::unix::ffi::OsStringExt;
    let name = std::str::from_utf8(name.strip_suffix(&[0]).unwrap_or(name))
        .expect("environment variable name");
    std::env::var_os(name).map_or_else(|| default.as_bytes().to_vec(), |value| value.into_vec())
}

pub fn c_path(path: &[u8]) -> Result<std::ffi::CString> {
    std::ffi::CString::new(path).map_err(|_| ())
}

pub fn path(dir: &[u8], leaf: &[u8]) -> Result<Vec<u8>> {
    let mut b = Vec::new();
    b.extend_from_slice(dir);
    b.extend_from_slice(b"/");
    b.extend_from_slice(leaf);
    Ok(b)
}

pub struct Fd(pub c_int);

impl Drop for Fd {
    fn drop(&mut self) {
        unsafe {
            close(self.0);
        }
    }
}

pub fn fd(n: c_int) -> Result<Fd> {
    if n < 0 {
        Err(())
    } else {
        Ok(Fd(n))
    }
}

pub async fn wait(fd: c_int, writing: bool, end: i64) -> Result<bool> {
    let interest = if writing {
        tokio::io::Interest::WRITABLE
    } else {
        tokio::io::Interest::READABLE
    };

    use std::os::fd::FromRawFd;

    let duplicate = unsafe { fcntl(fd, F_DUPFD_CLOEXEC, 0) };

    if duplicate < 0 {
        return Err(());
    }

    let owned = unsafe { std::os::fd::OwnedFd::from_raw_fd(duplicate) };

    // The duplicate remains owned and nonblocking throughout registration.
    let io = unsafe { tokio::io::unix::AsyncFd::register_with_interest(owned, interest) }
        .map_err(|_| ())?;

    let remaining = end - now();

    if remaining <= 0 || stopped() {
        return Err(());
    }

    let _ready = tokio::time::timeout(
        std::time::Duration::from_millis(remaining as u64),
        io.ready(interest),
    )
    .await
    .map_err(|_| ())?
    .map_err(|_| ())?;

    Ok(true)
}

pub async fn send_all(fd: c_int, mut data: &[u8], end: i64) -> Result<()> {
    while !data.is_empty() {
        wait(fd, true, end).await?;
        let n = unsafe {
            send(
                fd,
                data.as_ptr().cast(),
                data.len(),
                MSG_NOSIGNAL | MSG_DONTWAIT,
            )
        };

        if n < 0 && (errno() == EINTR || errno() == EAGAIN) {
            continue;
        }

        if n <= 0 {
            return Err(());
        }

        data = &data[n as usize..];
    }
    Ok(())
}

pub async fn recv_all(fd: c_int, mut data: &mut [u8], end: i64) -> Result<()> {
    while !data.is_empty() {
        wait(fd, false, end).await?;

        let n = unsafe { recv(fd, data.as_mut_ptr().cast(), data.len(), MSG_DONTWAIT) };
        if n < 0 && (errno() == EINTR || errno() == EAGAIN) {
            continue;
        }

        if n <= 0 {
            return Err(());
        }

        data = &mut data[n as usize..];
    }
    Ok(())
}

pub async fn connect_tcp(host: &[u8], port: u16) -> Result<Fd> {
    let ip = ipv4(host)?;

    unsafe {
        let f = fd(socket(
            AF_INET,
            SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC,
            0,
        ))?;

        let mut a: sockaddr_in = mem::zeroed();
        a.sin_family = AF_INET as u16;
        a.sin_port = port.to_be();
        a.sin_addr.s_addr = u32::from_ne_bytes(ip);

        if connect(
            f.0,
            (&a as *const sockaddr_in).cast(),
            mem::size_of_val(&a) as u32,
        ) < 0
            && errno() != EINPROGRESS
        {
            return Err(());
        }

        connected(f, now() + 2000).await
    }
}

pub async fn connected(f: Fd, end: i64) -> Result<Fd> {
    wait(f.0, true, end).await?;

    unsafe {
        let mut error: c_int = 0;
        let mut len = mem::size_of_val(&error) as u32;

        if getsockopt(
            f.0,
            SOL_SOCKET,
            SO_ERROR,
            (&mut error as *mut c_int).cast(),
            &mut len,
        ) != 0
            || error != 0
        {
            return Err(());
        }
    }

    Ok(f)
}

pub fn unix(path: &[u8]) -> Result<sockaddr_un> {
    unsafe {
        let mut a: sockaddr_un = mem::zeroed();

        a.sun_family = AF_UNIX as u16;

        if path.len() >= a.sun_path.len() || path.contains(&0) {
            return Err(());
        }

        for (dst, src) in a.sun_path.iter_mut().zip(path) {
            *dst = *src as c_char;
        }

        Ok(a)
    }
}

pub fn ipv4(s: &[u8]) -> Result<[u8; 4]> {
    let mut a = [0; 4];
    let mut parts = s.split(|c| *c == b'.');

    for v in &mut a {
        let p = parts.next().ok_or(())?;

        if p.is_empty() || p.len() > 3 || (p.len() > 1 && p[0] == b'0') {
            return Err(());
        }

        *v = decimal(p)?.try_into().map_err(|_| ())?;
    }

    if parts.next().is_some() {
        return Err(());
    }

    Ok(a)
}

pub fn decimal(s: &[u8]) -> Result<u32> {
    if s.is_empty() {
        return Err(());
    }

    let mut n = 0u32;
    for &c in s {
        if !c.is_ascii_digit() {
            return Err(());
        }

        n = n
            .checked_mul(10)
            .and_then(|x| x.checked_add((c - b'0') as u32))
            .ok_or(())?;
    }

    Ok(n)
}

pub fn metadata(path: &[u8]) -> Result<stat> {
    unsafe {
        let mut s = mem::zeroed();

        if lstat(c_path(path)?.as_ptr(), &mut s) != 0 {
            return Err(());
        }

        Ok(s)
    }
}

pub fn regular(s: &stat) -> bool {
    s.st_mode & S_IFMT == S_IFREG && s.st_uid == unsafe { geteuid() } && s.st_mode & 0o077 == 0
}

pub fn directory(path: &[u8]) -> Result<()> {
    unsafe {
        if mkdir(c_path(path)?.as_ptr(), 0o700) != 0 && errno() != EEXIST {
            return Err(());
        }
    }

    let s = metadata(path)?;
    if s.st_mode & S_IFMT != S_IFDIR || s.st_uid != unsafe { geteuid() } || s.st_mode & 0o077 != 0 {
        return Err(());
    }

    Ok(())
}

pub fn read_private(path: &[u8], out: &mut Vec<u8>) -> Result<stat> {
    use std::{
        fs::OpenOptions,
        io::Read,
        os::{
            fd::AsRawFd,
            unix::{ffi::OsStrExt, fs::OpenOptionsExt},
        },
    };
    let path = std::ffi::OsStr::from_bytes(path);
    let mut file = OpenOptions::new()
        .read(true)
        .custom_flags(O_NOFOLLOW | O_NONBLOCK)
        .open(path)
        .map_err(|_| ())?;
    let mut metadata = unsafe { mem::zeroed() };
    if unsafe { fstat(file.as_raw_fd(), &mut metadata) } != 0 || !regular(&metadata) {
        return Err(());
    }
    out.clear();
    file.read_to_end(out).map_err(|_| ())?;
    Ok(metadata)
}

pub async fn changed(path: &[u8], initial: &stat) -> bool {
    let path = path.to_vec();
    let initial = *initial;

    storage(move || {
        Ok(match metadata(&path) {
            Ok(s) => {
                s.st_ino != initial.st_ino
                    || s.st_mtime != initial.st_mtime
                    || s.st_mtime_nsec != initial.st_mtime_nsec
                    || s.st_size != initial.st_size
            }
            Err(_) => true,
        })
    })
    .await
    .unwrap_or(true)
}

pub fn atomic(path: &[u8], data: &[u8]) -> Result<()> {
    use std::{
        io::Write,
        os::unix::{ffi::OsStrExt, fs::PermissionsExt},
        path::Path,
    };
    let target = Path::new(std::ffi::OsStr::from_bytes(path));
    let parent = target.parent().ok_or(())?;
    let mut temporary = tempfile::NamedTempFile::new_in(parent).map_err(|_| ())?;
    temporary
        .as_file()
        .set_permissions(std::fs::Permissions::from_mode(0o600))
        .map_err(|_| ())?;
    temporary.write_all(data).map_err(|_| ())?;
    temporary.as_file().sync_all().map_err(|_| ())?;
    temporary.persist(target).map_err(|_| ())?;
    std::fs::File::open(parent)
        .and_then(|directory| directory.sync_all())
        .map_err(|_| ())
}

pub fn unlink_path(path: &[u8]) {
    if let Ok(path) = c_path(path) {
        unsafe {
            unlink(path.as_ptr());
        }
    }
}

/// Four requests bound the queue; the runtime has one blocking worker.
pub async fn storage<T: Send + 'static>(
    f: impl FnOnce() -> Result<T> + Send + 'static,
) -> Result<T> {
    static LIMIT: std::sync::OnceLock<std::sync::Arc<tokio::sync::Semaphore>> =
        std::sync::OnceLock::new();

    let permit = LIMIT
        .get_or_init(|| std::sync::Arc::new(tokio::sync::Semaphore::new(4)))
        .clone()
        .acquire_owned()
        .await
        .map_err(|_| ())?;

    tokio::task::spawn_blocking(move || {
        let _permit = permit;
        f()
    })
    .await
    .map_err(|_| ())?
}
