mod bridge;
mod camera;
mod config;
mod json;
mod mqtt;
mod router;
mod settings;
mod store;
mod sys;
mod time_config;

fn main() {
    use std::os::unix::ffi::OsStrExt;
    let mut arguments = std::env::args_os();
    if arguments.next().is_some_and(|name| {
        name.as_bytes().rsplit(|byte| *byte == b'/').next() == Some(b"local-ding-time-config")
    }) {
        let result = match (arguments.next(), arguments.next()) {
            (Some(path), None) => time_config::print(&path),
            _ => Err(()),
        };
        std::process::exit(if result.is_ok() { 0 } else { 1 });
    }

    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .max_blocking_threads(1)
        .build()
        .unwrap();
    let local = tokio::task::LocalSet::new();
    let code = runtime.block_on(local.run_until(run()));
    drop(local);
    runtime.shutdown_timeout(std::time::Duration::from_secs(5));
    std::process::exit(code);
}

/// Run one service until it exits or receives a shutdown signal.
async fn run() -> libc::c_int {
    use std::os::unix::ffi::OsStrExt;

    let mut arguments = std::env::args_os();
    let executable = arguments.next().expect("executable name");
    let config_path = arguments.next();
    let config_path = if arguments.next().is_some() {
        None
    } else {
        config_path.as_ref().map(|path| path.as_bytes())
    };

    sys::signals();
    tokio::select! {
        code = dispatch(executable.as_bytes(), config_path) => code,
        () = wait_for_shutdown() => 0,
    }
}

async fn wait_for_shutdown() {
    while !sys::stopped() {
        tokio::time::sleep(std::time::Duration::from_millis(25)).await;
    }
}

async fn dispatch(name: &[u8], config: Option<&[u8]>) -> libc::c_int {
    let name = name.rsplit(|b| *b == b'/').next().unwrap_or(name);

    let result = if name == b"local-ding-settings" {
        settings::run().await
    } else if name == b"local-ding-router" {
        router::run().await
    } else if let Some(config) = config {
        let path = config.to_vec();

        let config_path = path.clone();

        if sys::storage(move || config::Config::load(&config_path))
            .await
            .is_err()
        {
            return 2;
        }

        bridge::run(&path).await
    } else {
        sys::log("Usage: local-ding CONFIG_FILE\n");
        return 2;
    };

    if result.is_ok() {
        0
    } else {
        sys::log("Local service stopped: configuration, storage or I/O error\n");
        1
    }
}
